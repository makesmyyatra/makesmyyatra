import os
import urllib.parse
import requests
from django.conf import settings
from django.core.mail import send_mail


def clean_phone_number(phone_raw):
    """Mobile number se spaces aur country codes clean karne ke liye helper."""
    raw = str(phone_raw).strip().replace(" ", "").replace("-", "")
    if raw.startswith("+91"):
        return raw[3:]
    elif raw.startswith("91") and len(raw) == 12:
        return raw[2:]
    return raw


# ==========================================
# 1. SIMPLE MOBILE TEXT SMS (Fast2SMS / Console)
# ==========================================
def send_booking_sms(booking):
    """
    Customer ke SIM card par standard SMS bhejta hai.
    Keypad phone ya bina internet wale users ke liye 100% reliable.
    """
    clean_phone = clean_phone_number(booking.guest_phone)
    check_in_str = booking.check_in.strftime('%d/%m/%Y') if hasattr(booking.check_in, 'strftime') else str(booking.check_in)

    # Standard SMS length: Under 160 characters
    sms_text = (
        f"MakesMyYatra: Booking Confirmed! Ref #{booking.booking_id} at {booking.hotel.name}, "
        f"{booking.hotel.city}. Check-in: {check_in_str}. "
        f"Amount: Rs.{booking.total_amount}. Helpline: 9816000000"
    )

    api_key = getattr(settings, 'FAST2SMS_API_KEY', None)

    if api_key:
        try:
            url = "https://www.fast2sms.com/dev/bulkV2"
            payload = {
                'authorization': api_key,
                'route': 'q',
                'message': sms_text,
                'numbers': clean_phone,
            }
            response = requests.post(url, data=payload, timeout=5)
            return response.status_code == 200
        except Exception as e:
            print(f"[SMS Gateway Error]: {e}")
            return False
    else:
        # Development / Fallback mode: Terminal console par clean output
        print(f"\n==========================================")
        print(f"📩 [OUTGOING NORMAL SMS]")
        print(f"To: +91 {clean_phone}")
        print(f"Message: {sms_text}")
        print(f"==========================================\n")
        return True


# ==========================================
# 2. WHATSAPP CONFIRMATION NOTIFICATION
# ==========================================
def get_whatsapp_booking_text(booking):
    """WhatsApp message text format."""
    meal_display = "Room Only (EP)"
    if booking.meal_plan:
        meal_display = getattr(booking.meal_plan, 'get_plan_code_display', lambda: str(booking.meal_plan))()

    check_in_str = booking.check_in.strftime('%d %b %Y') if hasattr(booking.check_in, 'strftime') else str(booking.check_in)
    check_out_str = booking.check_out.strftime('%d %b %Y') if hasattr(booking.check_out, 'strftime') else str(booking.check_out)

    return (
        f"🏔️ *MakesMyYatra Booking Confirmation*\n\n"
        f"Dear *{booking.guest_name}*,\n"
        f"Your mountain stay has been successfully confirmed!\n\n"
        f"🏨 *Property:* {booking.hotel.name}\n"
        f"📍 *Location:* {booking.hotel.city}, Himachal Pradesh\n"
        f"🔖 *Booking ID:* #{booking.booking_id}\n"
        f"📅 *Dates:* {check_in_str} to {check_out_str}\n"
        f"🍽️ *Meal Plan:* {meal_display}\n"
        f"💰 *Total Payable:* ₹{booking.total_amount} ({booking.get_payment_mode_display() if hasattr(booking, 'get_payment_mode_display') else booking.payment_mode})\n\n"
        f"For route guidance or any assistance, please contact our support helpline: +91 98160 00000\n\n"
        f"Have a wonderful trip! 🌲"
    )


def send_whatsapp_notification(booking):
    """
    Twilio credentials hone par direct API se send karega,
    warna WhatsApp Click-to-Chat URL return karega (No Crash).
    """
    clean_phone = clean_phone_number(booking.guest_phone)
    full_phone = f"+91{clean_phone}"
    text_msg = get_whatsapp_booking_text(booking)

    account_sid = getattr(settings, 'TWILIO_ACCOUNT_SID', None)
    auth_token = getattr(settings, 'TWILIO_AUTH_TOKEN', None)
    twilio_number = getattr(settings, 'TWILIO_WHATSAPP_NUMBER', None)

    # Method A: Twilio Cloud API
    if account_sid and auth_token and twilio_number:
        try:
            from twilio.rest import Client
            client = Client(account_sid, auth_token)
            client.messages.create(
                body=text_msg,
                from_=f"whatsapp:{twilio_number}",
                to=f"whatsapp:{full_phone}"
            )
            return True, "sent_via_api"
        except Exception as e:
            print(f"[Twilio WA Error]: {e}")
            pass

    # Method B: Direct URL / Console Preview Fallback
    encoded_text = urllib.parse.quote(text_msg)
    wa_link = f"https://wa.me/91{clean_phone}?text={encoded_text}"
    
    print(f"\n==========================================")
    print(f"💬 [OUTGOING WHATSAPP ALERT]")
    print(f"To: +91 {clean_phone}")
    print(f"Direct Link: {wa_link}")
    print(f"==========================================\n")

    return True, wa_link

# ==========================================
# 3. EMAIL DISPATCH (Django SMTP / Console)
# ==========================================
def send_booking_email(booking):
    """Customer ko standard email invoice & confirmation dispatch karta hai bina website block kiye."""
    if not getattr(booking, 'guest_email', None):
        return False

    # Check agar email credentials settings me configured hain ya nahi
    email_user = getattr(settings, 'EMAIL_HOST_USER', '')
    email_pass = getattr(settings, 'EMAIL_HOST_PASSWORD', '')

    # Agar Render par abhi password nahi daala hai, toh timeout se bachne ke liye direct console par log karo
    if not email_user or not email_pass:
        print(f"\n[EMAIL SKIPPED] Email credentials not set in environment. Booking #{booking.booking_id} email skipped safely.\n")
        return False

    subject = f"Booking Confirmed: #{booking.booking_id} - {booking.hotel.name} | MakesMyYatra"

    check_in_str = booking.check_in.strftime('%d %b %Y') if hasattr(booking.check_in, 'strftime') else str(booking.check_in)
    check_out_str = booking.check_out.strftime('%d %b %Y') if hasattr(booking.check_out, 'strftime') else str(booking.check_out)

    message = (
        f"Dear {booking.guest_name},\n\n"
        f"Your booking for '{booking.hotel.name}' has been successfully confirmed.\n\n"
        f"--- BOOKING SUMMARY ---\n"
        f"Booking ID: #{booking.booking_id}\n"
        f"Property: {booking.hotel.name}\n"
        f"City: {booking.hotel.city}, Himachal Pradesh\n"
        f"Check-In Date: {check_in_str}\n"
        f"Check-Out Date: {check_out_str}\n"
        f"Total Amount: Rs. {booking.total_amount}\n"
        f"Payment Mode: {booking.get_payment_mode_display() if hasattr(booking, 'get_payment_mode_display') else booking.payment_mode}\n\n"
        f"Address / Reach Out: {booking.hotel.address or booking.hotel.city}\n\n"
        f"Have a wonderful trip!\n"
        f"Team MakesMyYatra\n"
        f"Helpline: +91 98160 00000"
    )

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'support@makesmyyatra.com'),
            recipient_list=[booking.guest_email],
            fail_silently=True,
        )
        return True
    except Exception as e:
        print(f"[Email Dispatch Error]: {e}")
        return False