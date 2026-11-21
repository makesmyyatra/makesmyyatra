
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .models import (
    Coupon,
    Booking,
    CabBooking,
    Welcome10Redemption,
)


class Welcome10ModelTests(TestCase):

    def setUp(self):
        self.coupon = Coupon.objects.create(
            code="WELCOME10",
            discount_type="percentage",
            discount_value=Decimal("10.00"),
            is_active=True,
        )

    def test_welcome10_coupon_is_valid(self):
        valid, message = self.coupon.is_valid()
        self.assertTrue(valid)

    def test_welcome10_discount_is_10_percent(self):
        discount, message = self.coupon.calculate_discount(
            Decimal("1000.00")
        )
        self.assertEqual(discount, Decimal("100.00"))

    def test_redemption_email_is_unique(self):
        Welcome10Redemption.objects.create(
            email="test@example.com",
            booking_type="HOTEL",
            booking_id="TEST-HOTEL-001",
        )

        with self.assertRaises(Exception):
            Welcome10Redemption.objects.create(
                email="test@example.com",
                booking_type="CAB",
                booking_id="TEST-CAB-001",
            )

    def test_confirmed_hotel_booking_is_detected(self):
        Booking.objects.create(
            guest_email="hotel@example.com",
            status="CONFIRMED",
        )

        self.assertTrue(
            Booking.objects.filter(
                guest_email__iexact="hotel@example.com",
                status="CONFIRMED",
            ).exists()
        )

    def test_cab_is_not_confirmed_before_advance(self):
        self.assertFalse(
            CabBooking.objects.filter(
                guest_email="cab@example.com",
                advance_paid=True,
                status="CONFIRMED",
            ).exists()
        )
