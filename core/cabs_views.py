import io
from decimal import Decimal, ROUND_CEILING
from datetime import datetime, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test, login_required
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.template.loader import get_template
from django.core.mail import send_mail
from django.conf import settings
from django_ratelimit.decorators import ratelimit
from xhtml2pdf import pisa
from django.conf import settings
from urllib.parse import quote
import qrcode
import io
import base64
from django.db import IntegrityError, transaction
import logging
from .models import CabCategory, CabRoutePackage, CabBooking, CabReview, Coupon, CabPartner, PartnerWalletTransaction,Welcome10Redemption, Booking
from .views import is_admin, _to_int, _normalize_phone

logger = logging.getLogger(__name__)

CAB_GST_RATE = Decimal('0.05')
NIGHT_CHARGE = Decimal('200')
WAITING_CHARGE_PER_HOUR = Decimal('100')


def calculate_cab_fare(category, trip_type, distance_km, pickup_time, waiting_hours=0, coupon=None):
    distance_km = Decimal(str(distance_km or 0))
    waiting_hours = int(waiting_hours or 0)

    rate = Decimal(str(category.base_per_km_rate))
    allowance = Decimal(str(category.driver_allowance_per_day))
    min_km = Decimal(str(category.min_km_per_day))

    if min_km <= 0 or distance_km < 0 or waiting_hours < 0:
        raise ValueError("Invalid fare calculation inputs.")

    if trip_type == 'ONE_WAY':
        base_fare = (distance_km * rate) + allowance

    elif trip_type == 'AIRPORT':
        base_fare = (Decimal('25') * rate) + allowance

    elif trip_type == 'ROUND_TRIP':
        days = max(
            1,
            int((distance_km / min_km).to_integral_value(
                rounding=ROUND_CEILING
            ))
        )
        base_fare = (min_km * days * rate) + (allowance * days)

    elif trip_type == 'HOURLY':
        hours = max(
            4,
            int((distance_km / Decimal('40')).to_integral_value(
                rounding=ROUND_CEILING
            ))
        )
        base_fare = (Decimal(hours) * Decimal('250')) + allowance

    else:
        raise ValueError("Unsupported trip type.")

    base_fare = base_fare.quantize(Decimal('0.01'))

    is_night = pickup_time.hour >= 22 or pickup_time.hour < 6
    night_charge = NIGHT_CHARGE if is_night else Decimal('0.00')

    waiting_charge = (
        Decimal(waiting_hours) * WAITING_CHARGE_PER_HOUR
    ).quantize(Decimal('0.01'))

    state_tax = (
        Decimal('500.00')
        if trip_type in ('ONE_WAY', 'ROUND_TRIP') and distance_km > 200
        else Decimal('0.00')
    )

    subtotal = base_fare + night_charge + waiting_charge + state_tax

    discount = Decimal('0.00')

    if coupon:
        is_valid, _ = coupon.is_valid()
        if is_valid:
            discount, _ = coupon.calculate_discount(subtotal)

    taxable = max(Decimal('0.00'), subtotal - discount)
    gst = (taxable * CAB_GST_RATE).quantize(Decimal('0.01'))
    total = (taxable + gst).quantize(Decimal('0.01'))

    return {
        'base_fare': base_fare,
        'night_charge': night_charge,
        'waiting_charge': waiting_charge,
        'state_tax': state_tax,
        'subtotal': subtotal,
        'discount': discount,
        'taxable': taxable,
        'gst': gst,
        'total': total,
    }

def cab_list(request):
    categories = CabCategory.objects.filter(is_active=True).order_by('seating_capacity')
    popular_routes = CabRoutePackage.objects.filter(
        is_active=True
    ).exclude(trip_type='HOURLY').select_related('category').order_by('from_city', 'fixed_price')[:12]
    hourly_packages = CabRoutePackage.objects.filter(
        trip_type='HOURLY', is_active=True
    ).select_related('category').order_by('from_city', 'duration_hours')
    return render(request, 'cabs/cab_list.html', {
        'categories': categories,
        'popular_routes': popular_routes,
        'hourly_packages': hourly_packages,
    })

def cab_search(request):
    if request.method != 'POST':
        return redirect('cab_list')

    # -----------------------------------------
    # 1. GET SEARCH INPUTS
    # -----------------------------------------
    trip_type = request.POST.get('trip_type', 'ONE_WAY').strip().upper()
    valid_trip_types = {'ONE_WAY', 'ROUND_TRIP', 'AIRPORT', 'HOURLY'}

    if trip_type not in valid_trip_types:
        messages.error(request, "Invalid trip type selected.")
        return redirect('cab_list')

    from_city = request.POST.get('from_city', '').strip()
    to_city = request.POST.get('to_city', '').strip()
    pickup_date_str = request.POST.get('pickup_date', '').strip()
    pickup_time_str = request.POST.get('pickup_time', '10:00').strip() or '10:00'
    
    distance_km = _to_int(request.POST.get('distance_km'), 0, 0, 5000)
    
    category_id_str = request.POST.get('category_id', '').strip()
    coupon_code = request.POST.get('coupon_code', '').strip().upper()
    hourly_package_id = request.POST.get('hourly_package_id', '').strip()

    # -----------------------------------------
    # 2. BASIC VALIDATION
    # -----------------------------------------
    if not from_city or not pickup_date_str:
        messages.error(request, "Please enter a pickup city and pickup date.")
        return redirect('cab_list')

    # Enforce destination for point-to-point trips
    if trip_type in ('ONE_WAY', 'ROUND_TRIP') and not to_city:
        messages.error(request, "Please enter a destination city for this trip type.")
        return redirect('cab_list')

    if trip_type in ('ONE_WAY', 'ROUND_TRIP') and to_city and from_city.casefold() == to_city.casefold():
        messages.error(request, "Pickup and destination cities cannot be the same.")
        return redirect('cab_list')

    # Validate category ID
    category_id = None
    if category_id_str:
        try:
            category_id = int(category_id_str)
        except (ValueError, TypeError):
            messages.error(request, "Invalid cab category selected.")
            return redirect('cab_list')

    # -----------------------------------------
    # 3. DATE AND TIME VALIDATION
    # -----------------------------------------
    try:
        pickup_date = datetime.strptime(pickup_date_str, '%Y-%m-%d').date()
        pickup_time = datetime.strptime(pickup_time_str, '%H:%M').time()
    except (ValueError, TypeError):
        messages.error(request, "Invalid pickup date or time format.")
        return redirect('cab_list')

    today = timezone.localdate()

    if pickup_date < today:
        messages.error(request, "Pickup date cannot be in the past.")
        return redirect('cab_list')

    # Prevent past pickup time on today's date
    if pickup_date == today:
        current_time = timezone.localtime(timezone.now()) if timezone.is_aware(timezone.now()) else timezone.now()
        current_clock = current_time.time().replace(second=0, microsecond=0)

        if pickup_time <= current_clock:
            messages.error(request, "Pickup time must be in the future.")
            return redirect('cab_list')

    # -----------------------------------------
    # 4. LOAD & VALIDATE HOURLY PACKAGES
    # -----------------------------------------
    selected_package = None

    if hourly_package_id and trip_type == 'HOURLY':
        try:
            package_pk = int(hourly_package_id)
        except (ValueError, TypeError):
            messages.error(request, "Invalid hourly package selected.")
            return redirect('cab_list')

        selected_package = CabRoutePackage.objects.filter(
            id=package_pk,
            trip_type='HOURLY',
            is_active=True,
            category__is_active=True
        ).select_related('category').first()

        if not selected_package:
            messages.error(request, "Selected hourly package is unavailable. Please choose another.")
            return redirect('cab_list')

        # Prevent conflicting cab category selection
        if category_id and category_id != selected_package.category_id:
            messages.error(request, "Selected hourly package belongs to a different cab category.")
            return redirect('cab_list')

    # -----------------------------------------
    # 5. LOAD ACTIVE CAB CATEGORIES
    # -----------------------------------------
    categories = CabCategory.objects.filter(is_active=True)

    if category_id:
        categories = categories.filter(id=category_id)

    # Hourly package strictly determines its own category
    if selected_package:
        categories = categories.filter(id=selected_package.category_id)

    # -----------------------------------------
    # 6. VALIDATE COUPON
    # -----------------------------------------
    coupon_obj = None
    if coupon_code:
        coupon_obj = Coupon.objects.filter(code__iexact=coupon_code, is_active=True).first()

    # -----------------------------------------
    # 7. CALCULATE CAB FARES
    # -----------------------------------------
    cabs_with_fare = []
    gst_rate = Decimal(str(CAB_GST_RATE)) # Ensure it's a Decimal for safe math

    for cat in categories:
        if selected_package:
            # Category is already filtered to match the package, so this is guaranteed
            package_price = Decimal(str(selected_package.fixed_price))
            gst_amount = (package_price * gst_rate).quantize(Decimal('0.01'))
            
            fare = {
                'base_fare': package_price,
                'night_charge': Decimal('0.00'),
                'waiting_charge': Decimal('0.00'),
                'state_tax': Decimal('0.00'),
                'subtotal': package_price,
                'discount': Decimal('0.00'),
                'taxable': package_price,
                'gst': gst_amount,
                'total': (package_price + gst_amount).quantize(Decimal('0.01')),
            }
        else:
            # Normal fare calculation (adjust the '0' arg if your function expects waiting_time/extra)
            fare = calculate_cab_fare(cat, trip_type, distance_km, pickup_time, 0, coupon_obj)

        cabs_with_fare.append({
            'category': cat,
            **fare,
            'package': selected_package,
        })

    # -----------------------------------------
    # 8. SEARCH RESULTS
    # -----------------------------------------
    # Pre-fetch hourly packages for the results page (e.g., for a "Modify Search" sidebar)
    hourly_packages = CabRoutePackage.objects.filter(
        trip_type='HOURLY',
        is_active=True
    ).select_related('category').order_by('from_city', 'duration_hours')

    return render(
        request,
        'cabs/cab_search_results.html',
        {
            'cabs': cabs_with_fare,
            'trip_type': trip_type,
            'from_city': from_city,
            'to_city': to_city,
            'pickup_date': pickup_date,
            'pickup_time': pickup_time,
            'distance_km': distance_km,
            'coupon_code': coupon_code,
            'coupon_valid': coupon_obj is not None,
            'hourly_packages': hourly_packages,
            'selected_package': selected_package,
        }
    )

@ratelimit(key='ip', rate='10/h', method='POST', block=True)
def book_cab(request):
    if request.method != 'POST':
        return redirect('cab_list')

    category_id = _to_int(request.POST.get('category_id'), 0, 1, 1000000)
    trip_type = request.POST.get('trip_type', 'ONE_WAY').strip().upper()
    from_city = request.POST.get('from_city', '').strip()
    to_city = request.POST.get('to_city', '').strip()
    pickup_address = request.POST.get('pickup_address', '').strip()
    pickup_date_str = request.POST.get('pickup_date', '').strip()
    pickup_time_str = request.POST.get('pickup_time', '10:00').strip()
    distance_km = _to_int(request.POST.get('distance_km'), 0, 0, 5000)
    waiting_hours = _to_int(request.POST.get('waiting_hours'), 0, 0, 24)
    hourly_package_id = request.POST.get('hourly_package_id', '').strip()
    coupon_code = request.POST.get('coupon_code', '').strip()

    guest_name = request.POST.get('guest_name', '').strip()
    guest_phone = request.POST.get('guest_phone', '').strip()
    guest_email = request.POST.get('guest_email', '').strip()

    valid_trip_types = ['ONE_WAY', 'ROUND_TRIP', 'AIRPORT', 'HOURLY']

    if trip_type not in valid_trip_types:
        messages.error(request, "Invalid trip type selected.")
        return redirect('cab_list')

    if not all([category_id, from_city, pickup_address, guest_name, guest_phone, pickup_date_str]):
        messages.error(request, "Please fill all required fields.")
        return redirect('cab_list')

    if trip_type in ['ONE_WAY', 'ROUND_TRIP'] and not to_city:
        messages.error(request, "Please enter destination city.")
        return redirect('cab_list')

    if trip_type in ['ONE_WAY', 'ROUND_TRIP'] and from_city.lower() == to_city.lower():
        messages.error(request, "Pickup and destination cities cannot be the same.")
        return redirect('cab_list')

    if trip_type in ['ONE_WAY', 'ROUND_TRIP'] and distance_km <= 0:
        messages.error(request, "Valid journey distance is required.")
        return redirect('cab_list')

    normalized_phone = _normalize_phone(guest_phone)
    if len(normalized_phone) < 10:
        messages.error(request, "Please enter a valid 10-digit phone number.")
        return redirect('cab_list')

    try:
        pickup_date = datetime.strptime(pickup_date_str, '%Y-%m-%d').date()
        pickup_time = datetime.strptime(pickup_time_str, '%H:%M').time()
        pickup_datetime = timezone.make_aware(
            datetime.combine(pickup_date, pickup_time)
        )
    except (ValueError, TypeError):
        messages.error(request, "Invalid pickup date or time.")
        return redirect('cab_list')

    if pickup_datetime < timezone.now():
        messages.error(request, "Pickup date and time cannot be in the past.")
        return redirect('cab_list')

    category = get_object_or_404(
        CabCategory,
        id=category_id,
        is_active=True
    )

    selected_package = None

    if trip_type == 'HOURLY':
        if not hourly_package_id:
            messages.error(request, "Please select an hourly package.")
            return redirect('cab_list')

        selected_package = CabRoutePackage.objects.filter(
            id=hourly_package_id,
            trip_type='HOURLY',
            is_active=True
        ).first()

        if not selected_package:
            messages.error(request, "Selected hourly package is unavailable.")
            return redirect('cab_list')

        if selected_package.category_id != category.id:
            messages.error(request, "Selected package does not match the cab category.")
            return redirect('cab_list')

        if selected_package.fixed_price <= 0:
            messages.error(request, "Invalid hourly package price.")
            return redirect('cab_list')

        if coupon_code:
            messages.error(request, "Coupon codes are not available for this fixed-price hourly package.")
            return redirect('cab_list')

        coupon_obj = None

    if coupon_code and trip_type != 'HOURLY':
        coupon = Coupon.objects.filter(
            code__iexact=coupon_code,
            is_active=True
        ).first()

        if not coupon or not coupon.is_valid()[0]:
            messages.error(request, "Invalid or expired coupon code.")
            return redirect('cab_list')

        if coupon_code.upper() == 'WELCOME10':
            guest_email = guest_email.strip().lower()

            already_used = (
                Welcome10Redemption.objects.filter(
                    email__iexact=guest_email
                ).exists()
                or Booking.objects.filter(
                    guest_email__iexact=guest_email,
                    status='CONFIRMED'
                ).exists()
                or CabBooking.objects.filter(
                    guest_email__iexact=guest_email,
                    advance_paid=True,
                    status='CONFIRMED'
                ).exists()
            )

            if already_used:
                messages.error(
                    request,
                    "WELCOME10 is valid only for your first successful booking."
                )
                return redirect('cab_list')

            if not (
                coupon.discount_type == 'percentage'
                and coupon.discount_value == Decimal('10.00')
            ):
                messages.error(
                    request,
                    "WELCOME10 coupon configuration is incorrect."
                )
                return redirect('cab_list')

        coupon_obj = coupon

    try:
        if selected_package:
            package_price = Decimal(str(selected_package.fixed_price)).quantize(
                Decimal('0.01')
            )
            gst = (package_price * CAB_GST_RATE).quantize(
                Decimal('0.01')
            )

            fare = {
                'base_fare': package_price,
                'night_charge': Decimal('0.00'),
                'waiting_charge': Decimal('0.00'),
                'state_tax': Decimal('0.00'),
                'subtotal': package_price,
                'discount': Decimal('0.00'),
                'taxable': package_price,
                'gst': gst,
                'total': package_price + gst,
            }
        else:
            fare = calculate_cab_fare(
                category,
                trip_type,
                distance_km,
                pickup_time,
                waiting_hours,
                coupon_obj
            )

        total = Decimal(str(fare['total'])).quantize(Decimal('0.01'))
        if total <= 0:
            messages.error(request, "Unable to calculate a valid booking fare.")
            return redirect('cab_list')

        advance = (total * Decimal('0.20')).quantize(Decimal('0.01'))
        balance = total - advance

        with transaction.atomic():
            booking = CabBooking.objects.create(
                category=category,
                route_package=selected_package,
                user=request.user if request.user.is_authenticated else None,
                coupon=coupon_obj,
                trip_type=trip_type,
                pickup_city=from_city,
                drop_city=to_city,
                pickup_address=pickup_address,
                pickup_datetime=pickup_datetime,
                distance_km=distance_km,
                waiting_hours=waiting_hours,
                guest_name=guest_name,
                guest_phone=normalized_phone,
                guest_email=guest_email,
                base_fare=fare['base_fare'],
                driver_allowance=category.driver_allowance_per_day,
                toll_charges=Decimal('0.00'),
                night_charge=fare['night_charge'],
                state_tax=fare['state_tax'],
                waiting_charges=fare.get('waiting_charge', Decimal('0.00')),
                gst_amount=fare['gst'],
                discount_amount=fare['discount'],
                total_amount=total,
                advance_amount=advance,
                balance_amount=balance,
                payment_mode='PAY_TO_DRIVER',
                payment_status='PENDING',
                status='PENDING',
            )

    except Exception:
        logger.exception("Cab booking creation failed.")
        messages.error(request, "Booking failed. Please try again.")
        return redirect('cab_list')

    if booking.guest_email:
        try:
            send_mail(
                f"Cab Booking Received — {booking.booking_id}",
                (
                    f"Dear {booking.guest_name},\n\n"
                    f"Your cab booking request has been received.\n"
                    f"Booking ID: {booking.booking_id}\n"
                    f"Pickup: {booking.pickup_address}, {booking.pickup_city}\n"
                    f"Destination: {booking.drop_city or 'As selected'}\n"
                    f"Date: {booking.pickup_datetime.strftime('%d %b %Y, %I:%M %p')}\n"
                    f"Total Fare: Rs. {booking.total_amount}\n"
                    f"Advance Required: Rs. {booking.advance_amount}\n\n"
                    f"Your booking will be confirmed after advance payment verification.\n\n"
                    f"— Makes My Yatra"
                ),
                settings.DEFAULT_FROM_EMAIL,
                [booking.guest_email],
                fail_silently=True,
            )
        except Exception as e:
            logger.warning(
                "Cab email failed for %s: %s",
                booking.booking_id,
                type(e).__name__
            )

    messages.info(
        request,
        f"Booking created! Please pay ₹{booking.advance_amount} advance to proceed."
    )
    return redirect('cab_pay_advance', booking_id=booking.booking_id)

def cab_booking_detail(request, booking_id):
    booking = get_object_or_404(CabBooking.objects.select_related('category', 'coupon'), booking_id=booking_id)
    return render(request, 'cabs/cab_booking_detail.html', {'booking': booking})


def track_cab_booking(request):
    booking = None
    query_id = (request.POST.get('booking_id') or request.GET.get('booking_id') or '').strip()
    contact_info = (request.POST.get('contact_info') or request.GET.get('contact_info') or '').strip()

    if query_id:
        clean_id = query_id.replace('#', '').strip()
        found = CabBooking.objects.select_related('category').filter(booking_id__iexact=clean_id).first()
        if found:
            u = request.user
            allowed = (
                (u.is_authenticated and (u.is_staff or u.is_superuser))
                or _normalize_phone(contact_info) == _normalize_phone(found.guest_phone)
                or (contact_info and '@' in contact_info and contact_info.lower() == (found.guest_email or '').lower())
            )
            if allowed:
                booking = found
            else:
                messages.error(request, "Please enter the phone/email used for this booking.")
        else:
            messages.error(request, "No cab booking found with this ID.")

    return render(request, 'cabs/track_cab.html', {'booking': booking, 'query_id': query_id, 'contact_info': contact_info})


def cancel_cab_booking(request, booking_id):
    booking = get_object_or_404(CabBooking, booking_id=booking_id)

    if booking.status in ('ONGOING', 'COMPLETED', 'CANCELLED', 'CANCELLED_BY_GUEST', 'CANCELLED_BY_ADMIN'):
        messages.error(request, "This booking cannot be cancelled.")
        return redirect('cab_booking_detail', booking_id=booking.booking_id)

    if request.method == 'POST':
        reason = request.POST.get('cancellation_reason', 'Cancelled by guest').strip()
        hours_left = (booking.pickup_datetime - timezone.now()).total_seconds() / 3600
        total = booking.total_amount

        if hours_left >= 24:
            refund = total
            note = "Full refund (>24 hrs)"
        elif hours_left >= 6:
            refund = (total * Decimal('0.50')).quantize(Decimal('0.01'))
            note = "50% refund (6-24 hrs)"
        else:
            refund = Decimal('0.00')
            note = "No refund (<6 hrs)"

        penalty = (total - refund).quantize(Decimal('0.01'))
        booking.status = 'CANCELLED_BY_GUEST'
        booking.cancelled_by = 'GUEST'
        booking.cancelled_at = timezone.now()
        booking.cancellation_reason = reason
        booking.cancellation_penalty = penalty
        booking.save()

        messages.success(request, f"Booking cancelled. Refund: ₹{refund} ({note})")
        return redirect('cab_booking_detail', booking_id=booking.booking_id)

    return render(request, 'cabs/cancel_cab.html', {'booking': booking})


@require_POST
def submit_cab_review(request, booking_id):
    booking = get_object_or_404(CabBooking, booking_id=booking_id)
    if booking.status != 'COMPLETED':
        messages.error(request, "Only completed trips can be reviewed.")
        return redirect('cab_booking_detail', booking_id=booking.booking_id)
    if hasattr(booking, 'review'):
        messages.warning(request, "Review already submitted.")
        return redirect('cab_booking_detail', booking_id=booking.booking_id)

    rating = _to_int(request.POST.get('rating'), 5, 1, 5)
    comment = request.POST.get('comment', '').strip()
    CabReview.objects.create(booking=booking, guest_name=booking.guest_name, rating=rating, comment=comment, is_approved=True)
    messages.success(request, "Thank you for your review!")
    return redirect('cab_booking_detail', booking_id=booking.booking_id)


def cab_invoice_pdf(request, booking_id):
    booking = get_object_or_404(CabBooking.objects.select_related('category', 'coupon'), booking_id=booking_id)
    template = get_template('cabs/cab_invoice_pdf.html')
    html = template.render({'booking': booking})
    result = io.BytesIO()
    pdf = pisa.pisaDocument(io.BytesIO(html.encode('UTF-8')), result)
    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="CabInvoice_{booking.booking_id}.pdf"'
        return response
    return HttpResponse('PDF generation failed', status=500)


# ============================================================
# ADMIN VIEWS
# ============================================================

@user_passes_test(is_admin, login_url='portal_login')
def portal_cabs(request):
    categories = CabCategory.objects.annotate(bookings_count=Count('bookings')).order_by('-created_at')
    return render(request, 'custom_admin/cabs.html', {'categories': categories})


@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_add(request):
    if request.method == 'POST':
        try:
            CabCategory.objects.create(
                name=request.POST.get('name', '').strip(),
                seating_capacity=_to_int(request.POST.get('seating_capacity'), 4, 1, 20),
                luggage_capacity=_to_int(request.POST.get('luggage_capacity'), 2, 0, 20),
                ac_available='ac_available' in request.POST,
                base_per_km_rate=Decimal(request.POST.get('base_per_km_rate', '12')),
                driver_allowance_per_day=Decimal(request.POST.get('driver_allowance_per_day', '300')),
                min_km_per_day=_to_int(request.POST.get('min_km_per_day'), 250, 50, 2000),
                is_active='is_active' in request.POST,
            )
            messages.success(request, "Cab category added!")
        except Exception as e:
            messages.error(request, f"Failed: {e}")
        return redirect('portal_cabs')
    return render(request, 'custom_admin/cab_add.html')


@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_edit(request, cab_id):
    category = get_object_or_404(CabCategory, id=cab_id)
    if request.method == 'POST':
        category.name = request.POST.get('name', category.name).strip()
        category.seating_capacity = _to_int(request.POST.get('seating_capacity'), category.seating_capacity, 1, 20)
        category.luggage_capacity = _to_int(request.POST.get('luggage_capacity'), category.luggage_capacity, 0, 20)
        category.ac_available = 'ac_available' in request.POST
        category.base_per_km_rate = Decimal(request.POST.get('base_per_km_rate', str(category.base_per_km_rate)))
        category.driver_allowance_per_day = Decimal(request.POST.get('driver_allowance_per_day', str(category.driver_allowance_per_day)))
        category.min_km_per_day = _to_int(request.POST.get('min_km_per_day'), category.min_km_per_day, 50, 2000)
        category.is_active = 'is_active' in request.POST
        category.save()
        messages.success(request, "Updated!")
        return redirect('portal_cabs')
    return render(request, 'custom_admin/cab_add.html', {'category': category, 'is_edit': True})


@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_delete(request, cab_id):
    category = get_object_or_404(CabCategory, id=cab_id)
    name = category.name
    category.delete()
    messages.success(request, f"'{name}' deleted.")
    return redirect('portal_cabs')


@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_bookings(request):
    status_filter = request.GET.get('status', 'ALL')
    search_q = request.GET.get('q', '').strip()
    bookings = CabBooking.objects.select_related('category').order_by('-created_at')
    if status_filter and status_filter != 'ALL':
        bookings = bookings.filter(status=status_filter)
    if search_q:
        bookings = bookings.filter(
            Q(booking_id__icontains=search_q) | Q(guest_name__icontains=search_q) |
            Q(guest_phone__icontains=search_q) | Q(pickup_city__icontains=search_q)
        )
    return render(request, 'custom_admin/cab_bookings.html', {'bookings': bookings, 'selected_status': status_filter, 'search_q': search_q})


@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_booking_detail(request, booking_id):
    booking = get_object_or_404(CabBooking.objects.select_related('category', 'coupon'), booking_id=booking_id)
    return render(request, 'custom_admin/cab_booking_detail.html', {'booking': booking})

@require_POST
@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_booking_update(request, booking_id):
    with transaction.atomic():
        booking = get_object_or_404(
            CabBooking.objects.select_for_update(),
            booking_id=booking_id
        )

        new_status = request.POST.get('status', booking.status).strip()

        # Admin cannot complete or mark no-show through this general form.
        allowed_transitions = {
            'PENDING': ['PENDING', 'CONFIRMED'],
            'CONFIRMED': ['CONFIRMED', 'ASSIGNED'],
            'ASSIGNED': ['ASSIGNED', 'ONGOING'],
            'ONGOING': ['ONGOING'],
        }

        allowed_statuses = allowed_transitions.get(booking.status, [])

        if new_status not in allowed_statuses:
            messages.error(
                request,
                "Invalid status change. Use the correct booking action."
            )
            return redirect(
                'portal_cab_booking_detail',
                booking_id=booking.booking_id
            )

        driver_name = request.POST.get(
            'driver_name',
            booking.driver_name or ''
        ).strip()

        driver_phone = request.POST.get(
            'driver_phone',
            booking.driver_phone or ''
        ).strip()

        vehicle_number = request.POST.get(
            'vehicle_number',
            booking.vehicle_number or ''
        ).strip().upper()

        if new_status in ('ASSIGNED', 'ONGOING'):
            has_partner = booking.assigned_partner_id is not None
            has_driver_details = bool(driver_name and driver_phone)

            if not has_partner and not has_driver_details:
                messages.error(
                    request,
                    "Add driver name and phone or assign a partner first."
                )
                return redirect(
                    'portal_cab_booking_detail',
                    booking_id=booking.booking_id
                )

        booking.status = new_status
        booking.driver_name = driver_name
        booking.driver_phone = driver_phone
        booking.vehicle_number = vehicle_number

        # Never mark payment PAID merely because a trip is completed.
        # Payment must be verified separately.

        booking.save(update_fields=[
            'status',
            'driver_name',
            'driver_phone',
            'vehicle_number',
        ])

    messages.success(
        request,
        f"Booking {booking.booking_id} updated successfully."
    )

    return redirect(
        'portal_cab_booking_detail',
        booking_id=booking.booking_id
    )


@require_POST
@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_cancel(request, booking_id):
    with transaction.atomic():
        booking = get_object_or_404(
            CabBooking.objects.select_for_update(),
            booking_id=booking_id
        )

        terminal_statuses = (
            'COMPLETED',
            'CANCELLED',
            'CANCELLED_BY_ADMIN',
            'CANCELLED_BY_GUEST',
            'NO_SHOW',
        )

        if booking.status in terminal_statuses:
            messages.warning(
                request,
                "This booking cannot be cancelled in its current status."
            )
            return redirect(
                'portal_cab_booking_detail',
                booking_id=booking.booking_id
            )

        reason = request.POST.get(
            'cancellation_reason',
            'Cancelled by admin'
        ).strip()

        if not reason:
            reason = 'Cancelled by admin'

        booking.status = 'CANCELLED_BY_ADMIN'
        booking.cancelled_by = 'ADMIN'
        booking.cancelled_at = timezone.now()
        booking.cancellation_reason = reason

        # Do not automatically mark a payment as refunded.
        # Any refund must be verified and recorded separately.

        booking.save(update_fields=[
            'status',
            'cancelled_by',
            'cancelled_at',
            'cancellation_reason',
        ])

    messages.success(
        request,
        f"Booking {booking.booking_id} cancelled successfully. "
        "Any applicable refund must be processed separately."
    )

    return redirect(
        'portal_cab_booking_detail',
        booking_id=booking.booking_id
    )

@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_revenue(request):
    today = timezone.localdate()
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else today - timedelta(days=30)
    except ValueError:
        start_date = today - timedelta(days=30)
    try:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else today
    except ValueError:
        end_date = today

    valid = ['CONFIRMED', 'ASSIGNED', 'ONGOING', 'COMPLETED']
    base = CabBooking.objects.filter(created_at__date__gte=start_date, created_at__date__lte=end_date, status__in=valid)
    kpi = base.aggregate(total_bookings=Count('id'), total_revenue=Sum('total_amount'), total_gst=Sum('gst_amount'))
    category_summary = base.values('category__name').annotate(bookings=Count('id'), revenue=Sum('total_amount')).order_by('-revenue')

    return render(request, 'custom_admin/cab_revenue.html', {
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'kpi': {
            'total_bookings': kpi['total_bookings'] or 0,
            'total_revenue': kpi['total_revenue'] or Decimal('0.00'),
            'total_gst': kpi['total_gst'] or Decimal('0.00'),
        },
        'category_summary': category_summary,
    })


@require_POST
@ratelimit(key='ip', rate='20/m', method='POST', block=True)
def apply_cab_coupon_ajax(request):
    code = request.POST.get('coupon_code', '').strip()
    if not code:
        return JsonResponse({'success': False, 'message': 'Enter a code.'}, status=400)
    coupon = Coupon.objects.filter(code__iexact=code, is_active=True).first()
    if not coupon:
        return JsonResponse({'success': False, 'message': 'Invalid coupon.'}, status=400)
    is_valid, msg = coupon.is_valid()
    if not is_valid:
        return JsonResponse({'success': False, 'message': msg}, status=400)
    return JsonResponse({
        'success': True, 'message': f"Coupon '{coupon.code}' applied!",
        'discount_type': coupon.discount_type, 'discount_value': str(coupon.discount_value),
    })

# ============================================================
# PARTNER WALLET SYSTEM VIEWS
# ============================================================

def deduct_commission_from_wallet(booking, partner, admin_user=None):
    """Deduct commission from partner wallet."""
    from .models import PartnerWalletTransaction
    
    commission_rate = partner.commission_percentage / Decimal('100')
    commission = (booking.base_fare * commission_rate).quantize(Decimal('0.01'))
    
    if partner.wallet_balance < commission:
        return False, f"Insufficient wallet. Required: ₹{commission}, Available: ₹{partner.wallet_balance}"
    
    if partner.wallet_balance - commission < partner.min_wallet_required:
        return False, f"Wallet will fall below min ₹{partner.min_wallet_required}"
    
    balance_before = partner.wallet_balance
    partner.wallet_balance -= commission
    partner.save()
    
    PartnerWalletTransaction.objects.create(
        partner=partner, txn_type='COMMISSION', amount=commission,
        balance_before=balance_before, balance_after=partner.wallet_balance,
        booking=booking, notes=f"Commission for {booking.booking_id}",
        created_by=admin_user,
    )
    
    booking.assigned_partner = partner
    booking.commission_deducted = True
    booking.commission_amount = commission
    booking.status = 'ASSIGNED'
    if not booking.driver_name:
        booking.driver_name = partner.name
        booking.driver_phone = partner.phone
    booking.save()
    
    return True, f"✓ Commission ₹{commission} deducted. New balance: ₹{partner.wallet_balance}"


@user_passes_test(is_admin, login_url='portal_login')
def portal_partners(request):
    from django.db.models import F
    partners = CabPartner.objects.annotate(
        bookings_count=Count('assigned_bookings')
    ).order_by('-created_at')
    
    stats = {
        'total_partners': partners.count(),
        'active': partners.filter(status='ACTIVE').count(),
        'low_wallet': partners.filter(wallet_balance__lt=F('min_wallet_required')).count(),
        'total_wallet': partners.aggregate(Sum('wallet_balance'))['wallet_balance__sum'] or 0,
    }
    
    return render(request, 'custom_admin/cab_partners.html', {
        'partners': partners, 'stats': stats,
    })

@user_passes_test(is_admin, login_url='portal_login')
def portal_partner_add(request):
    if request.method == 'POST':
        try:
            import secrets
            from django.contrib.auth.models import User as AuthUser
            
            name = request.POST.get('name', '').strip()
            phone = request.POST.get('phone', '').strip()
            
            # Check if phone already exists
            if CabPartner.objects.filter(phone=phone).exists():
                messages.error(request, f"Partner with phone {phone} already exists.")
                return redirect('portal_partner_add')
            
            # Create partner
            partner = CabPartner.objects.create(
                name=name,
                phone=phone,
                email=request.POST.get('email', '').strip(),
                partner_type=request.POST.get('partner_type', 'INDIVIDUAL'),
                company_name=request.POST.get('company_name', '').strip(),
                city=request.POST.get('city', '').strip(),
                address=request.POST.get('address', '').strip(),
                driving_license=request.POST.get('driving_license', '').strip(),
                commission_percentage=Decimal(request.POST.get('commission_percentage', '10')),
                min_wallet_required=Decimal(request.POST.get('min_wallet_required', '500')),
                status='ACTIVE',
            )
            
            # Create User account for login
            auto_password = secrets.token_urlsafe(8)[:12]
            
            if not AuthUser.objects.filter(username=phone).exists():
                user = AuthUser.objects.create_user(
                    username=phone,
                    password=auto_password,
                    first_name=name,
                )
                partner.user = user
                partner.save()
                
                messages.success(
                    request, 
                    f"✓ Partner added! Login credentials — Phone: {phone}, Password: {auto_password} (Please share with partner)"
                )
            else:
                messages.warning(request, f"Partner added but user account exists. Please reset password manually.")
                
        except Exception as e:
            messages.error(request, f"Failed: {e}")
        return redirect('portal_partners')
    
    return render(request, 'custom_admin/cab_partner_add.html')


@user_passes_test(is_admin, login_url='portal_login')
def portal_partner_detail(request, partner_id):
    partner = get_object_or_404(CabPartner, id=partner_id)
    transactions = partner.wallet_transactions.all()[:30]
    bookings = partner.assigned_bookings.all()[:20]
    
    return render(request, 'custom_admin/cab_partner_detail.html', {
        'partner': partner, 'transactions': transactions, 'bookings': bookings,
    })


@user_passes_test(is_admin, login_url='portal_login')
def portal_partner_recharge(request, partner_id):
    from .models import PartnerWalletTransaction
    partner = get_object_or_404(CabPartner, id=partner_id)
    
    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0'))
            reference = request.POST.get('reference_id', '').strip()
            notes = request.POST.get('notes', '').strip()
            
            if amount <= 0:
                raise ValueError("Amount must be positive")
            
            balance_before = partner.wallet_balance
            partner.wallet_balance += amount
            partner.save()
            
            PartnerWalletTransaction.objects.create(
                partner=partner, txn_type='RECHARGE', amount=amount,
                balance_before=balance_before, balance_after=partner.wallet_balance,
                reference_id=reference, notes=notes or "Wallet recharge",
                created_by=request.user,
            )
            messages.success(request, f"₹{amount} added. New: ₹{partner.wallet_balance}")
        except Exception as e:
            messages.error(request, f"Failed: {e}")
        return redirect('portal_partner_detail', partner_id=partner.id)
    
    return render(request, 'custom_admin/cab_partner_recharge.html', {'partner': partner})


@user_passes_test(is_admin, login_url='portal_login')
def portal_partner_toggle(request, partner_id):
    partner = get_object_or_404(CabPartner, id=partner_id)
    partner.is_available = not partner.is_available
    partner.save()
    status = "available" if partner.is_available else "offline"
    messages.success(request, f"{partner.name} is now {status}.")
    return redirect('portal_partner_detail', partner_id=partner.id)


@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_assign_partner(request, booking_id):
    from django.db.models import F

    if request.method == 'POST':
        with transaction.atomic():
            booking = get_object_or_404(
                CabBooking.objects.select_for_update(),
                booking_id=booking_id
            )

            if booking.status != 'CONFIRMED':
                messages.error(
                    request,
                    "Only confirmed bookings can be assigned."
                )
                return redirect(
                    'portal_cab_booking_detail',
                    booking_id=booking.booking_id
                )

            if not booking.advance_paid:
                messages.error(
                    request,
                    "Advance payment must be verified before assigning a partner."
                )
                return redirect(
                    'portal_cab_booking_detail',
                    booking_id=booking.booking_id
                )

            if booking.assigned_partner_id:
                messages.warning(
                    request,
                    "A partner is already assigned to this booking."
                )
                return redirect(
                    'portal_cab_booking_detail',
                    booking_id=booking.booking_id
                )

            partner_id = request.POST.get('partner_id')

            if not partner_id or not partner_id.isdigit():
                messages.error(request, "Please select a valid partner.")
                return redirect(
                    'portal_cab_assign_partner',
                    booking_id=booking.booking_id
                )

            partner = get_object_or_404(
                CabPartner.objects.select_for_update(),
                id=int(partner_id)
            )

            if not partner.can_accept_booking:
                messages.error(
                    request,
                    "This partner is not currently eligible."
                )
                return redirect(
                    'portal_cab_assign_partner',
                    booking_id=booking.booking_id
                )

            booking.assigned_partner = partner
            booking.status = 'PARTNER_REQUESTED'
            booking.save(update_fields=[
                'assigned_partner',
                'status'
            ])

        messages.success(
            request,
            "Partner request sent successfully. Commission will be settled after trip completion."
        )
        return redirect(
            'portal_cab_booking_detail',
            booking_id=booking.booking_id
        )

    booking = get_object_or_404(
        CabBooking,
        booking_id=booking_id
    )

    eligible = CabPartner.objects.filter(
        status='ACTIVE',
        is_available=True,
        wallet_balance__gte=F('min_wallet_required')
    ).exclude(
        assigned_bookings__status='PARTNER_REQUESTED'
    ).distinct()

    return render(
        request,
        'custom_admin/cab_assign_partner.html',
        {
            'booking': booking,
            'partners': eligible,
        }
    )


@login_required(login_url='partner_cab_login')
def partner_cab_accept_booking(request, booking_id):
    """Partner accepts booking - commission deducted NOW."""
    if not hasattr(request.user, 'cab_partner'):
        return redirect('index')
    
    partner = request.user.cab_partner
    booking = get_object_or_404(
        CabBooking, 
        booking_id=booking_id, 
        assigned_partner=partner,
        status='PARTNER_REQUESTED'
    )
    
    # Mark commission due (not deduct)
    commission_rate = partner.commission_percentage / Decimal('100')
    commission = (booking.base_fare * commission_rate).quantize(Decimal('0.01'))
    
    booking.assigned_partner = partner
    booking.commission_amount = commission
    booking.commission_deducted = True # Mark that commission is due
    booking.status = 'ASSIGNED'
    if not booking.driver_name:
        booking.driver_name = partner.name
        booking.driver_phone = partner.phone
    booking.save()
    
    messages.success(request, f"✓ Booking accepted! Commission ₹{commission} noted (deducted from advance at trip end).")
    return redirect('partner_cab_dashboard')

@user_passes_test(is_admin, login_url='portal_login')
def portal_partner_wallet_log(request):
    from .models import PartnerWalletTransaction
    txns = PartnerWalletTransaction.objects.select_related('partner', 'booking').order_by('-created_at')[:100]
    return render(request, 'custom_admin/cab_wallet_log.html', {'transactions': txns})

# ============================================================
# PARTNER CAB PANEL - LOGIN & DASHBOARD
# ============================================================

def partner_cab_login(request):
    """Partner login using phone + password."""
    if request.user.is_authenticated:
        if hasattr(request.user, 'cab_partner'):
            return redirect('partner_cab_dashboard')
    
    if request.method == 'POST':
        phone = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '').strip()
        
        from django.contrib.auth import authenticate, login
        user = authenticate(request, username=phone, password=password)
        
        if user and hasattr(user, 'cab_partner'):
            login(request, user)
            partner = user.cab_partner
            
            if partner.status != 'ACTIVE':
                messages.error(request, f"Your account is {partner.status}. Contact admin.")
                return redirect('partner_cab_login')
            
            messages.success(request, f"Welcome back, {partner.name}!")
            return redirect('partner_cab_dashboard')
        else:
            messages.error(request, "Invalid credentials. Please check phone and password.")
    
    return render(request, 'cabs_partner/login.html')


def partner_cab_logout(request):
    from django.contrib.auth import logout
    logout(request)
    messages.info(request, "Logged out successfully.")
    return redirect('partner_cab_login')


@login_required(login_url='partner_cab_login')
def partner_cab_dashboard(request):
    """Partner dashboard - shows wallet, pending requests, active trips."""
    if not hasattr(request.user, 'cab_partner'):
        messages.error(request, "You are not a registered cab partner.")
        return redirect('index')
    
    partner = request.user.cab_partner
    
    # Pending requests (need accept/reject)
    pending_requests = CabBooking.objects.filter(
        assigned_partner=partner,
        status='PARTNER_REQUESTED'
    ).select_related('category').order_by('-created_at')
    
    # Active bookings (accepted, ongoing)
    active_bookings = CabBooking.objects.filter(
        assigned_partner=partner,
        status__in=['ASSIGNED', 'ONGOING']
    ).select_related('category').order_by('-pickup_datetime')
    
    # Completed
    completed_bookings = CabBooking.objects.filter(
        assigned_partner=partner,
        status='COMPLETED'
    ).select_related('category').order_by('-created_at')[:10]
    
    # Stats
    total_bookings = CabBooking.objects.filter(assigned_partner=partner).count()
    total_earnings = CabBooking.objects.filter(
        assigned_partner=partner,
        status__in=['ASSIGNED', 'ONGOING', 'COMPLETED']
    ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    
    context = {
        'partner': partner,
        'pending_requests': pending_requests,
        'active_bookings': active_bookings,
        'completed_bookings': completed_bookings,
        'total_bookings': total_bookings,
        'total_earnings': total_earnings,
    }
    return render(request, 'cabs_partner/dashboard.html', context)

@login_required(login_url='partner_cab_login')
def partner_cab_reject_booking(request, booking_id):
    """Partner rejects booking - admin notified."""
    if not hasattr(request.user, 'cab_partner'):
        return redirect('index')
    
    partner = request.user.cab_partner
    booking = get_object_or_404(
        CabBooking, 
        booking_id=booking_id, 
        assigned_partner=partner,
        status='PARTNER_REQUESTED'
    )
    
    if request.method == 'POST':
        reason = request.POST.get('reason', 'Rejected by partner').strip()
        
        booking.assigned_partner = None
        booking.status = 'PENDING'
        booking.save()
        
        messages.warning(request, f"Booking {booking.booking_id} rejected. Admin will reassign.")
        return redirect('partner_cab_dashboard')
    
    return render(request, 'cabs_partner/reject_confirm.html', {'booking': booking})


@login_required(login_url='partner_cab_login')
def partner_cab_wallet(request):
    """Partner wallet page with UTR recharge form."""
    if not hasattr(request.user, 'cab_partner'):
        return redirect('index')
    
    partner = request.user.cab_partner
    transactions = partner.wallet_transactions.all()[:30]
    
    return render(request, 'cabs_partner/wallet.html', {
        'partner': partner,
        'transactions': transactions,
    })


@login_required(login_url='partner_cab_login')
def partner_cab_utr_submit(request):
    """Partner submits UTR for recharge - admin verifies."""
    if not hasattr(request.user, 'cab_partner'):
        return redirect('index')
    
    partner = request.user.cab_partner
    
    if request.method == 'POST':
        utr = request.POST.get('utr', '').strip()
        amount = request.POST.get('amount', '0')
        
        try:
            amount_dec = Decimal(amount)
            if amount_dec <= 0:
                raise ValueError("Amount must be positive")
            if not utr or len(utr) < 6:
                raise ValueError("UTR must be at least 6 characters")
            
            if partner.pending_utr:
                messages.warning(request, "You already have a pending UTR. Wait for admin verification.")
                return redirect('partner_cab_wallet')
            
            partner.pending_utr = utr
            partner.pending_utr_amount = amount_dec
            partner.pending_utr_submitted_at = timezone.now()
            partner.save()
            
            messages.success(request, f"UTR submitted! ₹{amount_dec} will be credited after admin verification.")
        except Exception as e:
            messages.error(request, f"Submission failed: {e}")
        
        return redirect('partner_cab_wallet')
    
    return redirect('partner_cab_wallet')


@login_required(login_url='partner_cab_login')
def partner_cab_bookings(request):
    """Partner's all bookings list."""
    if not hasattr(request.user, 'cab_partner'):
        return redirect('index')
    
    partner = request.user.cab_partner
    bookings = CabBooking.objects.filter(assigned_partner=partner).select_related('category').order_by('-created_at')
    
    return render(request, 'cabs_partner/bookings.html', {
        'partner': partner,
        'bookings': bookings,
    })


# ============================================================
# ADMIN - UTR APPROVAL
# ============================================================

@user_passes_test(is_admin, login_url='portal_login')
def portal_partner_utr_approve(request, partner_id):
    """Admin approves partner UTR - credits wallet."""
    from .models import PartnerWalletTransaction
    partner = get_object_or_404(CabPartner, id=partner_id)
    
    if not partner.pending_utr:
        messages.warning(request, "No pending UTR.")
        return redirect('portal_partner_detail', partner_id=partner.id)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'approve':
            amount = partner.pending_utr_amount
            balance_before = partner.wallet_balance
            
            partner.wallet_balance += amount
            utr_reference = partner.pending_utr
            partner.pending_utr = None
            partner.pending_utr_amount = Decimal('0.00')
            partner.pending_utr_submitted_at = None
            partner.save()
            
            PartnerWalletTransaction.objects.create(
                partner=partner, txn_type='RECHARGE', amount=amount,
                balance_before=balance_before, balance_after=partner.wallet_balance,
                reference_id=utr_reference, notes=f"UTR verified by admin",
                created_by=request.user,
            )
            
            messages.success(request, f"✓ ₹{amount} credited to {partner.name}'s wallet.")
        
        elif action == 'reject':
            partner.pending_utr = None
            partner.pending_utr_amount = Decimal('0.00')
            partner.pending_utr_submitted_at = None
            partner.save()
            messages.warning(request, "UTR rejected. Partner will need to resubmit.")
        
        return redirect('portal_partner_detail', partner_id=partner.id)
    
    return redirect('portal_partner_detail', partner_id=partner.id)

# ============================================================
# ADVANCE PAYMENT FLOW (20%) — MANUAL UTR
# ============================================================

def cab_pay_advance(request, booking_id):
    """Customer submits UTR. UTR submission never confirms payment."""
    booking = get_object_or_404(CabBooking, booking_id=booking_id)

    if booking.advance_paid:
        messages.info(request, "Advance payment is already verified.")
        return redirect('cab_booking_detail', booking_id=booking_id)

    if booking.status in ('CANCELLED', 'CANCELLED_BY_ADMIN',
                          'CANCELLED_BY_GUEST', 'NO_SHOW'):
        messages.error(request, "Payment cannot be submitted for this booking.")
        return redirect('cab_booking_detail', booking_id=booking_id)

    upi_id = getattr(settings, 'PARTNER_UPI_ID', '').strip()
    business_name = getattr(settings, 'BUSINESS_NAME', 'Makes My Yatra')

    upi_link = ''
    qr_base64 = ''

    # Do not generate a QR with a fake/default UPI ID.
    if upi_id and upi_id != 'yourbusiness@upi':
        upi_link = (
            f"upi://pay?pa={quote(upi_id)}"
            f"&pn={quote(business_name)}"
            f"&am={booking.advance_amount}"
            f"&cu=INR"
            f"&tn={quote('Advance for ' + booking.booking_id)}"
        )

        try:
            qr = qrcode.QRCode(version=1, box_size=8, border=2)
            qr.add_data(upi_link)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            qr_base64 = base64.b64encode(buffer.getvalue()).decode()
        except Exception:
            logger.exception("Cab advance QR generation failed.")
            messages.error(request, "Payment QR is temporarily unavailable.")

    if request.method == 'POST':
        utr = request.POST.get('utr', '').strip()

        if not upi_id or upi_id == 'yourbusiness@upi':
            messages.error(request, "Business UPI ID is not configured yet.")
        elif len(utr) < 6 or len(utr) > 50:
            messages.error(request, "Please enter a valid UTR/reference number.")
        elif booking.advance_utr and booking.status == 'ADVANCE_PENDING':
            messages.warning(
                request,
                "Your UTR is already submitted and is awaiting admin verification."
            )
        else:
            try:
                with transaction.atomic():
                    locked_booking = CabBooking.objects.select_for_update().get(
                        pk=booking.pk
                    )

                    if locked_booking.advance_paid:
                        messages.info(request, "Advance is already verified.")
                    elif locked_booking.status in (
                        'CANCELLED', 'CANCELLED_BY_ADMIN',
                        'CANCELLED_BY_GUEST', 'NO_SHOW'
                    ):
                        messages.error(request, "This booking is not accepting payment.")
                    elif locked_booking.advance_utr and (
                        locked_booking.status == 'ADVANCE_PENDING'
                    ):
                        messages.warning(
                            request,
                            "A UTR is already awaiting verification."
                        )
                    else:
                        locked_booking.advance_utr = utr
                        locked_booking.advance_utr_submitted_at = timezone.now()
                        locked_booking.status = 'ADVANCE_PENDING'
                        locked_booking.save(update_fields=[
                            'advance_utr',
                            'advance_utr_submitted_at',
                            'status',
                        ])
                        messages.success(
                            request,
                            "UTR submitted. Booking will be confirmed only "
                            "after the payment is verified by admin."
                        )
                        return redirect(
                            'cab_booking_detail',
                            booking_id=booking_id
                        )
            except CabBooking.DoesNotExist:
                messages.error(request, "Booking not found.")

    return render(request, 'cabs/cab_pay_advance.html', {
        'booking': booking,
        'upi_id': upi_id,
        'business_name': business_name,
        'upi_link': upi_link,
        'qr_base64': qr_base64,
    })


# ============================================================
# ADMIN — VERIFY ADVANCE / MARK NO-SHOW
# ============================================================

@user_passes_test(is_admin, login_url='portal_login')
@require_POST
def portal_cab_verify_advance(request, booking_id):
    """Admin verifies UTR against actual bank/UPI statement."""

    action = request.POST.get('action', '').strip()

    with transaction.atomic():
        booking = get_object_or_404(
            CabBooking.objects.select_for_update(),
            booking_id=booking_id
        )

        if action == 'approve':
            if booking.advance_paid:
                messages.info(request, "This advance is already verified.")
                return redirect(
                    'portal_cab_booking_detail',
                    booking_id=booking.booking_id
                )

            if not booking.advance_utr or not booking.advance_utr_submitted_at:
                messages.error(request, "No UTR has been submitted.")
                return redirect(
                    'portal_cab_booking_detail',
                    booking_id=booking.booking_id
                )

            if booking.status != 'ADVANCE_PENDING':
                messages.error(
                    request,
                    "Booking is not in the advance verification queue."
                )
                return redirect(
                    'portal_cab_booking_detail',
                    booking_id=booking.booking_id
                )

                        # IMPORTANT:
            # Admin must first confirm this exact payment in the bank statement.
            booking.advance_paid = True
            booking.advance_paid_at = timezone.now()
            booking.status = 'CONFIRMED'
            booking.save(update_fields=[
                'advance_paid',
                'advance_paid_at',
                'status',
            ])

            # Redeem WELCOME10 only after verified cab advance.
            if (
                booking.coupon
                and booking.coupon.code.strip().upper() == 'WELCOME10'
                and booking.guest_email
            ):
                try:
                    with transaction.atomic():
                        Welcome10Redemption.objects.create(
                            email=booking.guest_email.strip().lower(),
                            booking_type='CAB',
                            booking_id=booking.booking_id
                        )
                except IntegrityError:
                    logger.exception(
                        "WELCOME10 redemption conflict for cab booking %s",
                        booking.booking_id
                    )
                    messages.warning(
                        request,
                        "Booking confirmed, but WELCOME10 needs admin review."
                    )

            messages.success(
                request,
                f"Advance ₹{booking.advance_amount} marked verified. "
                "Booking confirmed."
            )
            messages.success(
                request,
                f"Advance ₹{booking.advance_amount} marked verified. "
                "Booking confirmed."
            )

            if booking.guest_email:
                try:
                    send_mail(
                        f"Booking Confirmed — {booking.booking_id}",
                        (
                            f"Dear {booking.guest_name},\n\n"
                            f"Your advance payment of ₹{booking.advance_amount} "
                            f"has been verified.\n"
                            f"Booking {booking.booking_id} is CONFIRMED.\n\n"
                            f"Balance to pay: ₹{booking.balance_amount}\n\n"
                            f"— Makes My Yatra"
                        ),
                        settings.DEFAULT_FROM_EMAIL,
                        [booking.guest_email],
                        fail_silently=True,
                    )
                except Exception:
                    logger.exception("Cab booking confirmation email failed.")

        elif action == 'reject':
            if booking.advance_paid:
                messages.error(
                    request,
                    "A verified payment cannot be rejected from this screen."
                )
            elif not booking.advance_utr:
                messages.warning(request, "There is no submitted UTR to reject.")
            else:
                booking.advance_utr = None
                booking.advance_utr_submitted_at = None
                booking.status = 'PENDING'
                booking.save(update_fields=[
                    'advance_utr',
                    'advance_utr_submitted_at',
                    'status',
                ])
                messages.warning(
                    request,
                    "UTR rejected. Customer can submit a corrected reference."
                )

        elif action == 'no_show':
            if booking.status not in ('CONFIRMED', 'ASSIGNED', 'ONGOING'):
                messages.error(
                    request,
                    "Only a confirmed or assigned booking can be marked no-show."
                )
            elif booking.status == 'NO_SHOW':
                messages.info(request, "Booking is already marked no-show.")
            else:
                booking.status = 'NO_SHOW'
                booking.cancelled_at = timezone.now()
                booking.cancellation_reason = "Customer no-show"
                booking.save(update_fields=[
                    'status',
                    'cancelled_at',
                    'cancellation_reason',
                ])

                # No automatic advance forfeiture or wallet credit here.
                # Handle compensation/refund only according to the approved
                # business policy and after checking the actual payment.
                messages.warning(
                    request,
                    "Booking marked NO-SHOW. Advance/refund and driver "
                    "compensation must be reconciled according to business policy."
                )

        else:
            messages.error(request, "Invalid action.")

    return redirect(
        'portal_cab_booking_detail',
        booking_id=booking_id
    )

# ============================================================
# ADMIN — COMPLETE TRIP + WAITING CHARGES + DRIVER SETTLEMENT
# ============================================================

@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_complete_trip(request, booking_id):
    """Complete an ongoing trip and settle partner advance only once."""

    if request.method == 'POST':
        waiting_minutes = _to_int(
            request.POST.get('waiting_minutes'),
            0,
            0,
            180
        )

        with transaction.atomic():
            booking = get_object_or_404(
                CabBooking.objects.select_for_update(),
                booking_id=booking_id
            )

            if booking.status == 'COMPLETED':
                messages.info(request, "This trip has already been completed.")
                return redirect(
                    'portal_cab_booking_detail',
                    booking_id=booking.booking_id
                )

            if booking.status != 'ONGOING':
                messages.error(
                    request,
                    "Only an ONGOING trip can be marked completed."
                )
                return redirect(
                    'portal_cab_booking_detail',
                    booking_id=booking.booking_id
                )

            # First 15 minutes are free.
            # Every started 15-minute block after that is chargeable.
            if waiting_minutes <= 15:
                waiting_charges = Decimal('0.00')
            else:
                extra_blocks = (waiting_minutes - 15 + 14) // 15
                waiting_charges = Decimal(extra_blocks * 50)

            booking.waiting_minutes = waiting_minutes
            booking.waiting_charges = waiting_charges
            booking.balance_amount = (
                booking.balance_amount or Decimal('0.00')
            ) + waiting_charges
            booking.total_amount = (
                booking.total_amount or Decimal('0.00')
            ) + waiting_charges

            # Settle partner only when advance is verified.
            if booking.assigned_partner and booking.advance_paid:
                if booking.driver_settled:
                    messages.error(
                        request,
                        "Partner settlement is already recorded. "
                        "Please check the wallet ledger."
                    )
                    return redirect(
                        'portal_cab_booking_detail',
                        booking_id=booking.booking_id
                    )

                commission = booking.commission_amount or Decimal('0.00')
                driver_credit = max(
                    Decimal('0.00'),
                    booking.advance_amount - commission
                )

                partner = CabPartner.objects.select_for_update().get(
                    pk=booking.assigned_partner_id
                )

                balance_before = partner.wallet_balance
                partner.wallet_balance = balance_before + driver_credit
                partner.save(update_fields=['wallet_balance'])

                PartnerWalletTransaction.objects.create(
                    partner=partner,
                    txn_type='ADJUSTMENT',
                    amount=driver_credit,
                    balance_before=balance_before,
                    balance_after=partner.wallet_balance,
                    booking=booking,
                    notes=f"Advance settlement for {booking.booking_id}",
                    created_by=request.user,
                )

                booking.driver_wallet_credit = driver_credit
                booking.driver_settled = True
                booking.driver_settled_at = timezone.now()

            booking.status = 'COMPLETED'
            booking.save()

        messages.success(
            request,
            f"Trip completed. Waiting charges: ₹{waiting_charges}. "
            f"Partner wallet credit: ₹{booking.driver_wallet_credit or 0}."
        )
        return redirect(
            'portal_cab_booking_detail',
            booking_id=booking.booking_id
        )

    booking = get_object_or_404(CabBooking, booking_id=booking_id)

    return render(
        request,
        'custom_admin/cab_complete_trip.html',
        {'booking': booking}
    )

# ============================================================
# ADMIN — SAFE CAB BOOKING DELETION
# ============================================================

@user_passes_test(is_admin, login_url='portal_login')
@require_POST
def portal_cab_booking_actions(request):
    """Delete only eligible unpaid pending bookings."""

    action = request.POST.get('action', '').strip()

    if action == 'delete_selected':
        selected_ids = request.POST.getlist('selected_bookings')

        if not selected_ids:
            messages.warning(request, "No bookings selected.")
            return redirect('portal_cab_bookings')

        eligible_bookings = CabBooking.objects.filter(
            id__in=selected_ids,
            status='PENDING',
            advance_paid=False,
            advance_utr__isnull=True,
            payment_status='PENDING',
        )

        count = eligible_bookings.count()
        eligible_bookings.delete()

        messages.success(
            request,
            f"{count} eligible unpaid pending bookings deleted."
        )

        skipped = len(selected_ids) - count
        if skipped > 0:
            messages.warning(
                request,
                f"{skipped} bookings were not deleted because they "
                "may have payment activity or a different status."
            )

    elif action == 'delete_all':
        messages.error(
            request,
            "Bulk deletion of all bookings is disabled to protect "
            "customer and payment records."
        )

    else:
        messages.error(request, "Invalid booking action.")

    return redirect('portal_cab_bookings')


@user_passes_test(is_admin, login_url='portal_login')
@require_POST
def portal_cab_booking_delete(request, booking_id):
    """Delete one eligible unpaid pending booking."""

    with transaction.atomic():
        booking = get_object_or_404(
            CabBooking.objects.select_for_update(),
            booking_id=booking_id
        )

        if (
            booking.status != 'PENDING'
            or booking.advance_paid
            or booking.advance_utr
            or booking.payment_status != 'PENDING'
        ):
            messages.error(
                request,
                "This booking cannot be deleted because it may contain "
                "payment activity or important booking history."
            )
            return redirect('portal_cab_bookings')

        bid = booking.booking_id
        booking.delete()

    messages.success(request, f"Booking {bid} deleted.")
    return redirect('portal_cab_bookings')