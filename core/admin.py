from django.contrib import admin
from .models import Hotel, HotelGalleryImage, RoomType, MealPlan, Coupon, Booking, EmergencySupport,CabCategory, CabRoutePackage, CabBooking, CabReview,CabPartner, PartnerWalletTransaction

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

# ============================================================
# CABS ADMIN
# ============================================================

@admin.register(CabCategory)
class CabCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'seating_capacity', 'base_per_km_rate', 'driver_allowance_per_day', 'is_active')
    list_filter = ('is_active', 'ac_available')
    search_fields = ('name',)
    list_editable = ('is_active',)


@admin.register(CabRoutePackage)
class CabRoutePackageAdmin(admin.ModelAdmin):
    list_display = ('from_city', 'to_city', 'trip_type', 'category', 'fixed_price', 'is_active')
    list_filter = ('trip_type', 'is_active')
    search_fields = ('from_city', 'to_city')


@admin.register(CabBooking)
class CabBookingAdmin(admin.ModelAdmin):
    list_display = ('booking_id', 'guest_name', 'pickup_city', 'trip_type', 'total_amount', 'status', 'assigned_partner', 'commission_deducted')
    list_filter = ('status', 'trip_type', 'payment_status', 'commission_deducted')
    search_fields = ('booking_id', 'guest_name', 'guest_phone')
    readonly_fields = ('booking_id', 'created_at', 'updated_at')


@admin.register(CabReview)
class CabReviewAdmin(admin.ModelAdmin):
    list_display = ('booking', 'guest_name', 'rating', 'is_approved', 'created_at')
    list_filter = ('rating', 'is_approved')
    list_editable = ('is_approved',)


@admin.register(CabPartner)
class CabPartnerAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'partner_type', 'wallet_balance', 'commission_percentage', 'status', 'is_available')
    list_filter = ('status', 'partner_type', 'is_available')
    search_fields = ('name', 'phone', 'email')
    list_editable = ('is_available',)


@admin.register(PartnerWalletTransaction)
class PartnerWalletTransactionAdmin(admin.ModelAdmin):
    list_display = ('partner', 'txn_type', 'amount', 'balance_after', 'created_at')
    list_filter = ('txn_type',)
    search_fields = ('partner__name', 'reference_id')
    readonly_fields = ('created_at',)