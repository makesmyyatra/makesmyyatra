from decimal import Decimal
import random
import string
import uuid
from django.contrib.auth.models import User
from django.db import models


# Helper for Booking Reference
def generate_mmy_booking_id():
  random_code = "".join(random.choices(string.digits, k=4))
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
  city = models.CharField(max_length=100, default="Una")
  address = models.TextField(blank=True, default="")
  google_maps_url = models.URLField(max_length=500, blank=True, null=True)
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

from decimal import Decimal
from django.db import models
from django.utils import timezone


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

    # Discount can never exceed total base amount
    discount = min(discount, amount)
    return round(discount, 2), "Applied successfully."

  def __str__(self):
    return f"{self.code} ({self.get_discount_type_display()} - {self.discount_value})"

# Booking records with auto MMY ID generation
class Booking(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending Payment"),
        ("CONFIRMED", "Confirmed"),
        ("CHECKED_IN", "Checked-In"),
        ("CHECKED_OUT", "Checked-Out"),
        ("CANCELLED", "Cancelled"),
    ]
    PAYMENT_MODES = [
        ("PREPAID", "Full Online Payment"),
        ("PAY_AT_HOTEL", "Pay at Hotel"),
        ("PARTIAL", "Partial Deposit"),
    ]

    # Editable=False taaki auto generate ho
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

    # Coupon ForeignKey Link (Fixes TypeError in book_hotel)
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

    adults_count = models.PositiveSmallIntegerField(default=2)
    children_count = models.PositiveSmallIntegerField(default=0)

    base_amount = models.DecimalField(
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

    payment_mode = models.CharField(
        max_length=20, choices=PAYMENT_MODES, default="PAY_AT_HOTEL"
    )
    payment_status = models.CharField(max_length=20, default="CONFIRMED")
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="CONFIRMED"
    )

    transaction_id = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Ensure ID uniqueness on save
        if not self.booking_id:
            while True:
                new_id = generate_mmy_booking_id()
                if not Booking.objects.filter(booking_id=new_id).exists():
                    self.booking_id = new_id
                    break
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
  location = models.CharField(max_length=200, default="Himachal Pradesh")
  issue_details = models.TextField(blank=True, default="")
  status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
  created_at = models.DateTimeField(auto_now_add=True)

  def __str__(self):
    return f"{self.name} - {self.status}"

# =================HOTEL MEDIA GALLERY =================
class HotelMedia(models.Model):
    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, related_name='media_gallery')
    # Device se direct uploaded photo save karne ke liye
    image_file = models.ImageField(upload_to='hotel_gallery/%Y/%m/', null=True, blank=True)
    caption = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.hotel.name} - Photo ({self.id})"

# ================= REVIEWS & RATINGS =================
class HotelReview(models.Model):
    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, related_name='reviews')
    guest_name = models.CharField(max_length=120)
    rating = models.PositiveSmallIntegerField(default=5)  # 1 to 5 Stars
    comment = models.TextField()
    is_approved = models.BooleanField(default=True)  # Admin moderation flag
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.guest_name} ({self.rating}★) - {self.hotel.name}"