import uuid
from django.db import models
from django.contrib.auth.models import User

class Hotel(models.Model):
    CATEGORY_CHOICES = [
        ('luxury', 'Luxury Resort'),
        ('boutique', 'Boutique Hotel'),
        ('budget', 'Budget Stay'),
        ('homestay', 'Cozy Homestay'),
    ]

    name = models.CharField(max_length=200, default='New Hotel')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='budget')
    city = models.CharField(max_length=100, default='Una')
    address = models.TextField(blank=True, default='')
    google_maps_url = models.URLField(max_length=500, blank=True, null=True)
    star_rating = models.PositiveSmallIntegerField(default=3)
    cover_image = models.URLField(max_length=500, blank=True, default='')

    # Amenities Booleans
    has_wifi = models.BooleanField(default=True)
    has_parking = models.BooleanField(default=True)
    has_pool = models.BooleanField(default=False)
    has_restaurant = models.BooleanField(default=True)
    has_ac = models.BooleanField(default=True)
    has_heater = models.BooleanField(default=False)

    # Operational/Staff Info
    owner_name = models.CharField(max_length=150, blank=True, default='')
    owner_phone = models.CharField(max_length=20, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.city})"


class HotelGalleryImage(models.Model):
    hotel = models.ForeignKey(Hotel, related_name='gallery_images', on_delete=models.CASCADE)
    image_url = models.URLField(max_length=500)
    caption = models.CharField(max_length=200, blank=True, default='')

    def __str__(self):
        return f"Image for {self.hotel.name}"


class RoomType(models.Model):
    hotel = models.ForeignKey(Hotel, related_name='room_types', on_delete=models.CASCADE)
    name = models.CharField(max_length=100, default='Standard Room')
    description = models.TextField(blank=True, default='')
    max_guests = models.PositiveSmallIntegerField(default=2)
    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=1500.00)
    total_inventory = models.PositiveIntegerField(default=5)
    is_available = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.hotel.name} - {self.name}"


class MealPlan(models.Model):
    PLAN_CHOICES = [
        ('EP', 'European Plan (Room Only)'),
        ('CP', 'Continental Plan (Bed & Breakfast)'),
        ('MAP', 'Modified American Plan (Breakfast + Lunch/Dinner)'),
        ('AP', 'American Plan (All Meals Included)'),
    ]
    room_type = models.ForeignKey(RoomType, related_name='meal_plans', on_delete=models.CASCADE)
    plan_code = models.CharField(max_length=10, choices=PLAN_CHOICES, default='EP')
    additional_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return f"{self.room_type.name} - {self.get_plan_code_display()}"


class Coupon(models.Model):
    DISCOUNT_TYPES = [
        ('flat', 'Flat Discount (₹)'),
        ('percentage', 'Percentage Discount (%)'),
    ]
    code = models.CharField(max_length=50, unique=True)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPES, default='flat')
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    valid_till = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.code


class Booking(models.Model):
    STATUS_CHOICES = [
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
        ('checked_in', 'Checked In'),
    ]
    PAYMENT_MODES = [
        ('pay_at_hotel', 'Pay at Hotel'),
        ('online', 'Online Payment'),
    ]

    booking_id = models.CharField(max_length=30, unique=True, default='MMY-HTL-DEFAULT')
    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE)
    room_type = models.ForeignKey(RoomType, on_delete=models.SET_NULL, null=True, blank=True)
    meal_plan = models.ForeignKey(MealPlan, on_delete=models.SET_NULL, null=True, blank=True)

    guest_name = models.CharField(max_length=150, default='Guest')
    guest_email = models.EmailField(blank=True, default='')
    guest_phone = models.CharField(max_length=20, default='0000000000')

    check_in = models.DateField()
    check_out = models.DateField()
    guests_count = models.PositiveIntegerField(default=1)

    base_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    final_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    payment_mode = models.CharField(max_length=30, choices=PAYMENT_MODES, default='pay_at_hotel')
    is_paid = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='confirmed')
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.booking_id or self.booking_id == 'MMY-HTL-DEFAULT':
            self.booking_id = f"MMY-HTL-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.booking_id} - {self.guest_name}"


class EmergencySupport(models.Model):
    STATUS_CHOICES = [
        ('open', 'Open / Active'),
        ('in_progress', 'Staff Contacted Guest'),
        ('resolved', 'Resolved'),
    ]
    name = models.CharField(max_length=150, default='User')
    phone = models.CharField(max_length=20, default='')
    location = models.CharField(max_length=200, default='Himachal Pradesh')
    issue_details = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.status}"