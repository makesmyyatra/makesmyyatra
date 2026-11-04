import io
from xhtml2pdf import pisa
import uuid
import openpyxl
import urllib.parse
from django.db import models
from django.db.models import Sum
from django.contrib.admin.models import LogEntry 
from django.contrib.auth.models import Group, User
from core.models import Booking, Coupon, EmergencySupport, Hotel, MealPlan, RoomType
from decimal import Decimal
from datetime import datetime, date
from .utils import purge_expired_emergency_requests
from openpyxl.styles import Font, PatternFill, Alignment
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Q, Count, Max
from django.core.mail import send_mail
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from .models import Hotel, RoomType, MealPlan, Booking, EmergencySupport, HotelReview, HotelMedia, Coupon
from .forms import HotelForm, RoomTypeForm, BookingStatusForm
from django.template.loader import get_template
from .utils import calculate_stay_pricing, partner_cancel_booking_logic, get_partner_booking_metrics
from datetime import datetime, timedelta
from .notifications import (
    send_booking_sms,
    send_booking_email,
    get_whatsapp_booking_text,
    send_whatsapp_notification
)

# Helper to verify admin permissions
def is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)

# Homepage and search filter
def index(request):
    city = request.GET.get('city', 'All')
    max_price = request.GET.get('max_price', '')
    property_type = request.GET.get('property_type', '')
    min_rating = request.GET.get('min_rating', '')

    # NEW: is_verified=True add kar diya taaki fake/unapproved properties public homepage par na dikhein
    hotels = Hotel.objects.filter(is_active=True, is_verified=True).prefetch_related('room_types', 'reviews', 'media_gallery')

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

# Booking engine with dynamic pricing, meal plan, auto offers, GST & direct voucher redirect
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
        adults_count = int(request.POST.get('adults_count', 2))
        children_count = int(request.POST.get('children_count', 0))
        rooms_count = int(request.POST.get('rooms_count', 1))
        adults_count = int(request.POST.get('adults_count', 2))
        children_count = int(request.POST.get('children_count', 0))
        meal_plan = request.POST.get('meal_plan', 'EP')
        total_guests = adults_count + children_count if (adults_count + children_count) > 0 else 2

        hotel = get_object_or_404(Hotel, id=hotel_id)
        room_type = RoomType.objects.filter(id=room_type_id).first() if room_type_id else hotel.room_types.first()
        base_rate = room_type.base_price if room_type else Decimal('1800.00')

        # Flexible Meal Plan Resolution
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
                code_upper = cleaned_val.upper()
                meal_extra = fallback_rates.get(code_upper, Decimal('0.00'))
                # if room_type exists, create a new MealPlan entry dynamically
                if room_type:
                    meal_plan, _ = MealPlan.objects.get_or_create(
                        room_type=room_type,
                        plan_code=code_upper,
                        defaults={'additional_price': meal_extra, 'is_active': True}
                    )
        # Calculate stay duration
        check_in_date = None
        check_out_date = None
        nights = 1
        if check_in_str and check_out_str:
            try:
                check_in_date = datetime.strptime(check_in_str, '%Y-%m-%d').date()
                check_out_date = datetime.strptime(check_out_str, '%Y-%m-%d').date()
                nights = max((check_out_date - check_in_date).days, 1)
            except ValueError:
                pass

        # --- Automatic Offer & Dynamic DB Coupon Evaluation ---
        discount_percentage = Decimal('0.00')
        flat_discount_amount = Decimal('0.00')
        offer_name = "Standard Pricing"
        applied_coupon_obj = None

        # 1. Early Bird Offer: Booking made >= 7 days before check-in (10% OFF)
        if check_in_date and (check_in_date - timezone.localdate()).days >= 7:
            discount_percentage = Decimal('10.00')
            offer_name = "Early Bird Offer (10% OFF)"

        # 2. Long Stay Offer: Booking stay >= 3 nights (15% OFF)
        if nights >= 3:
            if Decimal('15.00') > discount_percentage:
                discount_percentage = Decimal('15.00')
                offer_name = "Long Stay Offer (15% OFF)"

        # 3. Dynamic Database Coupon Lookup
        coupon_input = (request.POST.get('coupon_code') or '').strip()
        if coupon_input:
            coupon_match = Coupon.objects.filter(code__iexact=coupon_input, is_active=True).first()
            today = timezone.localdate()
            if coupon_match and (coupon_match.valid_till is None or coupon_match.valid_till >= today):
                applied_coupon_obj = coupon_match
                if coupon_match.discount_type == 'flat':
                    flat_discount_amount = coupon_match.discount_value
                    discount_percentage = Decimal('0.00')
                    offer_name = f"Coupon {coupon_match.code} (Rs. {coupon_match.discount_value} OFF)"
                else:
                    if coupon_match.discount_value > discount_percentage:
                        discount_percentage = coupon_match.discount_value
                        offer_name = f"Coupon {coupon_match.code} ({coupon_match.discount_value}% OFF)"
            else:
                messages.warning(request, f"Coupon '{coupon_input}' is either invalid or expired.")

        # Compute bill via calculator (with weekend surge engine)
        rooms_count = 1
        bill = calculate_stay_pricing(
            base_price=base_rate,
            meal_extra=meal_extra,
            check_in=check_in_date,
            check_out=check_out_date,
            rooms=rooms_count,
            discount_percent=discount_percentage,
            adults=adults_count,
            children=children_count,
            flat_discount=flat_discount_amount,
            surge_percent=20  # 20% surge for Friday & Saturday nights
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
            guests_count=total_guests,
            adults_count=adults_count,
            children_count=children_count,
            coupon=applied_coupon_obj,
            base_amount=bill['pure_room_base'],             # Reflects surge pricing
            meal_plan_amount=bill['total_meal_amount'],     
            discount_amount=bill['discount_amount'],
            cgst_amount=bill['cgst'],
            sgst_amount=bill['sgst'],
            gst_amount=bill['total_gst'],
            total_amount=bill['grand_total'],
            rooms_count=rooms_count,
            payment_mode='PAY_AT_HOTEL',
            payment_status='CONFIRMED',
            status='CONFIRMED',
            transaction_id=f"TXN-{uuid.uuid4().hex[:8].upper()}"
        )
        # 2. Multi-channel alerts dispatch (Safe try-except block)
        try:
            send_booking_sms(booking)
            try:
                send_booking_email(booking)
            except Exception as email_err:
                print(f"Skipping email dispatch due to network timeout: {email_err}")
            send_whatsapp_notification(booking)
        except Exception as sms_err:
            pass
        # 3. Direct Redirect to Printable Tax Voucher / Invoice
        messages.success(request, f"Booking #{booking.booking_id} confirmed! Here is your official travel pass.")
        return redirect('booking_voucher', booking_id=booking.booking_id)
    return redirect('index')


def is_admin(user):
    return user.is_staff or user.is_superuser
# Helpdesk and traveler support (Guest Facing Form)
def support_request(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        location = request.POST.get('location', '').strip()
        details = request.POST.get('details') or request.POST.get('issue', '')

        if not name or not phone:
            messages.error(request, 'Please provide both your name and contact phone number.')
            return redirect('support_request')

        EmergencySupport.objects.create(
            name=name,
            phone=phone,
            location=location,
            issue_details=details,
            status='PENDING'
        )
        messages.success(request, 'Your emergency assistance request has been received. Our team will contact you shortly.')
        return redirect('support_request')
        
    return render(request, 'traveler_help.html')

@login_required
@user_passes_test(is_admin)
def portal_urgent_help(request):
    purge_expired_emergency_requests(retention_days=30)

    current_filter = request.GET.get('filter', 'all').strip().lower()

    if current_filter == 'resolved':
        # Sirf Resolved (case-insensitive)
        supports = EmergencySupport.objects.filter(status__iexact='RESOLVED').order_by('-resolved_at', '-created_at')
    elif current_filter == 'pending':
        # Sirf Pending / Action Needed (case-insensitive)
        supports = EmergencySupport.objects.exclude(status__iexact='RESOLVED').order_by('-created_at')
    else:
        current_filter = 'all'
        supports = EmergencySupport.objects.all().order_by('-created_at')

    total_count = EmergencySupport.objects.count()
    resolved_count = EmergencySupport.objects.filter(status__iexact='RESOLVED').count()
    pending_count = EmergencySupport.objects.exclude(status__iexact='RESOLVED').count()

    return render(request, 'custom_admin/urgent_help.html', {
        'supports': supports,
        'current_filter': current_filter,
        'total_count': total_count,
        'resolved_count': resolved_count,
        'pending_count': pending_count,
    })


@login_required
@user_passes_test(is_admin)
def portal_urgent_help_resolve(request, support_id):
    # Hamesha standard uppercase 'RESOLVED' save hoga
    EmergencySupport.objects.filter(id=support_id).update(
        status='RESOLVED',
        resolved_at=timezone.now()
    )
    
    current_filter = request.GET.get('filter') or request.POST.get('filter') or 'all'
    messages.success(request, f"Ticket #{support_id} marked as RESOLVED.")
    return redirect(f"/portal/urgent-help/?filter={current_filter}")

# 3. Manual Immediate Delete Action View (Spam / Instant Trash)
@login_required
@user_passes_test(is_admin)
def portal_urgent_help_delete(request, support_id):
    req_item = get_object_or_404(EmergencySupport, id=support_id)
    guest_display_name = getattr(req_item, 'name', None) or getattr(req_item, 'guest_name', 'Guest')
    
    req_item.delete()
    messages.success(request, f"Emergency ticket #{support_id} for {guest_display_name} deleted successfully.")
    return redirect('portal_urgent_help')

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
@user_passes_test(is_admin, login_url="portal_login")
def portal_dashboard(request):
  # Metric Counts
  total_revenue = (
      Booking.objects.filter(status__in=["CONFIRMED", "CHECKED_OUT"]).aggregate(
          Sum("total_amount")
      )["total_amount__sum"]
      or Decimal("0.00")
  )
  total_bookings = Booking.objects.count()
  total_hotels = Hotel.objects.count()
  total_coupons = Coupon.objects.filter(is_active=True).count()
  active_supports = EmergencySupport.objects.filter(status='open').count()

  # Today's Traffic
  today = timezone.localdate()
  today_checkins = Booking.objects.filter(check_in=today).count()
  today_checkouts = Booking.objects.filter(check_out=today).count()
  new_bookings_today = Booking.objects.filter(created_at__date=today).count()

  # Pipeline
  pipeline = {
      "pending": Booking.objects.filter(status="PENDING").count(),
      "confirmed": Booking.objects.filter(status="CONFIRMED").count(),
      "checked_in": Booking.objects.filter(status="CHECKED_IN").count(),
      "checked_out": Booking.objects.filter(status="CHECKED_OUT").count(),
  }

  # Recent Bookings Table
  recent_bookings = Booking.objects.select_related(
      "hotel", "room_type"
  ).order_by("-id")[:6]

  # 1. Quick Master Tables Inventory Summary (Jaise Django Admin me hota hai)
  catalog_summary = {
      "users": User.objects.count(),
      "groups": Group.objects.count(),
      "hotels": total_hotels,
      "room_types": RoomType.objects.count(),
      "meal_plans": MealPlan.objects.count(),
      "bookings": total_bookings,
      "coupons": Coupon.objects.count(),
      "supports": EmergencySupport.objects.count(),
  }

  # 2. Django Built-in Audit Logs (Recent Actions History)
  recent_actions = LogEntry.objects.select_related(
      "content_type", "user"
  ).order_by("-action_time")[:8]

  context = {
      "revenue": total_revenue,
      "total_bookings": total_bookings,
      "total_hotels": total_hotels,
      "total_coupons": total_coupons,
      "active_supports": active_supports,
      "today_date": today,
      "today_checkins": today_checkins,
      "today_checkouts": today_checkouts,
      "new_bookings_today": new_bookings_today,
      "pipeline": pipeline,
      "cancellations_count": Booking.objects.filter(status="CANCELLED").count(),
      "bookings": recent_bookings,
      "catalog": catalog_summary,
      "recent_actions": recent_actions,
  }
  return render(request, "custom_admin/dashboard.html", context)

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
    hotels = Hotel.objects.all().order_by('-id')
    return render(request, 'custom_admin/hotel_list.html', {'hotels': hotels})


@user_passes_test(is_admin, login_url='portal_login')
def portal_all_rooms(request):
    rooms = RoomType.objects.select_related('hotel').all().order_by('-id')
    return render(request, 'custom_admin/room_types.html', {
        'room_types': rooms,
    })

@user_passes_test(is_admin, login_url='portal_login')
def portal_room_manage(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id)
    rooms = hotel.room_types.all().order_by('id')
    return render(request, 'custom_admin/room_manage.html', {
        'hotel': hotel,
        'rooms': rooms,
    })

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

def booking_voucher(request, booking_id):
    lookup = Q(booking_id=booking_id)
    if str(booking_id).isdigit():
        lookup |= Q(id=int(booking_id))

    booking = get_object_or_404(
        Booking.objects.select_related('hotel', 'room_type', 'meal_plan'),
        lookup
    )

    # Calculate nights
    nights = 1
    if booking.check_in and booking.check_out:
        delta = (booking.check_out - booking.check_in).days
        nights = max(delta, 1)

    # Invoice / Voucher reference data
    invoice_number = f"INV-{booking.booking_id}"
    invoice_date = booking.created_at.date() if booking.created_at else date.today()

    context = {
        'booking': booking,
        'hotel': booking.hotel,
        'nights': nights,
        'invoice_number': invoice_number,
        'invoice_date': invoice_date,
    }
    return render(request, 'custom_admin/invoice_print.html', context)

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

# 1. Partner Registration (Account Creation)

def partner_register(request):
    if request.user.is_authenticated:
        return redirect('partner_dashboard')

    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        phone = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        if password != confirm_password:
            messages.error(request, 'Passwords do not match.')
            return render(request, 'partner/register.html')

        if User.objects.filter(username=email).exists():
            messages.error(request, 'An account with this email already exists. Please login.')
            return redirect('partner_login')

        # Create Partner User
        user = User.objects.create_user(
            username=email,
            email=email,
            first_name=full_name,
            password=password
        )
        
        # Auto login after registration
        login(request, user)
        messages.success(request, 'Welcome! Now complete your property formalities.')
        return redirect('add_property')

    return render(request, 'partner/register.html')

@login_required(login_url='partner_login')
def add_property(request):
    if request.method == 'POST':
        name = request.POST.get('hotel_name', '').strip()
        city = request.POST.get('city', '').strip()
        category = request.POST.get('category', 'budget')
        star_rating = request.POST.get('star_rating', '3')
        address = request.POST.get('address', '').strip()
        price_val = request.POST.get('base_price', '1850')
        description = request.POST.get('description', '').strip()
        
        # Geolocation & Maps
        latitude = request.POST.get('latitude', '').strip()
        longitude = request.POST.get('longitude', '').strip()
        google_maps_url = request.POST.get('google_maps_url', '').strip()

        # Contact info
        owner_name = request.POST.get('owner_name', '').strip()
        owner_phone = request.POST.get('owner_phone', '').strip()
        owner_email = request.POST.get('owner_email', '').strip()

        # Amenities Booleans
        has_wifi = 'has_wifi' in request.POST
        has_ac = 'has_ac' in request.POST
        has_parking = 'has_parking' in request.POST
        has_restaurant = 'has_restaurant' in request.POST
        has_breakfast = 'has_breakfast' in request.POST
        has_heater = 'has_heater' in request.POST
        has_pool = 'has_pool' in request.POST

        # File Uploads
        verification_doc = request.FILES.get('verification_doc')
        cover_file = request.FILES.get('cover_file')
        cover_image_url = request.POST.get('cover_image', '').strip()

        if not name or not city or not address:
            messages.error(request, 'Please provide property name, destination district, and full address.')
            return render(request, 'partner/add_property.html')

        hotel_fields = [f.name for f in Hotel._meta.get_fields()]
        
        hotel_data = {
            'name': name,
            'city': city,
            'address': address,
            'owner': request.user,
        }

        # Safe attribute mapping
        if 'category' in hotel_fields:
            hotel_data['category'] = category
        if 'star_rating' in hotel_fields:
            hotel_data['star_rating'] = int(star_rating)
        if 'description' in hotel_fields:
            hotel_data['description'] = description
        if 'owner_name' in hotel_fields:
            hotel_data['owner_name'] = owner_name or request.user.username
        if 'owner_phone' in hotel_fields:
            hotel_data['owner_phone'] = owner_phone
        if 'owner_email' in hotel_fields:
            hotel_data['owner_email'] = owner_email or request.user.email

        # Amenities mapping
        for amenity_field in ['has_wifi', 'has_ac', 'has_parking', 'has_restaurant', 'has_breakfast', 'has_heater', 'has_pool']:
            if amenity_field in hotel_fields:
                hotel_data[amenity_field] = locals()[amenity_field]

        # GPS & Maps
        if latitude and 'latitude' in hotel_fields:
            hotel_data['latitude'] = latitude
        if longitude and 'longitude' in hotel_fields:
            hotel_data['longitude'] = longitude
        if google_maps_url and 'google_maps_url' in hotel_fields:
            hotel_data['google_maps_url'] = google_maps_url

        # Verification & Security (Requires admin review before going live)
        if 'is_verified' in hotel_fields:
            hotel_data['is_verified'] = False
        if verification_doc and 'verification_doc' in hotel_fields:
            hotel_data['verification_doc'] = verification_doc

        # Cover image handling
        if cover_file and 'cover_image' in hotel_fields:
            # Agar file upload support model me hai
            hotel_data['cover_image'] = cover_file
        elif cover_image_url and 'cover_image' in hotel_fields:
            hotel_data['cover_image'] = cover_image_url

        # Price check
        if 'price_per_night' in hotel_fields:
            hotel_data['price_per_night'] = price_val
        elif 'base_price' in hotel_fields:
            hotel_data['base_price'] = price_val

        # Create Hotel record
        hotel = Hotel.objects.create(**hotel_data)

        # Default standard room create karo taaki partner dashboard par rates open/close ho sakein
        try:
            from .models import RoomType
            room_fields = [f.name for f in RoomType._meta.get_fields()]
            room_data = {'hotel': hotel}
            if 'name' in room_fields:
                room_data['name'] = 'Standard Deluxe Room'
            if 'base_price' in room_fields:
                room_data['base_price'] = price_val
            elif 'price' in room_fields:
                room_data['price'] = price_val
            RoomType.objects.create(**room_data)
        except Exception:
            pass

        messages.success(request, f'Stay "{name}" successfully onboarded! Verification review is pending with the operations desk.')
        return redirect('partner_dashboard')

    return render(request, 'partner/add_property.html')

# 3. Partner Login (with Admin Protection )
def partner_login(request):
    #if user is already logged in, redirect to appropriate dashboard
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser:
            return redirect('portal_dashboard')
        return redirect('partner_dashboard')

    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user is not None:
            # if user is admin, prevent partner portal login
            if user.is_staff or user.is_superuser:
                messages.error(request, 'Cannot be used to login to the partner portal.')
                return render(request, 'partner/login.html')

            login(request, user)
            # if user has no hotel yet, redirect to add_property page
            if not Hotel.objects.filter(owner=user).exists():
                return redirect('add_property')
            return redirect('partner_dashboard')
        else:
            messages.error(request, 'Invalid credentials.')
    return render(request, 'partner/login.html')

# 4. Partner Dashboard
@login_required
def partner_dashboard(request):
    # 1. Fetch all hotels owned by the partner
    hotels = Hotel.objects.filter(owner=request.user).prefetch_related('room_types')

    # 2. Bookings fetch for the partner's hotels
    bookings = Booking.objects.filter(hotel__owner=request.user).select_related('hotel', 'room_type').order_by('-created_at')

    # 3. Active Bookings (Gross Revenue reduction logic)
    active_bookings = bookings.exclude(status__in=['CANCELLED', 'CANCELLED_BY_PARTNER', 'CANCELLED_BY_GUEST'])
    partner_revenue = active_bookings.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    # 4. Cancelled deductions
    cancelled_partner_bookings = bookings.filter(status='CANCELLED_BY_PARTNER')
    deducted_revenue = cancelled_partner_bookings.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    cancelled_count = cancelled_partner_bookings.count()

    # 5. Commission & Net Payout
    platform_commission = round(partner_revenue * Decimal('0.15'), 2)
    net_payout = partner_revenue - platform_commission

    total_bookings = bookings.count()
    active_bookings_count = active_bookings.count()

    return render(request, "partner/dashboard.html", {
        'hotels': hotels,
        'bookings': bookings,
        'total_bookings': total_bookings,
        'active_bookings_count': active_bookings_count,
        'partner_revenue': partner_revenue,
        'deducted_revenue': deducted_revenue,
        'cancelled_count': cancelled_count,
        'platform_commission': platform_commission,
        'net_payout': net_payout,
    })

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import Hotel

@login_required
def partner_edit_property(request, hotel_id):
    # Security: Ensure hotel logged-in partner ka hi ho
    hotel = get_object_or_404(Hotel, id=hotel_id, owner=request.user)
    if request.method == "POST":
        # 1. Basic Details
        hotel.name = request.POST.get("name", hotel.name).strip()
        hotel.category = request.POST.get("category", hotel.category)
        hotel.city = request.POST.get("city", hotel.city).strip()
        hotel.address = request.POST.get("address", hotel.address).strip()
        hotel.google_maps_url = request.POST.get("google_maps_url", "").strip() or None

        # 2. Geolocation (Optional)
        lat = request.POST.get("latitude", "").strip()
        lng = request.POST.get("longitude", "").strip()
        hotel.latitude = lat if lat else None
        hotel.longitude = lng if lng else None

        # 3. Contact Details
        hotel.owner_name = request.POST.get("owner_name", hotel.owner_name).strip()
        hotel.owner_phone = request.POST.get("owner_phone", hotel.owner_phone).strip()
        hotel.owner_email = request.POST.get("owner_email", hotel.owner_email).strip()

        # 4. Amenities Checklist (HTML checkboxes return 'on' only if checked)
        hotel.has_wifi = "has_wifi" in request.POST
        hotel.has_ac = "has_ac" in request.POST
        hotel.has_heater = "has_heater" in request.POST
        hotel.has_parking = "has_parking" in request.POST
        hotel.has_restaurant = "has_restaurant" in request.POST
        hotel.has_breakfast = "has_breakfast" in request.POST
        hotel.has_pool = "has_pool" in request.POST

        hotel.save()
        messages.success(request, f"Property '{hotel.name}' details updated successfully!")
        return redirect("partner_dashboard")

    return render(request, "partner/edit_property.html", {
        "hotel": hotel,
        "categories": Hotel.CATEGORY_CHOICES,
    })

@login_required(login_url='partner_login')
def partner_update_booking_status(request, booking_id, new_status):
    booking = get_object_or_404(Booking, booking_id=booking_id, hotel__owner=request.user)
    
    valid_transitions = ['CHECKED_IN', 'CHECKED_OUT', 'CANCELLED']
    if new_status in valid_transitions:
        booking.status = new_status
        if new_status == 'CANCELLED':
            booking.cancellation_reason = 'Cancelled by partner front desk'
        booking.save()
        messages.success(request, f"Booking {booking.booking_id} status updated to {new_status}.")
    else:
        messages.error(request, "Invalid status transition.")
        
    return redirect('partner_dashboard')

#partner delete hotel
@login_required
def delete_hotel(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id, owner=request.user)
    has_active_bookings = hotel.bookings.filter(
        status__in=['CONFIRMED', 'CHECKED_IN']
    ).exists()

    if has_active_bookings:
        messages.error(
            request, 
            f"Cannot delete '{hotel.name}'. It has active or upcoming guest reservations."
        )
        return redirect('partner_dashboard')
    hotel_name = hotel.name
    hotel.delete()
    messages.success(request, f"Property '{hotel_name}' has been permanently deleted.")
    return redirect('partner_dashboard')

# Partner Logout
def partner_logout(request):
    logout(request)
    messages.info(request, 'Logged out successfully.')
    return redirect('partner_login')

# NEW MODULES: REVIEWS, RATINGS & MEDIA GALLERY MANAGEMENT

def submit_hotel_review(request, hotel_id):
    if request.method == 'POST':
        hotel = get_object_or_404(Hotel, id=hotel_id)
        
        booking_id = request.POST.get('booking_id', '').strip()
        rating_val = request.POST.get('rating', '5')
        comment = request.POST.get('comment', '').strip()

        # 1. Booking ID Validation
        if not booking_id:
            messages.error(request, "Please enter your Booking ID (MMY-HTL-XXXX).")
            return redirect('index')

        # Check: this Booking ID is valid for this hotel and has completed stay
        valid_booking = Booking.objects.filter(
            booking_id__iexact=booking_id,
            hotel=hotel,
            status__in=['CHECKED_OUT', 'Confirmed']
        ).first()

        if not valid_booking:
            messages.error(request, "Invalid Booking ID! Please ensure you have completed your stay at this property before submitting a review.")
            return redirect('index')

        # 2. Check if review already exists for this booking
        # for security, we can check if the HotelReview model has a booking_id field
        if hasattr(HotelReview, 'booking_id') and HotelReview.objects.filter(booking_id=booking_id).exists():
            messages.warning(request, f"Booking ID {booking_id} par pehle hi review diya ja chuka hai.")
            return redirect('index')

        # 3. Rating & Comment Validation
        try:
            rating = max(1, min(5, int(rating_val)))
        except ValueError:
            rating = 5

        if not comment:
            messages.error(request, "Please enter your review comment.")
            return redirect('index')

        # 4. Create Review
        # Guest name is fetched from the booking record; if not available, fallback to user full name or generic "Verified Guest"
        guest_name = getattr(valid_booking, 'guest_name', None) or (valid_booking.user.get_full_name() if valid_booking.user else "Verified Guest")

        HotelReview.objects.create(
            hotel=hotel,
            guest_name=guest_name,
            rating=rating,
            comment=comment,
            is_approved=True
        )

        messages.success(request, f"Thank you {guest_name}! Your verified review has been submitted.")
        return redirect('index')

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


# Helper to fetch booking by string UUID or numeric ID
def _get_booking_lookup(booking_id):
    lookup = Q(booking_id=booking_id)
    if str(booking_id).isdigit():
        lookup |= Q(id=int(booking_id))
    return lookup

# Reusable PDF Helper
def render_to_pdf(template_src, context_dict={}):
    template = get_template(template_src)
    html = template.render(context_dict)
    result = io.BytesIO()
    pdf = pisa.pisaDocument(io.BytesIO(html.encode('UTF-8')), result)
    if not pdf.err:
        return result.getvalue()
    return None

# 1. Customer PDF Download View
def download_customer_voucher(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related('hotel', 'room_type', 'meal_plan'),
        _get_booking_lookup(booking_id)
    )

    nights = 1
    if booking.check_in and booking.check_out:
        nights = max((booking.check_out - booking.check_in).days, 1)

    context = {
        'booking': booking,
        'hotel': booking.hotel,
        'nights': nights,
        'invoice_number': f'INV-{booking.booking_id}',
        'invoice_date': (
            booking.created_at.date()
            if hasattr(booking, 'created_at') and booking.created_at
            else timezone.localdate()
        ),
    }

    pdf = render_to_pdf('custom_admin/invoice_print.html', context)
    if pdf:
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="Voucher_{booking.booking_id}.pdf"'
        return response

    return HttpResponse('Error generating voucher PDF', status=500)

# 2. Admin / Partner Portal PDF Download View
@login_required(login_url='portal_login')
def portal_booking_invoice(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related('hotel', 'room_type', 'meal_plan'),
        _get_booking_lookup(booking_id)
    )

    nights = 1
    if booking.check_in and booking.check_out:
        nights = max((booking.check_out - booking.check_in).days, 1)

    context = {
        'booking': booking,
        'hotel': booking.hotel,
        'nights': nights,
        'invoice_number': f'INV-{booking.booking_id}',
        'invoice_date': (
            booking.created_at.date()
            if hasattr(booking, 'created_at') and booking.created_at
            else timezone.localdate()
        ),
    }

    pdf = render_to_pdf('custom_admin/invoice_print.html', context)
    if pdf:
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="Invoice_{booking.booking_id}.pdf"'
        return response

    return HttpResponse('Error generating invoice PDF', status=500)

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
        messages.success(request, f"SMS and Email #{booking.booking_id} sent!")
    elif email_sent:
        messages.info(request, f"Email dispatched (#{booking.booking_id}).")
    elif sms_sent:
        messages.info(request, f"SMS dispatched (#{booking.booking_id}).")

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

@require_POST
def apply_coupon_ajax(request):
    coupon_code = request.POST.get('coupon_code', '').strip()
    base_rate = request.POST.get('base_rate', 0)
    meal_extra = request.POST.get('meal_extra', 0)
    nights = request.POST.get('nights', 1)

    if not coupon_code:
        return JsonResponse({'success': False, 'message': 'Please enter a coupon code.'}, status=400)

    coupon = Coupon.objects.filter(code__iexact=coupon_code, is_active=True).first()
    today = timezone.localdate()
    

    if not coupon or (coupon.valid_till and coupon.valid_till < today):
        return JsonResponse({'success': False, 'message': 'Invalid or expired coupon.'}, status=400)

    flat_disc = coupon.discount_value if coupon.discount_type == 'flat' else 0
    pct_disc = coupon.discount_value if coupon.discount_type == 'percentage' else 0
    rooms_count = int(request.POST.get('rooms_count', 1))
    adults_count = int(request.POST.get('adults_count', 2))
    children_count = int(request.POST.get('children_count', 0))

    bill = calculate_stay_pricing(
        base_price=base_rate,
        meal_extra=meal_extra,
        nights=nights,
        rooms=rooms_count,
        adults=adults_count,
        children=children_count,
        discount_percent=pct_disc,
        flat_discount=flat_disc
    )

    return JsonResponse({
        'success': True,
        'message': f"Coupon '{coupon.code}' applied!",
        'discount_amount': str(bill['discount_amount']),
        'taxable_amount': str(bill['taxable_amount']),
        'cgst': str(bill['cgst']),
        'sgst': str(bill['sgst']),
        'total_gst': str(bill['total_gst']),
        'grand_total': str(bill['grand_total']),
    })

# Custom Portal - Coupon Management
@user_passes_test(is_admin, login_url='portal_login')
def portal_coupons(request):
  if request.method == 'POST':
    code = request.POST.get('code', '').strip().upper()
    discount_type = request.POST.get('discount_type', 'percentage')
    discount_value = request.POST.get('discount_value', 0)
    valid_till = request.POST.get('valid_till') or None
    is_active = 'is_active' in request.POST

    if code and discount_value:
      Coupon.objects.create(
          code=code,
          discount_type=discount_type,
          discount_value=Decimal(str(discount_value)),
          valid_till=valid_till,
          is_active=is_active,
      )
      messages.success(request, f"Coupon '{code}' created successfully!")
    else:
      messages.error(request, 'Please fill all required fields.')
    return redirect('portal_coupons')

  coupons = Coupon.objects.all().order_by('-id')
  return render(request, 'custom_admin/coupons.html', {'coupons': coupons})


@user_passes_test(is_admin, login_url='portal_login')
def portal_coupon_delete(request, coupon_id):
  coupon = get_object_or_404(Coupon, id=coupon_id)
  code = coupon.code
  coupon.delete()
  messages.success(request, f"Coupon '{code}' deleted.")
  return redirect('portal_coupons')

@user_passes_test(is_admin, login_url='portal_login')
def portal_meal_plans(request):
    meal_plans = MealPlan.objects.all().order_by('id')
    return render(request, 'custom_admin/meal_plans.html', {'meal_plans': meal_plans})


@login_required(login_url='portal_login')
def portal_revenue_report(request):
    hotels = Hotel.objects.all().order_by('name')
    
    # 1. Date Filters Read Karein (Default: Last 30 Days)
    today = timezone.localdate()
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    hotel_id = request.GET.get('hotel_id')

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else today - timedelta(days=30)
    except ValueError:
        start_date = today - timedelta(days=30)

    try:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else today
    except ValueError:
        end_date = today

    # 2. Base Query (Sirf Confirmed / Completed bookings ka revenue count hoga)
    valid_statuses = ['CONFIRMED', 'CHECKED_IN', 'CHECKED_OUT']
    base_bookings = Booking.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
        status__in=valid_statuses
    )

    if hotel_id and hotel_id.isdigit():
        base_bookings = base_bookings.filter(hotel_id=int(hotel_id))

    # 3. Overall KPI Metrics
    totals = base_bookings.aggregate(
        total_bookings=Count('id'),
        total_revenue=Sum('total_amount'),
        total_gst=Sum('gst_amount'),
        total_meals=Sum('meal_plan_amount'),
        total_discount=Sum('discount_amount'),
    )

    kpi_summary = {
        'total_bookings': totals['total_bookings'] or 0,
        'total_revenue': totals['total_revenue'] or Decimal('0.00'),
        'total_gst': totals['total_gst'] or Decimal('0.00'),
        'total_meals': totals['total_meals'] or Decimal('0.00'),
        'total_discount': totals['total_discount'] or Decimal('0.00'),
        'net_revenue': ((totals['total_revenue'] or Decimal('0.00')) - (totals['total_gst'] or Decimal('0.00'))),
    }

    # 4. Hotel-wise Breakdown
    hotel_summary = (
        base_bookings.values('hotel__id', 'hotel__name', 'hotel__city')
        .annotate(
            booking_count=Count('id'),
            revenue=Sum('total_amount'),
            tax=Sum('gst_amount'),
            meal_revenue=Sum('meal_plan_amount')
        )
        .order_by('-revenue')
    )

    # 5. Date-wise Breakdown
    date_summary = (
        base_bookings.values('created_at__date')
        .annotate(
            booking_count=Count('id'),
            daily_revenue=Sum('total_amount'),
            daily_gst=Sum('gst_amount')
        )
        .order_by('-created_at__date')
    )

    context = {
        'hotels': hotels,
        'selected_hotel': int(hotel_id) if hotel_id and hotel_id.isdigit() else '',
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'kpi': kpi_summary,
        'hotel_summary': hotel_summary,
        'date_summary': date_summary,
    }
    return render(request, 'custom_admin/revenue_report.html', context)

def cancel_booking(request, booking_id):
    # Booking find karo
    if booking_id.isdigit():
        booking = get_object_or_404(Booking, id=int(booking_id))
    else:
        booking = get_object_or_404(Booking, booking_id=booking_id)

    # Agar pehle se cancelled hai toh wapas track page par bhej do
    booking_ref = booking.booking_id if hasattr(booking, 'booking_id') else booking.id
    if booking.status == 'CANCELLED':
        messages.info(request, "This booking is already cancelled.")
        return redirect(f"/track-booking/?booking_id={booking_ref}")

    if request.method == 'POST':
        cancellation_reason = request.POST.get('cancellation_reason', 'Cancelled by guest')
        
        booking.status = 'CANCELLED'
        if hasattr(booking, 'cancellation_reason'):
            booking.cancellation_reason = cancellation_reason
        booking.save()

        # Room availability wapas inventory mein add karo
        if hasattr(booking, 'room_type') and hasattr(booking.room_type, 'available_rooms'):
            booking.room_type.available_rooms += 1
            booking.room_type.save()

        messages.success(request, f"Your reservation #{booking_ref} has been cancelled successfully.")
        
        # Cancel hone ke baad Guest wapas apne Track Booking page par jayega (Admin panel nahi)
        return redirect(f"/track-booking/?booking_id={booking_ref}")

    # Guest-facing template render karo
    return render(request, 'guest_cancel_confirm.html', {'booking': booking})

def calculate_cancellation_refund(booking):
  """Calculates refund and deduction based on hours left until check-in date."""
  now = timezone.now()
  # Check-in date at standard 12:00 PM check-in time
  check_in_datetime = timezone.make_aware(
      datetime.combine(booking.check_in, datetime.min.time().replace(hour=12))
  )

  hours_left = (check_in_datetime - now).total_seconds() / 3600
  total_paid = booking.total_amount

  if hours_left >= 48:
    refund_percent = Decimal('1.00')  # 100% Refund
    policy_note = 'Free cancellation applied (>48 hrs before check-in)'
  elif 24 <= hours_left < 48:
    refund_percent = Decimal('0.50')  # 50% Refund
    policy_note = '50% retention charge applied (24-48 hrs before check-in)'
  else:
    refund_percent = Decimal('0.00')  # No Refund
    policy_note = 'No refund policy applied (<24 hrs before check-in)'

  refund_amount = (total_paid * refund_percent).quantize(Decimal('0.01'))
  cancellation_fee = (total_paid - refund_amount).quantize(Decimal('0.01'))

  return refund_amount, cancellation_fee, policy_note

@login_required(login_url='portal_login')
def portal_booking_cancel(request, booking_id):
  booking = get_object_or_404(Booking, booking_id=booking_id)

  if booking.status == 'CANCELLED':
    messages.warning(request, f'Booking #{booking.booking_id} is already cancelled.')
    return redirect('portal_bookings')

  refund_amt, cancel_fee, note = calculate_cancellation_refund(booking)

  # Status update
  booking.status = 'CANCELLED'
  booking.save()

  messages.success(
      request,
      f'Booking #{booking.booking_id} cancelled successfully! Refund Amount:'
      f' ₹{refund_amt} | Fee: ₹{cancel_fee} ({note})',
  )
  return redirect('portal_bookings')

@login_required(login_url='partner_login')
def partner_quick_update_room(request, room_id):
    room = get_object_or_404(RoomType, id=room_id, hotel__owner=request.user)
    if request.method == 'POST':
        base_price = request.POST.get('base_price')
        is_open = request.POST.get('is_open') == 'on'
        
        if base_price:
            try:
                room.base_price = Decimal(base_price)
                room.is_open = is_open
                room.is_available = is_open
                room.save()
                messages.success(request, f"Updated pricing and availability for {room.name}.")
            except Exception:
                messages.error(request, "Invalid price format.")
    return redirect('partner_dashboard')

# Property listing
@user_passes_test(is_admin, login_url='portal_login')
def portal_hotels(request):
    hotels = Hotel.objects.all().order_by('-id')
    return render(request, 'custom_admin/hotel_list.html', {'hotels': hotels})

# 1-Click Verification / Approval Toggle
@user_passes_test(is_admin, login_url='portal_login')
def portal_toggle_hotel_verification(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id)
    hotel.is_verified = not hotel.is_verified
    hotel.save()
    
    if hotel.is_verified:
        messages.success(request, f'Stay "{hotel.name}" is now Approved & Live on MakesMyYatra!')
    else:
        messages.warning(request, f'Stay "{hotel.name}" has been Unverified (Hidden from public search).')
        
    return redirect('portal_hotels')

@login_required
def partner_manage_gallery(request, hotel_id):
    # Admin ya property ka owner dono upload kar sakein
    if request.user.is_staff or request.user.is_superuser:
        hotel = get_object_or_404(Hotel, id=hotel_id)
    else:
        hotel = get_object_or_404(Hotel, id=hotel_id, owner=request.user)

    # Multi-Photo Upload Handler
    if request.method == 'POST':
        files = request.FILES.getlist('gallery_photos')
        if files:
            from .models import HotelMedia
            for f in files:
                HotelMedia.objects.create(hotel=hotel, image_file=f)
            messages.success(request, f"{len(files)} new photo(s) added to gallery successfully!")
        else:
            messages.warning(request, "No photos were selected for upload.")
        return redirect(request.path)

    # Gallery items fetch
    media_items = hotel.media_gallery.all().order_by('-id')
    return render(request, 'partner/manage_gallery.html', {
        'hotel': hotel,
        'media_items': media_items,
    })


# 1. Partner Cancellation Action View
@login_required
def partner_cancel_booking(request, booking_id):
    if request.method == "POST":
        # Check ensure karein ki booking partner ke hi hotel ki ho
        booking = get_object_or_404(Booking, id=booking_id, hotel__owner=request.user)
        reason = request.POST.get("reason", "Property unavailable / Overbooking").strip()

        # Step 2 ka utility logic call
        success, msg = partner_cancel_booking_logic(booking, reason=reason)

        if success:
            messages.warning(
                request, 
                f"Booking #{booking.booking_id} cancelled. Gross booking price reduced by ₹{booking.total_amount}."
            )
        else:
            messages.error(request, msg)

    return redirect(request.META.get("HTTP_REFERER", "partner_dashboard"))


@login_required
def partner_gallery_image_delete(request, media_id):
    from .models import HotelMedia
    if request.user.is_staff or request.user.is_superuser:
        media_item = get_object_or_404(HotelMedia, id=media_id)
    else:
        media_item = get_object_or_404(HotelMedia, id=media_id, hotel__owner=request.user)

    hotel_id = media_item.hotel.id
    media_item.delete()
    messages.success(request, "Photo deleted successfully.")
    return redirect('partner_manage_gallery', hotel_id=hotel_id)