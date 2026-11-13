import io
from decimal import Decimal
from datetime import datetime, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Count, Q
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.template.loader import get_template
from django.core.mail import send_mail
from django.conf import settings
from django_ratelimit.decorators import ratelimit
from xhtml2pdf import pisa
import logging

from .models import CabCategory, CabRoutePackage, CabBooking, CabReview, Coupon
from .views import is_admin, _to_int, _normalize_phone

logger = logging.getLogger(__name__)

CAB_GST_RATE = Decimal('0.05')
NIGHT_CHARGE = Decimal('200')
WAITING_CHARGE_PER_HOUR = Decimal('100')


def calculate_cab_fare(category, trip_type, distance_km, pickup_time, waiting_hours=0, coupon=None):
    distance_km = Decimal(str(distance_km)) if distance_km else Decimal('0')
    waiting_hours = int(waiting_hours) if waiting_hours else 0

    if trip_type == 'ONE_WAY' and distance_km > 0:
        base_fare = (distance_km * category.base_per_km_rate) + category.driver_allowance_per_day
    elif trip_type == 'AIRPORT':
        base_fare = (Decimal('25') * category.base_per_km_rate) + category.driver_allowance_per_day
    elif trip_type == 'ROUND_TRIP':
        days = max(1, int(distance_km / category.min_km_per_day) + 1) if distance_km else 1
        base_fare = (Decimal(category.min_km_per_day) * days * category.base_per_km_rate) + (category.driver_allowance_per_day * days)
    elif trip_type == 'HOURLY':
        hours = max(4, int(distance_km / 40))
        base_fare = Decimal(hours) * Decimal('250') + category.driver_allowance_per_day
    else:
        base_fare = category.base_per_km_rate * Decimal(category.min_km_per_day) + category.driver_allowance_per_day

    base_fare = base_fare.quantize(Decimal('0.01'))
    is_night = pickup_time.hour >= 22 or pickup_time.hour < 6
    night_charge = NIGHT_CHARGE if is_night else Decimal('0.00')
    waiting_charge = (Decimal(waiting_hours) * WAITING_CHARGE_PER_HOUR).quantize(Decimal('0.01'))
    state_tax = Decimal('500.00') if trip_type in ['ONE_WAY', 'ROUND_TRIP'] and distance_km > 200 else Decimal('0.00')

    subtotal = base_fare + night_charge + waiting_charge + state_tax
    discount = Decimal('0.00')
    if coupon:
        is_valid, _ = coupon.is_valid()
        if is_valid:
            discount, _ = coupon.calculate_discount(subtotal)

    taxable = max(Decimal('0'), subtotal - discount)
    gst = (taxable * CAB_GST_RATE).quantize(Decimal('0.01'))
    total = (taxable + gst).quantize(Decimal('0.01'))

    return {
        'base_fare': base_fare, 'night_charge': night_charge, 'waiting_charge': waiting_charge,
        'state_tax': state_tax, 'subtotal': subtotal, 'discount': discount,
        'taxable': taxable, 'gst': gst, 'total': total,
    }


def cab_list(request):
    categories = CabCategory.objects.filter(is_active=True).order_by('seating_capacity')
    popular_routes = CabRoutePackage.objects.filter(is_active=True).select_related('category').order_by('from_city', 'fixed_price')[:12]
    return render(request, 'cabs/cab_list.html', {'categories': categories, 'popular_routes': popular_routes})


def cab_search(request):
    if request.method != 'POST':
        return redirect('cab_list')

    trip_type = request.POST.get('trip_type', 'ONE_WAY')
    from_city = request.POST.get('from_city', '').strip()
    to_city = request.POST.get('to_city', '').strip()
    pickup_date_str = request.POST.get('pickup_date', '')
    pickup_time_str = request.POST.get('pickup_time', '')
    distance_km = _to_int(request.POST.get('distance_km'), 0, 0, 5000)
    category_id = request.POST.get('category_id')
    coupon_code = request.POST.get('coupon_code', '').strip()

    if not from_city or not pickup_date_str:
        messages.error(request, "Please fill required fields.")
        return redirect('cab_list')

    try:
        pickup_date = datetime.strptime(pickup_date_str, '%Y-%m-%d').date()
        pickup_time = datetime.strptime(pickup_time_str or '10:00', '%H:%M').time()
    except ValueError:
        messages.error(request, "Invalid date/time.")
        return redirect('cab_list')

    if pickup_date < timezone.localdate():
        messages.error(request, "Pickup date cannot be in the past.")
        return redirect('cab_list')

    categories = CabCategory.objects.filter(is_active=True)
    if category_id:
        categories = categories.filter(id=category_id)

    coupon_obj = Coupon.objects.filter(code__iexact=coupon_code, is_active=True).first() if coupon_code else None

    cabs_with_fare = []
    for cat in categories:
        fare = calculate_cab_fare(cat, trip_type, distance_km, pickup_time, 0, coupon_obj)
        cabs_with_fare.append({'category': cat, **fare})

    return render(request, 'cabs/cab_search_results.html', {
        'cabs': cabs_with_fare, 'trip_type': trip_type, 'from_city': from_city,
        'to_city': to_city, 'pickup_date': pickup_date, 'pickup_time': pickup_time,
        'distance_km': distance_km, 'coupon_code': coupon_code,
        'coupon_valid': coupon_obj is not None,
    })


@ratelimit(key='ip', rate='10/h', method='POST', block=True)
def book_cab(request):
    if request.method != 'POST':
        return redirect('cab_list')

    category_id = request.POST.get('category_id')
    trip_type = request.POST.get('trip_type', 'ONE_WAY')
    from_city = request.POST.get('from_city', '').strip()
    to_city = request.POST.get('to_city', '').strip()
    pickup_address = request.POST.get('pickup_address', '').strip()
    pickup_date_str = request.POST.get('pickup_date', '')
    pickup_time_str = request.POST.get('pickup_time', '')
    distance_km = _to_int(request.POST.get('distance_km'), 0, 0, 5000)
    waiting_hours = _to_int(request.POST.get('waiting_hours'), 0, 0, 24)
    coupon_code = request.POST.get('coupon_code', '').strip()

    guest_name = request.POST.get('guest_name', '').strip()
    guest_phone = request.POST.get('guest_phone', '').strip()
    guest_email = request.POST.get('guest_email', '').strip()

    if not all([category_id, from_city, pickup_address, guest_name, guest_phone]):
        messages.error(request, "Please fill all required fields.")
        return redirect('cab_list')

    if len(_normalize_phone(guest_phone)) < 10:
        messages.error(request, "Please enter valid 10-digit phone.")
        return redirect('cab_list')

    try:
        pickup_date = datetime.strptime(pickup_date_str, '%Y-%m-%d').date()
        pickup_time = datetime.strptime(pickup_time_str or '10:00', '%H:%M').time()
        pickup_datetime = timezone.make_aware(datetime.combine(pickup_date, pickup_time))
    except ValueError:
        messages.error(request, "Invalid date/time.")
        return redirect('cab_list')

    if pickup_datetime < timezone.now():
        messages.error(request, "Pickup cannot be in the past.")
        return redirect('cab_list')

    category = get_object_or_404(CabCategory, id=category_id, is_active=True)

    coupon_obj = None
    if coupon_code:
        c = Coupon.objects.filter(code__iexact=coupon_code, is_active=True).first()
        if c and c.is_valid()[0]:
            coupon_obj = c

    fare = calculate_cab_fare(category, trip_type, distance_km, pickup_time, waiting_hours, coupon_obj)

    try:
        with transaction.atomic():
            booking = CabBooking.objects.create(
                category=category, user=request.user if request.user.is_authenticated else None,
                coupon=coupon_obj, trip_type=trip_type, pickup_city=from_city, drop_city=to_city,
                pickup_address=pickup_address, pickup_datetime=pickup_datetime,
                distance_km=distance_km, waiting_hours=waiting_hours,
                guest_name=guest_name, guest_phone=guest_phone, guest_email=guest_email,
                base_fare=fare['base_fare'], driver_allowance=category.driver_allowance_per_day,
                night_charge=fare['night_charge'], state_tax=fare['state_tax'],
                gst_amount=fare['gst'], discount_amount=fare['discount'], total_amount=fare['total'],
                payment_mode='PAY_TO_DRIVER', payment_status='PENDING', status='CONFIRMED',
            )
    except Exception as e:
        logger.error(f"Cab booking failed: {e}")
        messages.error(request, "Booking failed. Try again.")
        return redirect('cab_list')

    if booking.guest_email:
        try:
            send_mail(
                f"Cab Booking Confirmed — {booking.booking_id}",
                f"Dear {booking.guest_name},\n\nYour cab booking is confirmed.\nBooking ID: {booking.booking_id}\nPickup: {booking.pickup_address}, {booking.pickup_city}\nDate: {booking.pickup_datetime.strftime('%d %b %Y, %I:%M %p')}\nTotal: Rs. {booking.total_amount} (Pay to driver)\n\n— Makes My Yatra",
                settings.DEFAULT_FROM_EMAIL, [booking.guest_email], fail_silently=True,
            )
        except Exception as e:
            logger.warning(f"Cab email failed: {e}")

    messages.success(request, f"Cab booking #{booking.booking_id} confirmed!")
    return redirect('cab_booking_detail', booking_id=booking.booking_id)


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


@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_booking_update(request, booking_id):
    booking = get_object_or_404(CabBooking, booking_id=booking_id)
    if request.method == 'POST':
        new_status = request.POST.get('status', booking.status)
        booking.status = new_status
        booking.driver_name = request.POST.get('driver_name', booking.driver_name or '').strip()
        booking.driver_phone = request.POST.get('driver_phone', booking.driver_phone or '').strip()
        booking.vehicle_number = request.POST.get('vehicle_number', booking.vehicle_number or '').strip()
        if new_status == 'COMPLETED' and booking.payment_status == 'PENDING':
            booking.payment_status = 'PAID'
        booking.save()
        messages.success(request, f"Booking {booking.booking_id} updated.")
    return redirect('portal_cab_booking_detail', booking_id=booking.booking_id)


@user_passes_test(is_admin, login_url='portal_login')
def portal_cab_cancel(request, booking_id):
    booking = get_object_or_404(CabBooking, booking_id=booking_id)
    if booking.status in ('COMPLETED', 'CANCELLED', 'CANCELLED_BY_ADMIN', 'CANCELLED_BY_GUEST'):
        messages.warning(request, "Cannot cancel.")
        return redirect('portal_cab_booking_detail', booking_id=booking.booking_id)
    booking.status = 'CANCELLED_BY_ADMIN'
    booking.cancelled_by = 'ADMIN'
    booking.cancelled_at = timezone.now()
    booking.cancellation_reason = "Cancelled by admin"
    booking.save()
    messages.success(request, f"Booking {booking.booking_id} cancelled.")
    return redirect('portal_cab_bookings')


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