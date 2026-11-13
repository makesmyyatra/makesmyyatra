from django.contrib import admin
from .models import Hotel, HotelGalleryImage, RoomType, MealPlan, Coupon, Booking, EmergencySupport,CabCategory, CabRoutePackage, CabBooking, CabReview

class HotelGalleryImageInline(admin.TabularInline):
    model = HotelGalleryImage
    extra = 1

class RoomTypeInline(admin.TabularInline):
    model = RoomType
    extra = 1

class MealPlanInline(admin.TabularInline):
    model = MealPlan
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
    inlines = [MealPlanInline]

@admin.register(MealPlan)
class MealPlanAdmin(admin.ModelAdmin):
    list_display = ('room_type', 'plan_code', 'additional_price', 'is_active')
    list_filter = ('plan_code', 'is_active')

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'discount_type', 'discount_value', 'valid_till', 'is_active')
    list_filter = ('discount_type', 'is_active')
    search_fields = ('code',)

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('booking_id', 'guest_name', 'hotel', 'total_amount', 'payment_mode', 'status', 'created_at')
    list_filter = ('status', 'payment_mode', 'created_at')
    search_fields = ('booking_id', 'guest_name', 'guest_phone', 'guest_email', 'transaction_id')

@admin.register(EmergencySupport)
class EmergencySupportAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'location', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('name', 'phone', 'location')

@admin.register(CabCategory)
class CabCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'seating_capacity', 'base_per_km_rate', 'driver_allowance_per_day', 'is_active', 'created_at')
    list_filter = ('is_active', 'ac_available')
    search_fields = ('name',)
    list_editable = ('is_active',)


@admin.register(CabRoutePackage)
class CabRoutePackageAdmin(admin.ModelAdmin):
    list_display = ('from_city', 'to_city', 'trip_type', 'category', 'fixed_price', 'is_active')
    list_filter = ('trip_type', 'is_active', 'tolls_included')
    search_fields = ('from_city', 'to_city')


@admin.register(CabBooking)
class CabBookingAdmin(admin.ModelAdmin):
    list_display = ('booking_id', 'guest_name', 'pickup_city', 'trip_type', 'total_amount', 'status', 'created_at')
    list_filter = ('status', 'trip_type', 'payment_status', 'payment_mode')
    search_fields = ('booking_id', 'guest_name', 'guest_phone', 'pickup_city')
    readonly_fields = ('booking_id', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Booking Info', {
            'fields': ('booking_id', 'status', 'user', 'category', 'route_package')
        }),
        ('Trip Details', {
            'fields': ('trip_type', 'pickup_city', 'drop_city', 'pickup_address', 'pickup_datetime', 'return_date', 'distance_km', 'waiting_hours')
        }),
        ('Guest Details', {
            'fields': ('guest_name', 'guest_phone', 'guest_email')
        }),
        ('Pricing', {
            'fields': ('base_fare', 'driver_allowance', 'toll_charges', 'night_charge', 'state_tax', 'gst_amount', 'discount_amount', 'total_amount', 'coupon')
        }),
        ('Payment', {
            'fields': ('payment_mode', 'payment_status', 'transaction_id')
        }),
        ('Driver', {
            'fields': ('driver_name', 'driver_phone', 'vehicle_number')
        }),
        ('Cancellation', {
            'fields': ('cancelled_by', 'cancelled_at', 'cancellation_reason', 'cancellation_penalty'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(CabReview)
class CabReviewAdmin(admin.ModelAdmin):
    list_display = ('booking', 'guest_name', 'rating', 'is_approved', 'created_at')
    list_filter = ('rating', 'is_approved')
    list_editable = ('is_approved',)
    search_fields = ('guest_name', 'booking__booking_id')