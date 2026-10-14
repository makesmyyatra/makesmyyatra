from decimal import Decimal

def calculate_stay_pricing(base_price, meal_extra=0, nights=1, rooms=1, discount=0):
    """Calculates room tariff, meal add-ons, tiered GST slabs, and grand total."""
    base_price = Decimal(str(base_price))
    meal_extra = Decimal(str(meal_extra))
    discount = Decimal(str(discount))
    nights = int(nights) if int(nights) > 0 else 1
    rooms = int(rooms) if int(rooms) > 0 else 1

    # Nightly room + meal rate per room
    nightly_rate = base_price + meal_extra

    # Indian Hotel GST Slabs based on nightly rate
    if nightly_rate < 1000:
        gst_rate = Decimal('0.00')
    elif nightly_rate <= 7500:
        gst_rate = Decimal('0.12')  # 12%
    else:
        gst_rate = Decimal('0.18')  # 18%

    total_base = (nightly_rate * nights * rooms) - discount
    total_base = max(Decimal('0.00'), total_base)

    # 50-50 Split: CGST + SGST
    total_gst = (total_base * gst_rate).quantize(Decimal('0.01'))
    cgst = (total_gst / 2).quantize(Decimal('0.01'))
    sgst = (total_gst / 2).quantize(Decimal('0.01'))
    grand_total = (total_base + total_gst).quantize(Decimal('0.01'))

    return {
        'nightly_rate': nightly_rate,
        'taxable_amount': total_base,
        'gst_percent': int(gst_rate * 100),
        'cgst': cgst,
        'sgst': sgst,
        'total_gst': total_gst,
        'grand_total': grand_total,
    }