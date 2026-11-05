from decimal import Decimal
import datetime
from datetime import timedelta
from django.utils import timezone
from .models import EmergencySupport, Booking
from decimal import Decimal
from django.db.models import Sum, Count, Q

def hotel_gst_rate(night_rate):
    """GST on hotel rooms is decided per room, per night, on the declared room tariff
    (rates in force since 22 Sep 2025 - confirm with your CA):
    up to Rs 1,000 = 0%, Rs 1,001 to Rs 7,500 = 5%, above Rs 7,500 = 18%."""
    night_rate = Decimal(str(night_rate))
    if night_rate <= Decimal('1000'):
        return Decimal('0.00')
    if night_rate <= Decimal('7500'):
        return Decimal('0.05')
    return Decimal('0.18')


def calculate_stay_pricing(
    base_price,
    meal_extra=Decimal('0.00'),
    check_in=None,
    check_out=None,
    nights=1,
    rooms=1,
    adults=2,
    children=0,
    discount_percent=Decimal('0.00'),
    flat_discount=Decimal('0.00'),
    surge_percent=20,
    tax_rate=None,  # None = slab-wise GST per night. Pass a Decimal (e.g. Decimal('0.12')) to force a flat rate.
    *args,
    **kwargs
):
    base_price = Decimal(str(base_price or 0))
    meal_extra = Decimal(str(meal_extra or 0))
    rooms = int(rooms) if int(rooms or 1) > 0 else 1
    adults = int(adults) if int(adults or 1) > 0 else 1
    children = int(children) if int(children or 0) >= 0 else 0
    discount_percent = Decimal(str(discount_percent or 0))
    flat_discount = Decimal(str(flat_discount or 0))
    surge_multiplier = Decimal('1.00') + (Decimal(str(surge_percent)) / Decimal('100'))
    surge_rate = base_price * surge_multiplier

    # 1. Nights and the declared room rate of each night (Fri/Sat = surge)
    night_rates = []
    weekend_nights = 0
    weekday_nights = 0

    if check_in and check_out and check_out > check_in:
        cur = check_in
        while cur < check_out:
            # 4 = Friday, 5 = Saturday
            if cur.weekday() in [4, 5]:
                weekend_nights += 1
                night_rates.append(surge_rate)
            else:
                weekday_nights += 1
                night_rates.append(base_price)
            cur += datetime.timedelta(days=1)
        total_nights = weekday_nights + weekend_nights
    else:
        total_nights = max(int(nights or 1), 1)
        weekday_nights = total_nights
        weekend_nights = 0
        night_rates = [base_price] * total_nights

    # 2. Room tariff with weekend surge
    pure_room_base = (Decimal(str(weekday_nights)) * base_price + Decimal(str(weekend_nights)) * surge_rate) * Decimal(str(rooms))

    # 3. Meal charges (adults 100%, children 50%, per night)
    child_meal_rate = meal_extra * Decimal('0.5')
    meal_per_night = (Decimal(str(adults)) * meal_extra) + (Decimal(str(children)) * child_meal_rate)
    total_meal_amount = meal_per_night * Decimal(str(total_nights))

    # 4. Gross total
    gross_total = pure_room_base + total_meal_amount

    # 5. Discounts
    if flat_discount > Decimal('0.00'):
        discount_amount = min(flat_discount, gross_total).quantize(Decimal('0.01'))
    elif discount_percent > Decimal('0.00'):
        discount_amount = (gross_total * (discount_percent / Decimal('100'))).quantize(Decimal('0.01'))
    else:
        discount_amount = Decimal('0.00')

    taxable_amount = max(Decimal('0.00'), gross_total - discount_amount)

    # 6. GST: slab chosen by each night's declared room rate, charged on that night's share after discount
    if tax_rate is not None:
        gst_raw = taxable_amount * Decimal(str(tax_rate))
    else:
        gst_raw = Decimal('0.00')
        for rate in night_rates:
            night_gross = rate * Decimal(str(rooms)) + meal_per_night
            share = (night_gross / gross_total) if gross_total > 0 else Decimal('0.00')
            night_taxable = night_gross - (discount_amount * share)
            gst_raw += max(Decimal('0.00'), night_taxable) * hotel_gst_rate(rate)

    total_gst = gst_raw.quantize(Decimal('0.01'))
    cgst = (total_gst / Decimal('2.00')).quantize(Decimal('0.01'))
    sgst = (total_gst - cgst).quantize(Decimal('0.01'))
    grand_total = (taxable_amount + total_gst).quantize(Decimal('0.01'))
    gst_percent = ((total_gst / taxable_amount) * Decimal('100')).quantize(Decimal('0.1')) if taxable_amount > 0 else Decimal('0.0')

    return {
        'total_nights': total_nights,
        'weekday_nights': weekday_nights,
        'weekend_nights': weekend_nights,
        'has_weekend_surge': weekend_nights > 0,
        'pure_room_base': pure_room_base,
        'total_meal_amount': total_meal_amount,
        'gross_total': gross_total,
        'discount_amount': discount_amount,
        'taxable_amount': taxable_amount,
        'gst_percent': gst_percent,
        'cgst': cgst,
        'sgst': sgst,
        'total_gst': total_gst,
        'grand_total': grand_total,
    }

def purge_expired_emergency_requests(retention_days=30):
    cutoff_time = timezone.now() - timedelta(days=retention_days)
    # Check if resolved_at exists, otherwise fallback to created_at
    deleted_count, _ = EmergencySupport.objects.filter(
        status__iexact='RESOLVED',
        created_at__lt=cutoff_time
    ).delete()
    return deleted_count

def partner_cancel_booking_logic(booking_obj, reason="Cancelled by Hotel Partner", penalty_rate=Decimal("0.00")):
    """
    Executes partner-side cancellation:
    - Marks status as CANCELLED_BY_PARTNER
    - Resets partner payout to 0.00 (Revenue deducted)
    - Records optional penalty and timestamp
    """
    if booking_obj.status in ["CANCELLED", "CANCELLED_BY_PARTNER", "CANCELLED_BY_GUEST"]:
        return False, "This booking is already cancelled."

    booking_obj.status = "CANCELLED_BY_PARTNER"
    booking_obj.cancelled_by = "PARTNER"
    booking_obj.cancellation_reason = reason
    booking_obj.cancelled_at = timezone.now()

    # Partner payout ko zero kar diya taaki partner ko amount na mile
    booking_obj.partner_payout = Decimal("0.00")

    # Agar koi penalty set karni ho (e.g., Decimal("0.10") for 10%)
    if penalty_rate > Decimal("0.00"):
        booking_obj.cancellation_penalty = round(booking_obj.total_amount * penalty_rate, 2)

    booking_obj.save()
    return True, f"Booking #{booking_obj.booking_id} cancelled. Gross booking revenue deducted."


def get_partner_booking_metrics(partner_user):
    """
    Calculates Partner Gross Booking Price after auto-deducting cancelled bookings.
    Only CONFIRMED, CHECKED_IN, and CHECKED_OUT bookings count toward Gross Revenue.
    """
    # Partner ke hotels ki saari bookings
    partner_bookings = Booking.objects.filter(hotel__owner=partner_user)

    # 1. Total Gross Booking Value (Only Active/Non-Cancelled) -> Automatically Reduced
    active_bookings = partner_bookings.exclude(
        status__in=["CANCELLED", "CANCELLED_BY_PARTNER", "CANCELLED_BY_GUEST"]
    )
    gross_booking_value = active_bookings.aggregate(
        total=Sum("total_amount")
    )["total"] or Decimal("0.00")

    # 2. Total Deducted / Cancelled Value (Jo price minus hui)
    cancelled_by_partner_bookings = partner_bookings.filter(status="CANCELLED_BY_PARTNER")
    deducted_gross_value = cancelled_by_partner_bookings.aggregate(
        total=Sum("total_amount")
    )["total"] or Decimal("0.00")

    # 3. Penalties total
    total_penalty = cancelled_by_partner_bookings.aggregate(
        penalty=Sum("cancellation_penalty")
    )["penalty"] or Decimal("0.00")

    return {
        "gross_booking_value": gross_booking_value,         # Reduced gross revenue
        "deducted_gross_value": deducted_gross_value,       # Amount cut due to cancellations
        "active_bookings_count": active_bookings.count(),
        "partner_cancellations_count": cancelled_by_partner_bookings.count(),
        "total_penalty": total_penalty,
    }