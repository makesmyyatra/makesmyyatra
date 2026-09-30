from django.contrib import admin
from .models import Hotel, HotelGalleryImage, RoomType, MealPlan, Coupon, Booking, EmergencySupport

class HotelGalleryImageInline(admin.TabularInline):
    model = HotelGalleryImage
    extra = 1

class RoomTypeInline(admin.TabularInline):
    model = RoomType
    extra = 1

@admin.register(Hotel)
class HotelAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'city', 'star_rating', 'is_active')
    list_filter = ('category', 'city', 'is_active', 'star_rating')
    search_fields = ('name', 'city', 'address', 'owner_name', 'owner_phone')
    inlines = [HotelGalleryImageInline, RoomTypeInline]

@admin.register(RoomType)
class RoomTypeAdmin(admin.ModelAdmin):
    list_display = ('hotel', 'name', 'base_price', 'total_inventory', 'is_available')
    list_filter = ('hotel', 'is_available')
    search_fields = ('name', 'hotel__name')

@admin.register(MealPlan)
class MealPlanAdmin(admin.ModelAdmin):
    list_display = ('room_type', 'plan_code', 'additional_price')
    list_filter = ('plan_code',)

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'discount_type', 'discount_value', 'valid_till', 'is_active')
    list_filter = ('discount_type', 'is_active')
    search_fields = ('code',)

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('booking_id', 'guest_name', 'guest_phone', 'hotel', 'status', 'final_amount', 'created_at')
    list_filter = ('status', 'payment_mode', 'is_paid')
    search_fields = ('booking_id', 'guest_name', 'guest_phone', 'hotel__name')

@admin.register(EmergencySupport)
class EmergencySupportAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'location', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('name', 'phone', 'location')