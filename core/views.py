import uuid
import openpyxl
import urllib.parse
from decimal import Decimal
from datetime import datetime, date
from openpyxl.styles import Font, PatternFill, Alignment
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Q, Count, Max
from django.core.mail import send_mail
from django.conf import settings
from .models import Hotel, RoomType, MealPlan, Booking, EmergencySupport, HotelReview, HotelMedia
from .forms import HotelForm, RoomTypeForm, BookingStatusForm
from .models import Booking
from .notifications import (
    send_booking_sms,
    send_booking_email,
    send_whatsapp_notification,
)

# Helper to verify admin permissions
def is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)

# Pricing and GST slab calculator with dynamic percentage discount
def calculate_stay_pricing(base_price, meal_extra=0, nights=1, rooms=1, discount_percent=0):
    base_price = Decimal(str(base_price))
    meal_extra = Decimal(str(meal_extra))
    nights = int(nights) if int(nights) > 0 else 1
    rooms = int(rooms) if int(rooms) > 0 else 1

    nightly_rate = base_price + meal_extra
    gross_total = nightly_rate * nights * rooms

    # Calculate percentage discount
    discount_amount = (gross_total * (Decimal(str(discount_percent)) / Decimal('100'))).quantize(Decimal('0.01'))
    taxable_amount = max(Decimal('0.00'), gross_total - discount_amount)

    # Dynamic GST slab logic
    if nightly_rate < 1000:
        gst_rate = Decimal('0.00')
    elif nightly_rate <= 7500:
        gst_rate = Decimal('0.12')
    else:
        gst_rate = Decimal('0.18')

    total_gst = (taxable_amount * gst_rate).quantize(Decimal('0.01'))
    cgst = (total_gst / 2).quantize(Decimal('0.01'))
    sgst = (total_gst / 2).quantize(Decimal('0.01'))
    grand_total = (taxable_amount + total_gst).quantize(Decimal('0.01'))

    return {
        'nightly_rate': nightly_rate,
        'gross_total': gross_total,
        'discount_amount': discount_amount,
        'taxable_amount': taxable_amount,
        'cgst': cgst,
        'sgst': sgst,
        'total_gst': total_gst,
        'grand_total': grand_total,
    }

# Homepage and search filter
def index(request):
    city = request.GET.get('city', 'All')
    max_price = request.GET.get('max_price', '')
    property_type = request.GET.get('property_type', '')
    min_rating = request.GET.get('min_rating', '')

    hotels = Hotel.objects.filter(is_active=True).prefetch_related('room_types', 'reviews', 'media_gallery')

    if city and city != 'All':
        hotels = hotels.filter(city__iexact=city)
    if property_type:
        hotels = hotels.filter(category__iexact=property_type)
    if min_rating:
        try:
            hotels = hotels.filter(star_rating__gte=int(min_rating))
        except ValueError:
            pass
    if max_price:
        try:
            hotels = hotels.filter(room_types__base_price__lte=Decimal(max_price)).distinct()
        except Exception:
            pass

    return render(request, 'index.html', {
        'hotels': hotels,
        'selected_city': city,
        'request': request,
    })


# Booking engine with dynamic pricing, meal plan, auto offers, and GST
def book_hotel(request):
    if request.method == 'POST':
        hotel_id = request.POST.get('hotel_id')
        room_type_id = request.POST.get('room_type_id')
        meal_plan_id = request.POST.get('meal_plan_id')
        guest_name = request.POST.get('guest_name', 'Guest').strip()
        guest_phone = request.POST.get('guest_phone', '').strip()
        guest_email = request.POST.get('guest_email', '').strip()
        check_in_str = request.POST.get('check_in')
        check_out_str = request.POST.get('check_out')

        hotel = get_object_or_404(Hotel, id=hotel_id)
        room_type = RoomType.objects.filter(id=room_type_id).first() if room_type_id else hotel.room_types.first()
        base_rate = room_type.base_price if room_type else Decimal('1800.00')

        # Flexible Meal Plan Resolution (Numeric PK & string codes: EP, CP, MAP, AP)
        meal_plan = None
        meal_extra = Decimal('0.00')
        if meal_plan_id:
            cleaned_val = str(meal_plan_id).strip()
            if cleaned_val.isdigit():
                meal_plan = MealPlan.objects.filter(id=int(cleaned_val), room_type=room_type).first()
            else:
                meal_plan = MealPlan.objects.filter(plan_code__iexact=cleaned_val, room_type=room_type).first()
                if not meal_plan:
                    meal_plan = MealPlan.objects.filter(plan_code__iexact=cleaned_val).first()

            if meal_plan:
                meal_extra = meal_plan.additional_price
            else:
                fallback_rates = {
                    'EP': Decimal('0.00'),
                    'CP': Decimal('350.00'),
                    'MAP': Decimal('750.00'),
                    'AP': Decimal('1200.00'),
                }
                meal_extra = fallback_rates.get(cleaned_val.upper(), Decimal('0.00'))

        # Calculate stay duration
        nights = 1
        check_in_date = None
        check_out_date = None
        if check_in_str and check_out_str:
            try:
                check_in_date = datetime.strptime(check_in_str, '%Y-%m-%d').date()
                check_out_date = datetime.strptime(check_out_str, '%Y-%m-%d').date()
                nights = max((check_out_date - check_in_date).days, 1)
            except ValueError:
                pass

        # --- Automatic Offer & Discount Evaluation ---
        discount_percentage = Decimal('0.00')
        offer_name = "Standard Pricing"

        # 1. Early Bird Offer: Booking made >= 7 days before check-in (10% OFF)
        if check_in_date and (check_in_date - timezone.localdate()).days >= 7:
            discount_percentage = Decimal('10.00')
            offer_name = "Early Bird Offer (10% OFF)"

        # 2. Long Stay Offer: Booking stay >= 3 nights (15% OFF)
        if nights >= 3:
            if Decimal('15.00') > discount_percentage:
                discount_percentage = Decimal('15.00')
                offer_name = "Long Stay Offer (15% OFF)"

        # 3. Promotional Coupon Code: WELCOMEYATRA (20% OFF)
        coupon_input = (request.POST.get('coupon_code') or '').strip().upper()
        if coupon_input == 'WELCOMEYATRA':
            discount_percentage = Decimal('20.00')
            offer_name = "Promo WELCOMEYATRA (20% OFF)"

        # Compute bill with auto GST & applied discount
        bill = calculate_stay_pricing(
            base_price=base_rate,
            meal_extra=meal_extra,
            nights=nights,
            rooms=1,
            discount_percent=discount_percentage
        )

        custom_booking_id = f"MMY-HTL-{uuid.uuid4().hex[:4].upper()}"

        # 1. Create and save booking in DB
        booking = Booking.objects.create(
            booking_id=custom_booking_id,
            hotel=hotel,
            room_type=room_type,
            meal_plan=meal_plan,
            user=request.user if request.user.is_authenticated else None,
            guest_name=guest_name,
            guest_phone=guest_phone,
            guest_email=guest_email,
            check_in=check_in_date,
            check_out=check_out_date,
            destination_title=f"{hotel.name} ({hotel.city})",
            travel_date=check_in_date,
            guests_count=2,
            base_amount=bill['taxable_amount'],
            cgst_amount=bill['cgst'],
            sgst_amount=bill['sgst'],
            gst_amount=bill['total_gst'],
            total_amount=bill['grand_total'],
            payment_mode='PAY_AT_HOTEL',
            payment_status='CONFIRMED',
            status='CONFIRMED',
            transaction_id=f"TXN-{uuid.uuid4().hex[:8].upper()}"
        )

        # 2. Multi-channel alerts dispatch (BEFORE rendering template)
        send_booking_sms(booking)
        send_booking_email(booking)
        send_whatsapp_notification(booking)

        # 3. Render success page to user
        return render(request, 'booking_success.html', {
            'booking': booking,
            'hotel': hotel,
            'guest_name': guest_name,
            'guest_phone': guest_phone,
            'guest_email': guest_email,
            'check_in': check_in_str,
            'check_out': check_out_str,
            'offer_applied': offer_name,
            'discount_amount': bill['discount_amount'],
        })

    return redirect('index')

# Helpdesk and traveler support
def support_request(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        phone = request.POST.get('phone')
        location = request.POST.get('location', '')
        details = request.POST.get('details') or request.POST.get('issue', '')
        EmergencySupport.objects.create(
            name=name,
            phone=phone,
            location=location,
            issue_details=details
        )
        messages.success(request, 'Your emergency assistance request has been received. Our team will contact you shortly.')
        return redirect('support_request')
    return render(request, 'traveler_help.html')


# Admin portal login and logout
def portal_login(request):
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user is not None:
            login(request, user)
            request.session.set_expiry(0)
            return redirect('portal_dashboard')
        else:
            messages.error(request, 'Invalid admin credentials.')
    return render(request, 'custom_admin/login.html')


def portal_logout(request):
    logout(request)
    return redirect('portal_login')


# Admin operations dashboard
@user_passes_test(is_admin, login_url='portal_login')
def portal_dashboard(request):
    today = timezone.localdate()
    total_hotels = Hotel.objects.count()
    total_bookings = Booking.objects.count()
    active_supports = EmergencySupport.objects.filter(status='open').count()
    revenue = Booking.objects.filter(payment_status='CONFIRMED').aggregate(
        Sum('total_amount')
    )['total_amount__sum'] or 0

    today_checkins = Booking.objects.filter(check_in=today, status__in=['CONFIRMED', 'CHECKED_IN']).count()
    today_checkouts = Booking.objects.filter(check_out=today, status='CHECKED_IN').count()
    new_bookings_today = Booking.objects.filter(created_at__date=today).count()
    cancellations_count = Booking.objects.filter(status__iexact='cancelled').count()

    pipeline = {
        'pending': Booking.objects.filter(status__iexact='pending').count(),
        'confirmed': Booking.objects.filter(status__iexact='confirmed').count(),
        'checked_in': Booking.objects.filter(status__iexact='checked_in').count(),
        'checked_out': Booking.objects.filter(status__iexact='checked_out').count(),
    }

    hotels = Hotel.objects.all().order_by('-created_at')[:5]
    supports = EmergencySupport.objects.all().order_by('-created_at')[:5]
    recent_bookings = Booking.objects.select_related('user', 'hotel').order_by('-created_at')[:8]

    context = {
        'total_hotels': total_hotels,
        'total_bookings': total_bookings,
        'active_supports': active_supports,
        'total_supports': active_supports,
        'revenue': revenue,
        'today_checkins': today_checkins,
        'today_checkouts': today_checkouts,
        'new_bookings_today': new_bookings_today,
        'cancellations_count': cancellations_count,
        'pipeline': pipeline,
        'hotels': hotels,
        'supports': supports,
        'bookings': recent_bookings,
        'today_date': today,
    }
    return render(request, 'custom_admin/dashboard.html', context)


# Bulk delete & clear actions
def portal_booking_actions(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete_selected":
            selected_ids = request.POST.getlist("selected_bookings")
            if selected_ids:
                count = Booking.objects.filter(id__in=selected_ids).delete()[0]
                messages.success(request, f"{count} selected bookings deleted successfully.")
            else:
                messages.warning(request, "You haven't selected any bookings.")
        elif action == "delete_all":
            count = Booking.objects.count()
            Booking.objects.all().delete()
            messages.success(request, f"Saari {count} bookings deleted successfully!")
    return redirect('portal_bookings')


# Individual Booking Deletion
def portal_booking_delete(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    booking.delete()
    messages.success(request, f"Booking #{booking_id} deleted successfully.")
    return redirect('portal_bookings')


# Customer directory
@user_passes_test(is_admin, login_url='portal_login')
def portal_customers(request):
    customers = Booking.objects.values('guest_name', 'guest_phone', 'guest_email').annotate(
        total_stays=Count('id'),
        total_spent=Sum('total_amount'),
        last_stay=Max('check_in')
    ).order_by('-total_stays')
    return render(request, 'custom_admin/customers.html', {'customers': customers})


# Property listing
@user_passes_test(is_admin, login_url='portal_login')
def portal_hotels(request):
    hotels = Hotel.objects.all().prefetch_related('room_types', 'media_gallery').order_by('-created_at')
    return render(request, 'custom_admin/hotel_list.html', {'hotels': hotels})


# Add new property
# Add hotel
@user_passes_test(is_admin, login_url='portal_login')
@login_required(login_url='portal_login')
def portal_hotel_add(request):
    if request.method == 'POST':
        # Basic fields
        name = request.POST.get('name', '').strip()
        category = request.POST.get('category', 'budget')
        city = request.POST.get('city', 'Shimla')
        star_rating = int(request.POST.get('star_rating', 3))
        address = request.POST.get('address', '').strip()
        google_maps_url = request.POST.get('google_maps_url', '').strip()
        base_price = request.POST.get('base_price', 1800.00)

        # Hotel object create karo
        hotel = Hotel.objects.create(
            name=name,
            category=category,
            city=city,
            star_rating=star_rating,
            address=address,
            google_maps_url=google_maps_url if google_maps_url else None,
            
            # Amenities (Checkboxes logic)
            has_wifi='has_wifi' in request.POST,
            has_ac='has_ac' in request.POST,
            has_parking='has_parking' in request.POST,
            has_restaurant='has_restaurant' in request.POST,
            has_breakfast='has_breakfast' in request.POST,
            has_heater='has_heater' in request.POST,
            has_pool='has_pool' in request.POST,
            is_active='is_active' in request.POST,

            # Owner / Ops Contacts
            owner_name=request.POST.get('owner_name', '').strip(),
            owner_phone=request.POST.get('owner_phone', '').strip(),
            owner_email=request.POST.get('owner_email', '').strip(),
            commission_percentage=request.POST.get('commission_percentage', 15.00),
        )

        # Device Direct Cover Photo Upload
        if 'cover_image' in request.FILES:
            cover_file = request.FILES['cover_image']
            media_item = HotelMedia.objects.create(hotel=hotel, image_file=cover_file, caption="Primary Cover")
            hotel.cover_image = media_item.image_file.url
            hotel.save()
        elif request.POST.get('cover_image_url'):
            hotel.cover_image = request.POST.get('cover_image_url')
            hotel.save()

        # Bulk Gallery Photos Upload (Multi-Select)
        gallery_files = request.FILES.getlist('gallery_photos')
        for f in gallery_files:
            HotelMedia.objects.create(hotel=hotel, image_file=f, caption="Gallery Upload")

        # Initial Default Room Type create karo base tariff ke saath
        RoomType.objects.create(
            hotel=hotel,
            name="Standard Deluxe",
            base_price=base_price,
            weekend_price=float(base_price) + 400.00,
            festival_price=float(base_price) + 800.00,
            total_inventory=5,
            is_open=True,
            is_available=True
        )

        messages.success(request, f"Stay '{hotel.name}' and amenities onboarded successfully!")
        return redirect('portal_hotels')

    return render(request, 'custom_admin/hotel_add.html', {'is_edit': False})

# Edit hotel
@login_required(login_url='portal_login')
def portal_hotel_edit(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id)
    
    if request.method == 'POST':
        hotel.name = request.POST.get('name', hotel.name).strip()
        hotel.category = request.POST.get('category', hotel.category)
        hotel.city = request.POST.get('city', hotel.city)
        hotel.star_rating = int(request.POST.get('star_rating', hotel.star_rating))
        hotel.address = request.POST.get('address', hotel.address).strip()
        
        g_map = request.POST.get('google_maps_url', '').strip()
        hotel.google_maps_url = g_map if g_map else None

        # Amenities Update
        hotel.has_wifi = 'has_wifi' in request.POST
        hotel.has_ac = 'has_ac' in request.POST
        hotel.has_parking = 'has_parking' in request.POST
        hotel.has_restaurant = 'has_restaurant' in request.POST
        hotel.has_breakfast = 'has_breakfast' in request.POST
        hotel.has_heater = 'has_heater' in request.POST
        hotel.has_pool = 'has_pool' in request.POST
        hotel.is_active = 'is_active' in request.POST

        # Owner / Ops Contact Update
        hotel.owner_name = request.POST.get('owner_name', hotel.owner_name).strip()
        hotel.owner_phone = request.POST.get('owner_phone', hotel.owner_phone).strip()
        hotel.owner_email = request.POST.get('owner_email', hotel.owner_email).strip()
        hotel.commission_percentage = request.POST.get('commission_percentage', hotel.commission_percentage)

        # Cover Photo Change if uploaded
        if 'cover_image' in request.FILES:
            cover_file = request.FILES['cover_image']
            media_item = HotelMedia.objects.create(hotel=hotel, image_file=cover_file, caption="Updated Cover")
            hotel.cover_image = media_item.image_file.url
        elif request.POST.get('cover_image_url'):
            hotel.cover_image = request.POST.get('cover_image_url')

        # Additional Bulk Photos if selected
        gallery_files = request.FILES.getlist('gallery_photos')
        for f in gallery_files:
            HotelMedia.objects.create(hotel=hotel, image_file=f, caption="Gallery Upload")

        hotel.save()
        messages.success(request, f"Updated details for '{hotel.name}' successfully!")
        return redirect('portal_hotels')

    # Pehle room ka base price fetch karke input me populate karna
    first_room = hotel.room_types.first()
    current_price = first_room.base_price if first_room else 1800.00

    return render(request, 'custom_admin/hotel_add.html', {
        'hotel': hotel,
        'is_edit': True,
        'current_price': current_price
    })


# Delete hotel
@user_passes_test(is_admin, login_url='portal_login')
def portal_hotel_delete(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id)
    hotel_name = hotel.name
    hotel.delete()
    messages.success(request, f"Property '{hotel_name}' has been deleted.")
    return redirect('portal_hotels')


# Bookings view
@login_required(login_url='portal_login')
def portal_bookings(request):
    bookings = Booking.objects.select_related('hotel', 'room_type', 'meal_plan').order_by('-created_at')
    return render(request, 'custom_admin/bookings.html', {'bookings': bookings})


# Quick booking status toggle
@login_required(login_url='portal_login')
def portal_booking_status_change(request, booking_id, new_status):
    booking = get_object_or_404(Booking, booking_id=booking_id)
    valid_statuses = ['PENDING', 'CONFIRMED', 'CHECKED_IN', 'CHECKED_OUT', 'CANCELLED']
    upper_status = new_status.upper()
    if upper_status in valid_statuses:
        booking.status = upper_status
        booking.save()
        if upper_status == 'CONFIRMED' and booking.guest_email:
            subject = f"Booking Confirmed! MakesMyYatra Pass #{booking.booking_id}"
            message = f"""Dear {booking.guest_name},

Great news! Your mountain stay reservation has been officially CONFIRMED.
==============================================
             BOOKING CONFIRMATION
==============================================
Reservation ID : {booking.booking_id}
Hotel Name     : {booking.hotel.name if booking.hotel else 'MakesMyYatra Stay'}
Destination    : {booking.hotel.city if booking.hotel else 'Himachal Pradesh'}
Check-In Date  : {booking.check_in}
Check-Out Date : {booking.check_out}
Amount Payable : Rs. {booking.total_amount} (Pay at Hotel)
Status         : CONFIRMED
==============================================
MakesMyYatra 24x7 Operations Desk: +91 98160XXXXX
"""
            try:
                send_mail(
                    subject,
                    message,
                    settings.DEFAULT_FROM_EMAIL,
                    [booking.guest_email],
                    fail_silently=True,
                )
                messages.success(request, f"Booking {booking.booking_id} confirmed and email sent!")
            except Exception:
                messages.success(request, f"Booking {booking.booking_id} status updated to {upper_status}.")
        else:
            messages.success(request, f"Booking {booking.booking_id} status changed to {upper_status}.")
    return redirect('portal_bookings')

# Booking status update via form
@login_required(login_url='portal_login')
def portal_booking_status(request, booking_id):
    if request.method == 'POST':
        booking = get_object_or_404(Booking, id=booking_id)
        new_status = request.POST.get('status')
        if new_status:
            booking.status = new_status.upper()
            booking.save()
            messages.success(request, f"Booking #{booking.booking_id} updated to {booking.status}.")
    return redirect('portal_bookings')

# Edit booking details
@login_required(login_url='portal_login')
def portal_booking_update(request, booking_id):
    booking = get_object_or_404(Booking, booking_id=booking_id)
    if request.method == 'POST':
        form = BookingStatusForm(request.POST, instance=booking)
        if form.is_valid():
            form.save()
            messages.success(request, f'Booking {booking.booking_id} updated successfully.')
            return redirect('portal_bookings')
    else:
        form = BookingStatusForm(instance=booking)
    return render(request, 'custom_admin/booking_update.html', {'form': form, 'booking': booking})

# Printable Tax Voucher View
def booking_voucher(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related('hotel', 'room_type', 'meal_plan'),
        booking_id=booking_id
    )
    return render(request, 'booking_voucher.html', {'booking': booking})

# Export bookings report to Excel
@login_required(login_url='portal_login')
def export_bookings_excel(request):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MakesMyYatra Bookings"

    headers = [
        "Booking ID", "Guest Name", "Phone", "Email",
        "Hotel Name", "City", "Check-in", "Check-out",
        "Base Amount", "GST Amount", "Grand Total (INR)",
        "Payment Mode", "Status", "Booked At"
    ]
    ws.append(headers)

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="051322", end_color="051322", fill_type="solid")
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    bookings = Booking.objects.select_related('hotel', 'room_type').all().order_by('-created_at')
    for b in bookings:
        ws.append([
            str(b.booking_id),
            b.guest_name,
            b.guest_phone or "N/A",
            b.guest_email or "N/A",
            b.hotel.name if b.hotel else "N/A",
            b.hotel.city if b.hotel else "N/A",
            str(b.check_in),
            str(b.check_out),
            float(b.base_amount) if b.base_amount else 0.0,
            float(b.gst_amount) if b.gst_amount else 0.0,
            float(b.total_amount) if b.total_amount else 1800.0,
            b.payment_mode or "Pay at Hotel",
            str(b.status).upper(),
            b.created_at.strftime("%d-%m-%Y %H:%M") if hasattr(b, 'created_at') and b.created_at else "N/A"
        ])

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=MakesMyYatra_Bookings_Report.xlsx'
    wb.save(response)
    return response

# Emergency helpdesk list
@login_required(login_url='portal_login')
def portal_urgent_help(request):
    supports = EmergencySupport.objects.all().order_by('-created_at')
    return render(request, 'custom_admin/urgent_help.html', {'supports': supports})

# Resolve support ticket
@login_required(login_url='portal_login')
def portal_urgent_help_resolve(request, support_id):
    item = get_object_or_404(EmergencySupport, id=support_id)
    item.status = 'resolved'
    item.save()
    messages.success(request, f"Urgent request from {item.name} marked as resolved.")
    return redirect('portal_urgent_help')

# User personal dashboard
@login_required(login_url='login')
def user_dashboard(request):
    bookings = Booking.objects.filter(user=request.user).order_by('-created_at')
    context = {
        'total_bookings': bookings.count(),
        'confirmed_bookings': bookings.filter(payment_status='CONFIRMED').count(),
        'pending_bookings': bookings.filter(payment_status='PENDING').count(),
        'bookings': bookings,
    }
    return render(request, 'user_dashboard.html', context)


# Static taxi page
def taxi(request):
    return render(request, 'taxi.html')

# Operations command dashboard
@login_required
def admin_operations_dashboard(request):
    today = timezone.localdate()
    bookings = Booking.objects.all().order_by('-created_at')

    today_checkins = Booking.objects.filter(check_in=today, status__in=['CONFIRMED', 'CHECKED_IN']).count()
    today_checkouts = Booking.objects.filter(check_out=today, status='CHECKED_IN').count()
    new_bookings_today = Booking.objects.filter(created_at__date=today).count()
    total_revenue = Booking.objects.filter(
        status__in=['CONFIRMED', 'CHECKED_IN', 'CHECKED_OUT']
    ).aggregate(total=Sum('base_amount'))['total'] or 0

    status_filter = request.GET.get('status')
    search_query = request.GET.get('q')

    if status_filter and status_filter != 'ALL':
        bookings = bookings.filter(status=status_filter)

    if search_query:
        bookings = bookings.filter(
            Q(booking_id__icontains=search_query) |
            Q(guest_name__icontains=search_query) |
            Q(guest_phone__icontains=search_query) |
            Q(hotel__name__icontains=search_query)
        )

    context = {
        'today': today,
        'today_checkins': today_checkins,
        'today_checkouts': today_checkouts,
        'new_bookings_today': new_bookings_today,
        'total_revenue': total_revenue,
        'bookings': bookings[:50],
        'selected_status': status_filter or 'ALL',
        'search_query': search_query or '',
    }
    return render(request, 'admin/operations_dashboard.html', context)

# Authenticated User Booking Details
@login_required(login_url='login')
def booking_detail(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related('hotel', 'room_type'),
        booking_id=booking_id,
        user=request.user
    )
    return render(request, 'booking_detail.html', {'booking': booking})

# Public Booking Tracking Page
def track_booking(request):
    booking = None
    query_id = (request.POST.get('booking_id') or request.GET.get('booking_id') or '').strip()
    contact_info = (request.POST.get('contact_info') or request.GET.get('contact_info') or '').strip()

    if query_id:
        clean_id = query_id.replace('#', '').strip()
        booking = Booking.objects.select_related('hotel', 'room_type').filter(
            booking_id__iexact=clean_id
        ).first()

        if not booking and clean_id.isdigit():
            booking = Booking.objects.select_related('hotel', 'room_type').filter(
                id=int(clean_id)
            ).first()

        if booking and contact_info:
            phone_match = bool(booking.guest_phone and contact_info in booking.guest_phone)
            email_match = bool(booking.guest_email and contact_info.lower() == booking.guest_email.lower())
            if not (phone_match or email_match):
                booking = None
                messages.error(request, "Booking reference found, but the provided phone number or email does not match.")

        if not booking and not messages.get_messages(request):
            messages.error(request, f"No reservation found for Booking ID '{query_id}'. Please check your details and try again.")

    return render(request, 'track_booking.html', {
        'booking': booking,
        'query_id': query_id,
        'contact_info': contact_info
    })

# Hotel partner portal login
def partner_login(request):
    if request.user.is_authenticated:
        return redirect('partner_dashboard')
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user is not None:
            login(request, user)
            return redirect('partner_dashboard')
        else:
            messages.error(request, 'Invalid hotel partner credentials.')
    return render(request, 'partner/login.html')

# Hotel partner operations dashboard
@login_required(login_url='partner_login')
def partner_dashboard(request):
    partner_hotels = Hotel.objects.filter(owner=request.user)
    if not partner_hotels.exists() and request.user.is_staff:
        partner_hotels = Hotel.objects.all()[:1]
    bookings = Booking.objects.filter(hotel__in=partner_hotels).select_related('hotel').order_by('-created_at')
    partner_revenue = bookings.filter(payment_status='CONFIRMED').aggregate(Sum('total_amount'))['total_amount__sum'] or 0

    context = {
        'hotels': partner_hotels,
        'bookings': bookings,
        'total_bookings': bookings.count(),
        'partner_revenue': partner_revenue,
    }
    return render(request, 'partner/dashboard.html', context)

# NEW MODULES: REVIEWS, RATINGS & MEDIA GALLERY MANAGEMENT

# 1. Public Review Submission
def submit_hotel_review(request, hotel_id):
    if request.method == 'POST':
        hotel = get_object_or_404(Hotel, id=hotel_id)
        guest_name = request.POST.get('guest_name', 'Verified Guest')
        rating_val = request.POST.get('rating', '5')
        comment = request.POST.get('comment', '').strip()

        try:
            rating = max(1, min(5, int(rating_val)))
        except ValueError:
            rating = 5

        if comment:
            HotelReview.objects.create(
                hotel=hotel,
                guest_name=guest_name,
                rating=rating,
                comment=comment,
                is_approved=True
            )
            messages.success(request, f"Thank you, {guest_name}! Your review has been submitted.")
    return redirect('index')

# 2. Admin Reviews List & Moderation
@user_passes_test(is_admin, login_url='portal_login')
def portal_reviews(request):
    reviews = HotelReview.objects.select_related('hotel').order_by('-created_at')
    return render(request, 'custom_admin/reviews.html', {'reviews': reviews})

# 3. Admin Review Visibility Toggle
@user_passes_test(is_admin, login_url='portal_login')
def portal_review_toggle(request, review_id):
    review = get_object_or_404(HotelReview, id=review_id)
    review.is_approved = not review.is_approved
    review.save()
    messages.success(request, f"Review #{review.id} visibility updated.")
    return redirect('portal_reviews')

# 4. Admin Review Delete
@user_passes_test(is_admin, login_url='portal_login')
def portal_review_delete(request, review_id):
    review = get_object_or_404(HotelReview, id=review_id)
    review.delete()
    messages.success(request, "Review deleted successfully.")
    return redirect('portal_reviews')

# 5. Media Gallery View for a Hotel
def hotel_gallery(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id)
    media_items = hotel.media_gallery.all().order_by('-created_at')
    return render(request, 'hotel_gallery.html', {'hotel': hotel, 'media_items': media_items})

def is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)

# Multiple photo upload
@user_passes_test(is_admin, login_url='portal_login')
def portal_hotel_gallery_manage(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id)

    if request.method == 'POST':
        uploaded_files = request.FILES.getlist('gallery_photos')
        
        if uploaded_files:
            count = 0
            for f in uploaded_files:
                HotelMedia.objects.create(
                    hotel=hotel,
                    image_file=f,
                    caption=hotel.name
                )
                count += 1
            messages.success(request, f"Total {count} photos uploaded successfully to the gallery!")
        else:
            messages.warning(request, "Please select at least one photo to upload.")

        return redirect('portal_hotel_gallery_manage', hotel_id=hotel.id)

    media_items = hotel.media_gallery.all().order_by('-created_at')
    return render(request, 'custom_admin/gallery_manage.html', {
        'hotel': hotel,
        'media_items': media_items
    })

# Single photo delete action
@user_passes_test(is_admin, login_url='portal_login')
def portal_gallery_image_delete(request, media_id):
    media = get_object_or_404(HotelMedia, id=media_id)
    hotel_id = media.hotel.id
    if media.image_file:
        media.image_file.delete(save=False) 
    media.delete()
    messages.success(request, "Photo deleted successfully!")
    return redirect('portal_hotel_gallery_manage', hotel_id=hotel_id)

def portal_room_manage(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id)
    rooms = hotel.room_types.all().order_by('id')
    return render(request, 'custom_admin/room_manage.html', {
        'hotel': hotel,
        'rooms': rooms,
    })

def portal_room_create(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id)
    if request.method == 'POST':
        name = request.POST.get('name', 'Deluxe Room')
        max_adults = request.POST.get('max_adults', 2)
        max_children = request.POST.get('max_children', 1)
        base_price = request.POST.get('base_price', 1800.00)
        weekend_price = request.POST.get('weekend_price', 2200.00)
        festival_price = request.POST.get('festival_price', 2800.00)
        extra_bed_charge = request.POST.get('extra_bed_charge', 500.00)
        total_inventory = request.POST.get('total_inventory', 5)

        RoomType.objects.create(
            hotel=hotel,
            name=name,
            max_adults=max_adults,
            max_children=max_children,
            base_price=base_price,
            weekend_price=weekend_price,
            festival_price=festival_price,
            extra_bed_charge=extra_bed_charge,
            total_inventory=total_inventory,
            is_open=True,
            is_available=True
        )
        messages.success(request, f"New room type '{name}' added successfully!")
    return redirect('portal_room_manage', hotel_id=hotel.id)

def portal_room_delete(request, hotel_id, room_id):
    room = get_object_or_404(RoomType, id=room_id, hotel_id=hotel_id)
    room.delete()
    messages.success(request, "Room category removed.")
    return redirect('portal_room_manage', hotel_id=hotel_id)
from django.template.loader import render_to_string

# 1. Tax Invoice Print/Download View (Dual-Copy: Customer & Hotel)
@login_required(login_url='portal_login')
def portal_booking_invoice(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related('hotel', 'room_type', 'meal_plan'),
        booking_id=booking_id
    )
    # Calculate nights breakdown
    nights = 1
    if booking.check_in and booking.check_out:
        nights = max((booking.check_out - booking.check_in).days, 1)
    context = {
        'booking': booking,
        'hotel': booking.hotel,
        'nights': nights,
        'invoice_number': f"INV-{booking.booking_id}",
        'invoice_date': booking.created_at.date() if hasattr(booking, 'created_at') else timezone.localdate(),
    }
    return render(request, 'custom_admin/invoice_print.html', context)

# 2. Click Instant Room Toggle (Open/Close Room for Bookings)
@user_passes_test(is_admin, login_url='portal_login')
def portal_room_toggle_status(request, room_id):
    room = get_object_or_404(RoomType, id=room_id)
    room.is_open = not room.is_open
    room.is_available = room.is_open
    room.save()
    status_label = "OPEN" if room.is_open else "BLOCKED / CLOSED"
    messages.success(request, f"Inventory status for '{room.name}' updated to {status_label}.")
    return redirect('portal_room_manage', hotel_id=room.hotel.id)

import urllib.parse
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q

from .models import Booking
from .notifications import (
    send_booking_sms,
    send_booking_email,
    get_whatsapp_booking_text
)


@login_required(login_url='portal_login')
def portal_booking_send_whatsapp(request, booking_id):
    """
    Triggers all 3 communication channels at once:
    1. Normal SMS to SIM card
    2. Official Email confirmation
    3. Redirects to WhatsApp Web/App with pre-filled message
    """
    booking = get_object_or_404(
        Booking, 
        Q(booking_id=booking_id) | Q(id=booking_id if str(booking_id).isdigit() else 0)
    )

    # 1. Trigger Standard Text SMS
    sms_sent = send_booking_sms(booking)

    # 2. Trigger Confirmation Email
    email_sent = send_booking_email(booking)

    # Feedback flash message for Admin
    if sms_sent and email_sent:
        messages.success(request, f"SMS aur Email #{booking.booking_id} ke liye bhej diye gaye hain!")
    elif email_sent:
        messages.info(request, f"Email dispatch ho gaya hai (#{booking.booking_id}).")
    elif sms_sent:
        messages.info(request, f"SMS dispatch ho gaya hai (#{booking.booking_id}).")

    # 3. Format Phone & Message for WhatsApp Redirect
    raw_phone = str(booking.guest_phone).strip().replace(" ", "").replace("-", "").replace("+", "")
    
    if raw_phone.startswith("0"):
        raw_phone = raw_phone[1:]
        
    if len(raw_phone) == 10:
        clean_phone = f"91{raw_phone}"
    elif raw_phone.startswith("91"):
        clean_phone = raw_phone
    else:
        clean_phone = f"91{raw_phone}"

    text_msg = get_whatsapp_booking_text(booking)
    encoded_text = urllib.parse.quote(text_msg)

    # Open WhatsApp chat
    whatsapp_url = f"https://api.whatsapp.com/send?phone={clean_phone}&text={encoded_text}"
    return redirect(whatsapp_url)