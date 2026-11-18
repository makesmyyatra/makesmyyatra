from decimal import Decimal
import uuid
from django.contrib.auth.models import User
from django.db import IntegrityError, models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator

# Helper for Booking Reference
def generate_mmy_booking_id():
    random_code = uuid.uuid4().hex[:10].upper()
    return f"MMY-HTL-{random_code}"


# Hotel profile and details
class Hotel(models.Model):
    CATEGORY_CHOICES = [
        ("luxury", "Luxury Resort"),
        ("boutique", "Boutique Hotel"),
        ("budget", "Budget Stay"),
        ("homestay", "Cozy Homestay"),
        ("camp", "Camping & Glamping"),
    ]
    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_hotels",
    )
    name = models.CharField(max_length=200, default="New Hotel")
    category = models.CharField(
        max_length=50, choices=CATEGORY_CHOICES, default="budget"
    )
    city = models.CharField(max_length=100, default="", db_index=True)
    state = models.CharField(max_length=100, blank=True, default="", db_index=True)
    address = models.TextField(blank=True, default="")
    google_maps_url = models.URLField(max_length=500, blank=True, null=True)

    # Live Geolocation Fields
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    star_rating = models.PositiveSmallIntegerField(default=3)
    cover_image = models.TextField(blank=True, null=True)
    owner_name = models.CharField(max_length=150, blank=True, default="")
    owner_phone = models.CharField(max_length=20, blank=True, default="")
    owner_email = models.EmailField(blank=True, default="")
    has_wifi = models.BooleanField(default=True)
    has_parking = models.BooleanField(default=True)
    has_ac = models.BooleanField(default=True)
    has_heater = models.BooleanField(default=False)
    has_restaurant = models.BooleanField(default=True)
    has_breakfast = models.BooleanField(default=True)
    has_pool = models.BooleanField(default=False)
    commission_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=15.00
    )
    is_active = models.BooleanField(default=True)

    # Verification & Crime-Free Check
    is_verified = models.BooleanField(default=False, help_text="Admin approval needed")
    verification_doc = models.FileField(upload_to="verification_docs/", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.city})"


# Hotel gallery images
class HotelGalleryImage(models.Model):
    hotel = models.ForeignKey(
        Hotel, related_name="gallery_images", on_delete=models.CASCADE
    )
    image_url = models.URLField(max_length=500)
    caption = models.CharField(max_length=200, blank=True, default="")

    def __str__(self):
        return f"Image for {self.hotel.name}"


# Room types, occupancy and base pricing
class RoomType(models.Model):
    hotel = models.ForeignKey(
        Hotel, related_name="room_types", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=100, default="Standard Deluxe")
    description = models.TextField(blank=True, default="")
    max_guests = models.PositiveSmallIntegerField(default=2)
    is_available = models.BooleanField(default=True)
    max_adults = models.PositiveSmallIntegerField(default=2)
    max_children = models.PositiveSmallIntegerField(default=1)
    extra_bed_charge = models.DecimalField(
        max_digits=10, decimal_places=2, default=500.00
    )
    base_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=1800.00
    )
    weekend_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=2200.00
    )
    festival_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=2800.00
    )
    total_inventory = models.PositiveIntegerField(default=5)
    is_open = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.hotel.name} - {self.name}"


# Meal and rate plans (EP, CP, MAP, AP)
class MealPlan(models.Model):
    PLAN_CHOICES = [
        ("EP", "European Plan (Room Only)"),
        ("CP", "Continental Plan (Bed & Breakfast)"),
        ("MAP", "Modified American Plan (Breakfast + Lunch/Dinner)"),
        ("AP", "American Plan (All Meals Included)"),
    ]
    room_type = models.ForeignKey(
        RoomType, related_name="meal_plans", on_delete=models.CASCADE
    )
    plan_code = models.CharField(max_length=10, choices=PLAN_CHOICES, default="EP")
    additional_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("room_type", "plan_code")

    def __str__(self):
        return f"{self.room_type.hotel.name} - {self.room_type.name} ({self.get_plan_code_display()})"


# Discount coupons
class Coupon(models.Model):
    DISCOUNT_TYPES = [
        ("flat", "Flat Discount (₹)"),
        ("percentage", "Percentage Discount (%)"),
    ]
    code = models.CharField(max_length=50, unique=True)
    discount_type = models.CharField(
        max_length=20, choices=DISCOUNT_TYPES, default="flat"
    )
    discount_value = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    valid_till = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def is_valid(self):
        today = timezone.now().date()
        if not self.is_active:
            return False, "This coupon is inactive."
        if self.valid_till and self.valid_till < today:
            return False, "This coupon has expired."
        return True, "Coupon is valid."

    def calculate_discount(self, total_base_amount):
        amount = Decimal(str(total_base_amount))
        valid, msg = self.is_valid()
        if not valid:
            return Decimal("0.00"), msg
        if self.discount_type == "flat":
            discount = self.discount_value
        else:
            discount = (amount * self.discount_value) / Decimal("100.00")
        discount = min(discount, amount)
        return round(discount, 2), "Applied successfully."

    def __str__(self):
        return f"{self.code} ({self.get_discount_type_display()} {self.discount_value})"


# Booking records with auto MMY ID generation
class Booking(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending Payment"),
        ("CONFIRMED", "Confirmed"),
        ("CHECKED_IN", "Checked-In"),
        ("CHECKED_OUT", "Checked-Out"),
        ("CANCELLED", "Cancelled"),
        ("CANCELLED_BY_ADMIN", "Cancelled by Admin"),
        ("CANCELLED_BY_PARTNER", "Cancelled by Partner"),
        ("CANCELLED_BY_GUEST", "Cancelled by Guest"),
    ]
    PAYMENT_MODES = [
        ("PREPAID", "Full Online Payment"),
        ("PAY_AT_HOTEL", "Pay at Hotel"),
        ("PARTIAL", "Partial Deposit"),
    ]
    booking_id = models.CharField(
        max_length=50,
        unique=True,
        default=generate_mmy_booking_id,
        editable=False,
    )
    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="bookings",
        null=True,
        blank=True,
    )
    room_type = models.ForeignKey(
        RoomType, on_delete=models.SET_NULL, null=True, blank=True
    )
    meal_plan = models.ForeignKey(
        MealPlan, on_delete=models.SET_NULL, null=True, blank=True
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )
    guest_name = models.CharField(max_length=150, blank=True, default="Guest")
    guest_phone = models.CharField(max_length=20, blank=True, default="")
    guest_email = models.EmailField(blank=True, default="")
    check_in = models.DateField(null=True, blank=True)
    check_out = models.DateField(null=True, blank=True)
    destination_title = models.CharField(max_length=200, blank=True, default="")
    travel_date = models.DateField(null=True, blank=True)
    guests_count = models.PositiveIntegerField(default=2)
    rooms_count = models.PositiveIntegerField(default=1)
    adults_count = models.PositiveIntegerField(default=2)
    children_count = models.PositiveIntegerField(default=0)
    
    # Pricing Breakdown Fields
    base_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    meal_plan_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    extra_bed_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    discount_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    cgst_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    sgst_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    
    # === Partner Payout & Cancellation Reduction Fields ===
    partner_payout = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00,
        help_text="Net payout due to hotel partner (becomes 0 if partner cancels)"
    )
    cancelled_by = models.CharField(
        max_length=30, blank=True, null=True,
        choices=[("PARTNER", "Partner"), ("GUEST", "Guest"), ("ADMIN", "Admin")]
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, null=True)
    cancellation_penalty = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00,
        help_text="Penalty levied on partner if cancelled by partner"
    )

    payment_mode = models.CharField(
        max_length=20, choices=PAYMENT_MODES, default="PAY_AT_HOTEL"
    )
    payment_status = models.CharField(max_length=20, default="PENDING")
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default="CONFIRMED"
    )
    transaction_id = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.booking_id:
            for _ in range(5):
                self.booking_id = generate_mmy_booking_id()
                try:
                    super().save(*args, **kwargs)
                    return
                except IntegrityError:
                    if Booking.objects.filter(booking_id=self.booking_id).exists():
                        self.booking_id = None
                        continue
                    raise
            raise IntegrityError("Failed to generate unique booking ID after 5 attempts")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.booking_id} - {self.guest_name} ({self.status})"


# Emergency support requests
class EmergencySupport(models.Model):
    STATUS_CHOICES = [
        ("open", "Open / Active"),
        ("in_progress", "Staff Contacted Guest"),
        ("resolved", "Resolved"),
    ]
    name = models.CharField(max_length=150, default="User")
    phone = models.CharField(max_length=20, default="")
    location = models.CharField(max_length=200, default="")
    issue_details = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.status}"


# Hotel gallery media
class HotelMedia(models.Model):
    hotel = models.ForeignKey(
        Hotel, on_delete=models.CASCADE, related_name="media_gallery"
    )
    image_file = models.ImageField(upload_to="hotel_gallery/%Y/%m/", null=True, blank=True)
    caption = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.hotel.name} Photo ({self.id})"


# Reviews & Ratings
class HotelReview(models.Model):
    hotel = models.ForeignKey(
        Hotel, on_delete=models.CASCADE, related_name="reviews"
    )
    guest_name = models.CharField(max_length=120)
    rating = models.PositiveSmallIntegerField(
        default=5,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(5),
        ]
    )
    comment = models.TextField()
    booking_ref = models.CharField(max_length=50, blank=True, default="", db_index=True)
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['booking_ref'],
                condition=models.Q(booking_ref__gt=''),
                name='unique_review_per_booking'
            )
        ]

    def __str__(self):
        return f"{self.guest_name} ({self.rating}★) - {self.hotel.name}"

# ============================================================
# CABS MODULE
# ============================================================

class CabCategory(models.Model):
    """Cab vehicle types: Hatchback, Sedan, SUV, Tempo Traveller."""
    name = models.CharField(max_length=50)
    seating_capacity = models.PositiveIntegerField(default=4)
    luggage_capacity = models.PositiveIntegerField(default=2)
    ac_available = models.BooleanField(default=True)
    base_per_km_rate = models.DecimalField(max_digits=8, decimal_places=2, default=12.00)
    driver_allowance_per_day = models.DecimalField(max_digits=8, decimal_places=2, default=300.00)
    min_km_per_day = models.PositiveIntegerField(default=250)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cab Category"
        verbose_name_plural = "Cab Categories"
        ordering = ['seating_capacity', 'name']

    def __str__(self):
        return f"{self.name} ({self.seating_capacity} seater)"


class CabRoutePackage(models.Model):
    """Fixed route packages: Delhi→Jaipur, Airport transfers, etc."""
    TRIP_TYPES = [
        ('ONE_WAY', 'Outstation One-Way'),
        ('ROUND_TRIP', 'Outstation Round-Trip'),
        ('AIRPORT', 'Airport Transfer'),
        ('HOURLY', 'Hourly Rental'),
    ]
    trip_type = models.CharField(max_length=20, choices=TRIP_TYPES)
    from_city = models.CharField(max_length=100, db_index=True)
    to_city = models.CharField(max_length=100, blank=True, default="")
    distance_km = models.PositiveIntegerField(default=0)
    duration_hours = models.PositiveIntegerField(default=0)
    category = models.ForeignKey(CabCategory, on_delete=models.CASCADE, related_name='route_packages')
    fixed_price = models.DecimalField(max_digits=10, decimal_places=2)
    tolls_included = models.BooleanField(default=False)
    state_tax_included = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        if self.to_city:
            return f"{self.from_city} → {self.to_city} ({self.get_trip_type_display()})"
        return f"{self.from_city} - {self.get_trip_type_display()}"


def generate_cab_booking_id():
    """Generates unique MMY-CAB-XXXXXXXXXX format."""
    return f"MMY-CAB-{uuid.uuid4().hex[:10].upper()}"


class CabBooking(models.Model):
    """Cab booking with full pricing breakdown."""
    STATUS_CHOICES = [
        ('PENDING', 'Pending Payment'),
        ('ADVANCE_PENDING', 'Advance Submitted — Awaiting Verification'),
        ('CONFIRMED', 'Confirmed'),
        ('PARTNER_REQUESTED', 'Awaiting Partner Accept'),
        ('ASSIGNED', 'Driver Assigned'),
        ('ONGOING', 'Trip Ongoing'),
        ('COMPLETED', 'Completed'),
        ('NO_SHOW', 'Customer No-Show'),
        ('CANCELLED', 'Cancelled'),
        ('CANCELLED_BY_ADMIN', 'Cancelled by Admin'),
        ('CANCELLED_BY_GUEST', 'Cancelled by Guest'),
    ]
    TRIP_TYPES = CabRoutePackage.TRIP_TYPES
    PAYMENT_MODES = [
        ('PAY_TO_DRIVER', 'Pay to Driver'),
        ('PREPAID', 'Full Online Payment'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PAID', 'Paid'),
        ('REFUNDED', 'Refunded'),
    ]

    booking_id = models.CharField(max_length=50, unique=True, default=generate_cab_booking_id, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='cab_bookings')
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name='cab_bookings')
    category = models.ForeignKey(CabCategory, on_delete=models.PROTECT, related_name='bookings')
    route_package = models.ForeignKey(CabRoutePackage, on_delete=models.SET_NULL, null=True, blank=True)

    trip_type = models.CharField(max_length=20, choices=TRIP_TYPES)
    pickup_city = models.CharField(max_length=100)
    drop_city = models.CharField(max_length=100, blank=True, default="")
    pickup_address = models.TextField()
    pickup_datetime = models.DateTimeField()
    return_date = models.DateField(null=True, blank=True)
    distance_km = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    waiting_hours = models.PositiveIntegerField(default=0)

    guest_name = models.CharField(max_length=120)
    guest_phone = models.CharField(max_length=15)
    guest_email = models.EmailField(blank=True, default="")

    base_fare = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    driver_allowance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    toll_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    night_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    state_tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    payment_mode = models.CharField(max_length=20, choices=PAYMENT_MODES, default='PAY_TO_DRIVER')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='PENDING')
    transaction_id = models.CharField(max_length=100, blank=True, null=True)

    driver_name = models.CharField(max_length=100, blank=True, default="")
    driver_phone = models.CharField(max_length=15, blank=True, default="")
    vehicle_number = models.CharField(max_length=20, blank=True, default="")

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING')
    cancelled_by = models.CharField(max_length=30, blank=True, null=True,
        choices=[("GUEST", "Guest"), ("ADMIN", "Admin")])
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, null=True)
    cancellation_penalty = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Partner Wallet System Fields
    assigned_partner = models.ForeignKey(
        'CabPartner', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_bookings'
    )
    commission_deducted = models.BooleanField(default=False)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # ============ ADVANCE PAYMENT (20%) ============
    advance_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00,
        help_text="20% advance paid by customer online"
    )
    advance_paid = models.BooleanField(default=False)
    advance_paid_at = models.DateTimeField(null=True, blank=True)
    advance_utr = models.CharField(max_length=100, blank=True, null=True)
    advance_utr_submitted_at = models.DateTimeField(null=True, blank=True)
    
    balance_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00,
        help_text="Balance to pay driver in cash (80% minus waiting)"
    )
    
    # ============ WAITING CHARGES ============
    waiting_minutes = models.IntegerField(default=0, help_text="Extra waiting beyond 15 min free")
    waiting_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # ============ DRIVER SETTLEMENT ============
    driver_wallet_credit = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00,
        help_text="Amount admin owes driver (advance - commission)"
    )
    driver_settled = models.BooleanField(default=False)
    driver_settled_at = models.DateTimeField(null=True, blank=True)
    booking_fee = models.DecimalField(max_digits=8, decimal_places=2, default=99.00,
        help_text="Convenience fee charged to customer")
    booking_fee_paid = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cab Booking"
        verbose_name_plural = "Cab Bookings"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.booking_id} - {self.guest_name} ({self.status})"


class CabReview(models.Model):
    """Review for a completed cab booking."""
    booking = models.OneToOneField(CabBooking, on_delete=models.CASCADE, related_name='review')
    guest_name = models.CharField(max_length=120)
    rating = models.PositiveSmallIntegerField(default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True, default="")
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cab Review"
        verbose_name_plural = "Cab Reviews"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.guest_name} ({self.rating}★) - {self.booking.booking_id}"


# ============================================================
# CAB PARTNER WALLET SYSTEM
# ============================================================

class CabPartner(models.Model):
    """Cab driver/vendor with prepaid wallet for commission."""
    PARTNER_TYPE = [
        ('INDIVIDUAL', 'Individual Driver'),
        ('VENDOR', 'Cab Vendor/Agency'),
    ]
    STATUS = [
        ('PENDING', 'Pending Verification'),
        ('ACTIVE', 'Active'),
        ('BLOCKED', 'Blocked'),
    ]
    
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=15, unique=True)
    email = models.EmailField(blank=True)
    partner_type = models.CharField(max_length=20, choices=PARTNER_TYPE, default='INDIVIDUAL')
    
    company_name = models.CharField(max_length=200, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    
    aadhar_number = models.CharField(max_length=20, blank=True)
    pan_number = models.CharField(max_length=15, blank=True)
    driving_license = models.CharField(max_length=30, blank=True)
    dl_document = models.FileField(upload_to='partner_kyc/dl/', blank=True, null=True)
    rc_document = models.FileField(upload_to='partner_kyc/rc/', blank=True, null=True)
    insurance_document = models.FileField(upload_to='partner_kyc/insurance/', blank=True, null=True)
    
    wallet_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00,
        help_text="Prepaid balance — commission auto-deducted")
    min_wallet_required = models.DecimalField(max_digits=8, decimal_places=2, default=500.00,
        help_text="Minimum balance to receive new bookings")
    
    commission_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=10.00,
        help_text="Default 10% commission per booking")
    
    status = models.CharField(max_length=20, choices=STATUS, default='PENDING')
    is_available = models.BooleanField(default=True)
    
    bank_account_name = models.CharField(max_length=150, blank=True)
    bank_account_number = models.CharField(max_length=30, blank=True)
    bank_ifsc = models.CharField(max_length=15, blank=True)
    # UTR pending (for self-recharge verification)
    pending_utr = models.CharField(max_length=100, blank=True, null=True, help_text="UTR submitted by partner")
    pending_utr_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    pending_utr_submitted_at = models.DateTimeField(null=True, blank=True)
    
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='cab_partner')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Cab Partner"
        verbose_name_plural = "Cab Partners"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} ({self.phone}) - ₹{self.wallet_balance}"
    
    @property
    def can_accept_booking(self):
        return (self.status == 'ACTIVE' and self.is_available 
                and self.wallet_balance >= self.min_wallet_required)


class PartnerWalletTransaction(models.Model):
    """Track all wallet credit/debit transactions."""
    TXN_TYPE = [
        ('RECHARGE', 'Recharge'),
        ('COMMISSION', 'Commission Deduction'),
        ('REFUND', 'Refund (Cancellation)'),
        ('PAYOUT', 'Payout to Bank'),
        ('ADJUSTMENT', 'Manual Adjustment'),
    ]
    
    partner = models.ForeignKey(CabPartner, on_delete=models.CASCADE, related_name='wallet_transactions')
    txn_type = models.CharField(max_length=20, choices=TXN_TYPE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    balance_before = models.DecimalField(max_digits=10, decimal_places=2)
    balance_after = models.DecimalField(max_digits=10, decimal_places=2)
    
    booking = models.ForeignKey(CabBooking, on_delete=models.SET_NULL, null=True, blank=True, related_name='wallet_txns')
    reference_id = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Wallet Transaction"
        verbose_name_plural = "Wallet Transactions"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.partner.name} - {self.txn_type} ₹{self.amount}"