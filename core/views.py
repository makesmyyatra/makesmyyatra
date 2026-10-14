import uuid
import openpyxl
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
from .models import Hotel, RoomType, MealPlan, Booking, EmergencySupport
from .forms import HotelForm, RoomTypeForm, BookingStatusForm

# Helper to verify admin permissions
def is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)

# Pricing and GST slab calculator
def calculate_stay_pricing(base_price, meal_extra=0, nights=1, rooms=1, discount=0):
    base_price = Decimal(str(base_price))
    meal_extra = Decimal(str(meal_extra))
    discount = Decimal(str(discount))
    nights = int(nights) if int(nights) > 0 else 1
    rooms = int(rooms) if int(rooms) > 0 else 1

    nightly_rate = base_price + meal_extra

    if nightly_rate < 1000:
        gst_rate = Decimal('0.00')
    elif nightly_rate <= 7500:
        gst_rate = Decimal('0.12')
    else:
        gst_rate = Decimal('0.18')

    taxable_amount = max(Decimal('0.00'), (nightly_rate * nights * rooms) - discount)
    total_gst = (taxable_amount * gst_rate).quantize(Decimal('0.01'))
    cgst = (total_gst / 2).quantize(Decimal('0.01'))
    sgst = (total_gst / 2).quantize(Decimal('0.01'))
    grand_total = (taxable_amount + total_gst).quantize(Decimal('0.01'))

    return {
        'nightly_rate': nightly_rate,
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
    hotels = Hotel.objects.filter(is_active=True).prefetch_related('room_types')

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

# Booking engine with dynamic pricing, meal plan, and GST
def book_hotel(request):
    if request.method == 'POST':
        hotel_id = request.POST.get('hotel_id')
        room_type_id = request.POST.get('room_type_id')
        meal_plan_id = request.POST.get('meal_plan_id')
        guest_name = request.POST.get('guest_name', 'Guest')
        guest_phone = request.POST.get('guest_phone', '')
        guest_email = request.POST.get('guest_email', '')
        check_in_str = request.POST.get('check_in')
        check_out_str = request.POST.get('check_out')

        hotel = get_object_or_404(Hotel, id=hotel_id)
        room_type = RoomType.objects.filter(id=room_type_id).first() if room_type_id else hotel.room_types.first()
        base_rate = room_type.base_price if room_type else Decimal('1800.00')

        meal_plan = MealPlan.objects.filter(id=meal_plan_id, room_type=room_type).first() if meal_plan_id else None
        meal_extra = meal_plan.additional_price if meal_plan else Decimal('0.00')

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

        # Compute bill with auto GST
        bill = calculate_stay_pricing(
            base_price=base_rate,
            meal_extra=meal_extra,
            nights=nights,
            rooms=1,
            discount=0
        )

        custom_booking_id = f"MMY-HTL-{uuid.uuid4().hex[:4].upper()}"

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
        return render(request, 'booking_success.html', {
            'booking': booking,
            'hotel': hotel,
            'guest_name': guest_name,
            'guest_phone': guest_phone,
            'guest_email': guest_email,
            'check_in': check_in_str,
            'check_out': check_out_str,
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

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import Booking

# 1. Clear-All Actions
def portal_booking_actions(request):
    if request.method == "POST":
        action = request.POST.get("action")
        
        # Delete Selected Items
        if action == "delete_selected":
            selected_ids = request.POST.getlist("selected_bookings")
            if selected_ids:
                count = Booking.objects.filter(id__in=selected_ids).delete()[0]
                messages.success(request, f"{count} selected bookings deleted successfully.")
            else:
                messages.warning(request, "You haven't selected any bookings.")
                
        #  Clear All Bookings
        elif action == "delete_all":
            count = Booking.objects.count()
            Booking.objects.all().delete()
            messages.success(request, f"Saari {count} bookings deleted successfully!")
            
    return redirect('portal_bookings')

# 2. Individual Booking Deletion
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
    hotels = Hotel.objects.all().prefetch_related('room_types').order_by('-created_at')
    return render(request, 'custom_admin/hotel_list.html', {'hotels': hotels})

# Add hotel
@user_passes_test(is_admin, login_url='portal_login')
def portal_hotel_add(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        city = request.POST.get('city', '').strip()
        category = request.POST.get('property_type', 'budget')
        star_rating = int(request.POST.get('star_rating', 3))
        address = request.POST.get('address', '').strip()
        price_val = request.POST.get('base_price', '1800')
        submitted_img = request.POST.get('image_url') or request.POST.get('cover_image')
        default_cover = "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=800&q=80"
        cover_image = submitted_img.strip() if submitted_img and submitted_img.strip() else default_cover

        hotel = Hotel.objects.create(
            name=name,
            city=city,
            category=category,
            star_rating=star_rating,
            address=address,
            cover_image=cover_image,
            has_wifi=True,
            has_parking=True,
            has_ac=True,
            has_heater=True,
            is_active=True
        )
        clean_price = Decimal(str(price_val).strip()) if price_val else Decimal('1800.00')
        RoomType.objects.create(
            hotel=hotel,
            name="Standard Room",
            base_price=clean_price,
            max_guests=2,
            is_open=True
        )
        messages.success(request, f"Property '{name}' published successfully!")
        return redirect('portal_hotels')
    return render(request, 'custom_admin/hotel_add.html', {'is_edit': False, 'current_price': ''})

# Edit hotel
@user_passes_test(is_admin, login_url='portal_login')
def portal_hotel_edit(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id)
    room = hotel.room_types.first()
    if request.method == 'POST':
        hotel.name = request.POST.get('name')
        hotel.city = request.POST.get('city')
        hotel.category = request.POST.get('property_type', 'budget')
        hotel.star_rating = int(request.POST.get('star_rating', 3))
        hotel.address = request.POST.get('address')
        if request.POST.get('cover_image'):
            hotel.cover_image = request.POST.get('cover_image')
        hotel.save()
        price_val = request.POST.get('base_price')
        if price_val:
            clean_price = Decimal(str(price_val).strip())
            if not room:
                room = RoomType.objects.create(hotel=hotel, name="Standard Room", base_price=clean_price)
            else:
                room.base_price = clean_price
                room.save()
        messages.success(request, f"Property '{hotel.name}' updated successfully!")
        return redirect('portal_hotels')
    current_price = room.base_price if room else ''
    return render(request, 'custom_admin/hotel_add.html', {
        'hotel': hotel,
        'current_price': current_price,
        'is_edit': True
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

        # 1. Look up by custom booking_id (case-insensitive)
        booking = Booking.objects.select_related('hotel', 'room_type').filter(
            booking_id__iexact=clean_id
        ).first()

        # 2. Fallback to primary key if input is numeric
        if not booking and clean_id.isdigit():
            booking = Booking.objects.select_related('hotel', 'room_type').filter(
                id=int(clean_id)
            ).first()

        # 3. Security verification (Phone or Email match) if contact info is provided
        if booking and contact_info:
            phone_match = bool(booking.guest_phone and contact_info in booking.guest_phone)
            email_match = bool(booking.guest_email and contact_info.lower() == booking.guest_email.lower())
            
            if not (phone_match or email_match):
                booking = None
                messages.error(request, "Booking reference found, but the provided phone number or email does not match.")

        # 4. Handle reservation not found
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