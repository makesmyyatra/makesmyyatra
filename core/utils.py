from decimal import Decimal
import datetime
from datetime import timedelta
from django.utils import timezone
from .models import EmergencySupport, Booking
from decimal import Decimal
from django.db.models import Sum, Count, Q

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
    tax_rate=Decimal('0.12'),  # 12% GST (6% CGST + 6% SGST)
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

    # 1. PEHLE NIGHTS CALCULATE HOGI
    total_nights = 0
    weekend_nights = 0
    weekday_nights = 0

    if check_in and check_out and check_out > check_in:
        cur = check_in
        while cur < check_out:
            # 4 = Friday, 5 = Saturday
            if cur.weekday() in [4, 5]:
                weekend_nights += 1
            else:
                weekday_nights += 1
            cur += datetime.timedelta(days=1)
        total_nights = weekday_nights + weekend_nights
    else:
        total_nights = int(nights or 1)
        weekday_nights = total_nights
        weekend_nights = 0

    # 2. ROOM TARIFF WITH WEEKEND SURGE
    weekday_cost = Decimal(str(weekday_nights)) * base_price
    weekend_cost = Decimal(str(weekend_nights)) * (base_price * surge_multiplier)
    pure_room_base = (weekday_cost + weekend_cost) * Decimal(str(rooms))

    # 3. REAL-WORLD MEAL CHARGES (Adults 100%, Children 50% * Nights)
    child_meal_rate = meal_extra * Decimal('0.5')
    total_meal_amount = ((Decimal(str(adults)) * meal_extra) + (Decimal(str(children)) * child_meal_rate)) * Decimal(str(total_nights))

    # 4. GROSS TOTAL
    gross_total = pure_room_base + total_meal_amount

    # 5. DISCOUNTS
    if flat_discount > Decimal('0.00'):
        discount_amount = min(flat_discount, gross_total).quantize(Decimal('0.01'))
    elif discount_percent > Decimal('0.00'):
        discount_amount = (gross_total * (discount_percent / Decimal('100'))).quantize(Decimal('0.01'))
    else:
        discount_amount = Decimal('0.00')

    taxable_amount = max(Decimal('0.00'), gross_total - discount_amount)

    # 6. 12% GST CALCULATION (CGST 6% + SGST 6%)
    total_gst = (taxable_amount * Decimal(str(tax_rate))).quantize(Decimal('0.01'))
    cgst = (total_gst / Decimal('2.00')).quantize(Decimal('0.01'))
    sgst = (total_gst - cgst).quantize(Decimal('0.01'))
    grand_total = (taxable_amount + total_gst).quantize(Decimal('0.01'))

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
        'cgst': cgst,
        'sgst': sgst,
        'total_gst': total_gst,
        'grand_total': grand_total,
    }

def purge_expired_emergency_requests(retention_days=30):
    """
    Autopilot: 30 din purane RESOLVED tickets ko database se auto delete karta hai.
    """
    cutoff_time = timezone.now() - timedelta(days=retention_days)
    deleted_count, _ = EmergencySupport.objects.filter(
        status='RESOLVED',
        resolved_at__lt=cutoff_time
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