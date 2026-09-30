import openpyxl 
import uuid
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum
from django.core.mail import send_mail
from django.conf import settings

from .models import Hotel, RoomType, Booking, EmergencySupport
from .forms import HotelForm, RoomTypeForm, BookingStatusForm

from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import Booking

@login_required(login_url='portal_login')
def portal_booking_status(request, booking_id):
    if request.method == 'POST':
        booking = get_object_or_404(Booking, id=booking_id)
        new_status = request.POST.get('status')
        if new_status in ['Confirmed', 'Cancelled', 'Pending']:
            booking.status = new_status
            booking.save()
            messages.success(request, f"Booking #{booking.booking_id or booking.id} status updated to {new_status}.")
    return redirect('portal_bookings')


# ================= 1. PUBLIC WEB PORTAL =================
def index(request):
    city_query = request.GET.get('city', '').strip()
    check_in = request.GET.get('check_in', '')
    check_out = request.GET.get('check_out', '')

    hotels = Hotel.objects.filter(is_active=True)

    if city_query and city_query != 'All':
        hotels = hotels.filter(city__icontains=city_query)

    context = {
        'hotels': hotels,
        'selected_city': city_query if city_query else 'All',
        'check_in': check_in,
        'check_out': check_out,
    }
    return render(request, 'index.html', context)

def taxi(request):
    return render(request, 'index.html', {'message': 'Cab service booking coming soon!'})


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

def book_hotel(request):
    if request.method == 'POST':
        hotel_id = request.POST.get('hotel_id')
        guest_name = request.POST.get('guest_name', '').strip()
        guest_phone = request.POST.get('guest_phone', '').strip()
        guest_email = request.POST.get('guest_email', '').strip()
        check_in = request.POST.get('check_in')
        check_out = request.POST.get('check_out')
        base_amount = request.POST.get('base_amount', 2499)

        hotel = get_object_or_404(Hotel, id=hotel_id)

        # 1. Ensure RoomType exists (Mandatory foreign key fix)
        room = hotel.room_types.first()
        if not room:
            room = RoomType.objects.create(
                hotel=hotel,
                name="Deluxe Room",
                base_price=2499,
                max_guests=2
            )

        # 2. Generate unique booking reference id
        booking_ref = f"MMY{uuid.uuid4().hex[:6].upper()}"

        try:
            booking = Booking.objects.create(
                booking_id=booking_ref,
                hotel=hotel,
                room_type=room,
                guest_name=guest_name,
                guest_phone=guest_phone,
                guest_email=guest_email,
                check_in=check_in,
                check_out=check_out,
                guests_count=2,
                base_amount=base_amount,
                final_amount=base_amount,
                payment_mode='pay_at_hotel',
                status='pending',
                is_paid=False
            )
            messages.success(request, f'Congratulations {guest_name}! Your stay at {hotel.name} is reserved. Booking ID: #{booking.booking_id}')
        except Exception as e:
            messages.error(request, f'Could not complete booking: {e}')

        return redirect('index')

    return redirect('index')

# ================= 2. ADMIN AUTHENTICATION =================

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


# ================= 3. ADMIN DASHBOARD & CRUD =================

@login_required(login_url='portal_login')
def portal_dashboard(request):
    total_hotels = Hotel.objects.count()
    total_bookings = Booking.objects.count()
    active_supports = EmergencySupport.objects.filter(status='open').count()
    revenue = Booking.objects.filter(is_paid=True).aggregate(Sum('final_amount'))['final_amount__sum'] or 0

    recent_bookings = Booking.objects.select_related('hotel', 'room_type').order_by('-created_at')[:8]
    recent_supports = EmergencySupport.objects.filter(status='open').order_by('-created_at')[:5]

    context = {
        'total_hotels': total_hotels,
        'total_bookings': total_bookings,
        'active_supports': active_supports,
        'revenue': revenue,
        'recent_bookings': recent_bookings,
        'recent_supports': recent_supports,
    }
    return render(request, 'custom_admin/dashboard.html', context)


# --- HOTEL CRUD ---

@login_required(login_url='portal_login')
def portal_hotels(request):
    hotels = Hotel.objects.all().order_by('-created_at')
    return render(request, 'custom_admin/hotel_list.html', {'hotels': hotels})

@login_required(login_url='portal_login')
def portal_hotel_add(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        city = request.POST.get('city', '').strip()
        address = request.POST.get('address', '').strip()
        star_rating = request.POST.get('star_rating', 4)
        
        # User input ya fallback default image (NOT NULL error se bachne ke liye)
        submitted_img = request.POST.get('image_url') or request.POST.get('cover_image')
        default_cover = "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=800&q=80"
        cover_image = submitted_img.strip() if submitted_img and submitted_img.strip() else default_cover

        # Hotel create karein schema ke mutabiq
        hotel = Hotel.objects.create(
            name=name,
            category='budget',
            city=city,
            address=address,
            star_rating=int(star_rating) if star_rating else 4,
            cover_image=cover_image,
            has_wifi=True,
            has_parking=True,
            has_heater=True,
            is_active=True
        )

        # Base room category auto-link karein
        try:
            RoomType.objects.get_or_create(
                hotel=hotel,
                defaults={
                    'name': 'Standard Valley Room',
                    'base_price': 2499,
                    'max_guests': 2
                }
            )
        except Exception:
            pass

        messages.success(request, f'Property "{hotel.name}" has been published to inventory!')
        return redirect('portal_hotels')

    return render(request, 'custom_admin/hotel_add.html', {'title': 'Register New Property'})


@login_required(login_url='portal_login')
def portal_hotel_edit(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id)
    if request.method == 'POST':
        form = HotelForm(request.POST, request.FILES, instance=hotel)
        if form.is_valid():
            form.save()
            messages.success(request, f'Property "{hotel.name}" updated successfully!')
            return redirect('portal_hotels')
    else:
        form = HotelForm(instance=hotel)
    return render(request, 'custom_admin/hotel_add.html', {'form': form, 'title': f'Edit Property: {hotel.name}'})


@login_required(login_url='portal_login')
def portal_hotel_delete(request, hotel_id):
    hotel = get_object_or_404(Hotel, id=hotel_id)
    name = hotel.name
    hotel.delete()
    messages.success(request, f'Property "{name}" has been deleted from inventory.')
    return redirect('portal_hotels')


# --- BOOKING MANAGEMENT, STATUS UPDATE & EMAIL ---

@login_required(login_url='portal_login')
def portal_bookings(request):
    bookings = Booking.objects.select_related('hotel', 'room_type').order_by('-created_at')
    return render(request, 'custom_admin/bookings.html', {'bookings': bookings})


@login_required(login_url='portal_login')
def portal_booking_status_change(request, booking_id, new_status):
    booking = get_object_or_404(Booking, booking_id=booking_id)
    valid_statuses = ['pending', 'confirmed', 'cancelled', 'rejected']
    
    if new_status in valid_statuses:
        booking.status = new_status
        booking.save()

        # Send official confirmation email when admin confirms booking
        if new_status == 'confirmed' and booking.guest_email:
            subject = f"Booking Confirmed! MakeMyYatra Pass #{booking.booking_id}"
            message = f"""Dear {booking.guest_name},

Great news! Your mountain stay reservation has been officially CONFIRMED.

==============================================
             BOOKING CONFIRMATION
==============================================
Reservation ID : {booking.booking_id}
Hotel Name     : {booking.hotel.name}
Destination    : {booking.hotel.city}
Check-In Date  : {booking.check_in}
Check-Out Date : {booking.check_out}
Guests         : {booking.guests_count}
Amount Payable : Rs. {booking.final_amount} (Pay at Hotel)
Status         : CONFIRMED
==============================================

Address / Landmark:
{booking.hotel.address}

Need assistance during your hill drive?
MakeMyYatra 24x7 Operations Desk: +91 98160XXXXX

Warm Regards,
MakeMyYatra Central Reservations Team
"""
            try:
                send_mail(
                    subject,
                    message,
                    settings.DEFAULT_FROM_EMAIL,
                    [booking.guest_email],
                    fail_silently=True,
                )
                messages.success(request, f"Booking {booking.booking_id} confirmed and official email sent to {booking.guest_email}!")
            except Exception as e:
                messages.warning(request, f"Status updated, but email could not be sent: {e}")
        else:
            messages.success(request, f"Booking {booking.booking_id} status changed to {new_status.title()}.")

    return redirect('portal_bookings')


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


# --- EXCEL REPORT EXPORT ---

@login_required(login_url='portal_login')
def export_bookings_excel(request):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MakeMyYatra Bookings"

    # Header styling
    headers = [
        "Booking ID", "Guest Name", "Phone", "Email", 
        "Hotel Name", "City", "Check-in", "Check-out", 
        "Amount (INR)", "Payment Mode", "Status", "Booked At"
    ]
    ws.append(headers)

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="051322", end_color="051322", fill_type="solid")
    
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Insert Data rows
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
            float(b.final_amount) if b.final_amount else 2499.0,
            b.payment_mode or "Pay at Hotel",
            str(b.status).upper(),
            b.created_at.strftime("%d-%m-%Y %H:%M") if hasattr(b, 'created_at') and b.created_at else "N/A"
        ])

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=MakeMyYatra_Bookings_Report.xlsx'
    wb.save(response)
    return response


# --- URGENT HELP DESK ---

@login_required(login_url='portal_login')
def portal_urgent_help(request):
    supports = EmergencySupport.objects.all().order_by('-created_at')
    return render(request, 'custom_admin/urgent_help.html', {'supports': supports})


@login_required(login_url='portal_login')
def portal_urgent_help_resolve(request, support_id):
    item = get_object_or_404(EmergencySupport, id=support_id)
    item.status = 'resolved'
    item.save()
    messages.success(request, f"Urgent request from {item.name} marked as resolved.")
    return redirect('portal_urgent_help')