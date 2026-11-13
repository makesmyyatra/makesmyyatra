from django.urls import path
from core.views import health_check
from . import views
from . import cabs_views

urlpatterns = [
    
    path('health/', health_check, name='health_check'),
    # --- Public Pages & Booking Engine ---
    path('', views.index, name='index'),
    path('dashboard/', views.user_dashboard, name='user_dashboard'),
    path('taxi/', views.taxi, name='taxi'),
    path('support-request/', views.support_request, name='support_request'),
    path('book-hotel/', views.book_hotel, name='book_hotel'),
    path('track-booking/', views.track_booking, name='track_booking'),
    path('booking/<str:booking_id>/', views.booking_detail, name='booking_detail'),
    path('booking/voucher/<str:booking_id>/', views.booking_voucher, name='booking_voucher'),
    path('booking/<str:booking_id>/voucher/', views.download_customer_voucher, name='download_customer_voucher'),
    path('booking/download-voucher/<str:booking_id>/', views.download_customer_voucher, name='download_customer_voucher'),
    path('booking/<str:booking_id>/cancel/', views.cancel_booking, name='cancel_booking'),
    
    # --- Hotel Public Reviews & Gallery ---
    path('hotel/<int:hotel_id>/review/submit/', views.submit_hotel_review, name='submit_hotel_review'),
    path('hotel/<int:hotel_id>/gallery/', views.hotel_gallery, name='hotel_gallery'),

    # --- AJAX Endpoints ---
    path('api/apply-coupon/', views.apply_coupon_ajax, name='apply_coupon_ajax'),

    
    # urgent help endpoints for portal
    path('portal/urgent-help/<int:support_id>/resolve/',views.portal_urgent_help_resolve,name='portal_urgent_help_resolve',),
    path('portal/urgent-help/<int:support_id>/delete/',views.portal_urgent_help_delete,name='portal_urgent_help_delete',),
    path('portal/urgent-help/', views.portal_urgent_help, name='portal_urgent_help'),

    # --- Partner Auth & Dashboard ---
    path('partner/register/', views.partner_register, name='partner_register'),
    path('partner/add-property/', views.add_property, name='add_property'),
    path('partner/login/', views.partner_login, name='partner_login'),
    path('partner/logout/', views.partner_logout, name='partner_logout'),
    path('partner/dashboard/', views.partner_dashboard, name='partner_dashboard'),
    path('partner/property/<int:hotel_id>/edit/', views.partner_edit_property, name='partner_edit_property'),
    path('partner/hotel/<int:hotel_id>/gallery/', views.partner_manage_gallery, name='partner_manage_gallery'),
    path('partner/gallery/<int:media_id>/delete/', views.partner_gallery_image_delete, name='partner_gallery_image_delete'),
    path('partner/hotel/<int:hotel_id>/delete/', views.delete_hotel, name='delete_hotel'),
    path('partner/room/<int:room_id>/quick-update/', views.partner_quick_update_room, name='partner_quick_update_room'),
    path('partner/booking/<str:booking_id>/status/<str:new_status>/', views.partner_update_booking_status, name='partner_update_booking_status'),
    path('partner/booking/<int:booking_id>/cancel/', views.partner_cancel_booking, name='partner_cancel_booking'),

    # --- Operations / Portal Auth & Dashboard ---
    path('portal/login/', views.portal_login, name='portal_login'),
    path('portal/logout/', views.portal_logout, name='portal_logout'),
    path('portal/dashboard/', views.portal_dashboard, name='portal_dashboard'),
    path('portal/customers/', views.portal_customers, name='portal_customers'),
    path('portal/booking/<str:booking_id>/invoice/', views.portal_booking_invoice, name='portal_booking_invoice'),

    # --- Hotel & Room Management ---
    path('portal/hotels/', views.portal_hotels, name='portal_hotels'),
    path('portal/hotels/add/', views.portal_hotel_add, name='portal_hotel_add'),
    path('portal/hotel/<int:hotel_id>/toggle-verification/', views.portal_toggle_hotel_verification, name='portal_toggle_hotel_verification'),
    path('portal/hotels/edit/<int:hotel_id>/', views.portal_hotel_edit, name='portal_hotel_edit'),
    path('portal/hotels/delete/<int:hotel_id>/', views.portal_hotel_delete, name='portal_hotel_delete'),
    
    path('portal/room-types/', views.portal_all_rooms, name='portal_room_types'),
    path('portal/hotels/<int:hotel_id>/rooms/', views.portal_room_manage, name='portal_room_manage'),
    path('portal/hotels/<int:hotel_id>/rooms/create/', views.portal_room_create, name='portal_room_create'),
    path('portal/hotels/<int:hotel_id>/rooms/<int:room_id>/delete/', views.portal_room_delete, name='portal_room_delete'),
    path('portal/rooms/<int:room_id>/toggle/', views.portal_room_toggle_status, name='portal_room_toggle_status'),

    # --- Media Gallery ---
    path('portal/hotel/<int:hotel_id>/gallery/', views.portal_hotel_gallery_manage, name='portal_hotel_gallery_manage'),
    path('portal/gallery/<int:media_id>/delete/', views.portal_gallery_image_delete, name='portal_gallery_image_delete'),

    # --- Bookings Management ---
    path('portal/bookings/', views.portal_bookings, name='portal_bookings'),
    path('portal/bookings/export-excel/', views.export_bookings_excel, name='export_bookings_excel'),
    path('portal/bookings/actions/', views.portal_booking_actions, name='portal_booking_actions'),
    path('portal/bookings/<str:booking_id>/update/', views.portal_booking_update, name='portal_booking_update'),
    path('portal/bookings/<int:booking_id>/delete/', views.portal_booking_delete, name='portal_booking_delete'),
    path('portal/bookings/<int:booking_id>/status/', views.portal_booking_status, name='portal_booking_status'),
    path('portal/bookings/status/<str:booking_id>/<str:new_status>/', views.portal_booking_status_change, name='portal_booking_status_change'),
    path('portal/booking/<str:booking_id>/invoice/', views.portal_booking_invoice, name='portal_booking_invoice'),
    path('portal/bookings/<str:booking_id>/send-whatsapp/', views.portal_booking_send_whatsapp, name='portal_booking_send_whatsapp'),

    # --- Coupons & Meal Plans ---
    path('portal/coupons/', views.portal_coupons, name='portal_coupons'),
    path('portal/coupons/<int:coupon_id>/delete/', views.portal_coupon_delete, name='portal_coupon_delete'),
    path('portal/meal-plans/', views.portal_meal_plans, name='portal_meal_plans'),

    # --- Reviews Moderation ---
    path('portal/reviews/', views.portal_reviews, name='portal_reviews'),
    path('portal/reviews/<int:review_id>/toggle/', views.portal_review_toggle, name='portal_review_toggle'),
    path('portal/reviews/<int:review_id>/delete/', views.portal_review_delete, name='portal_review_delete'),

    # --- Urgent Help / Emergency ---
    path('portal/urgent-help/', views.portal_urgent_help, name='portal_urgent_help'),
    path('portal/urgent-help/<int:support_id>/resolve/', views.portal_urgent_help_resolve, name='portal_urgent_help_resolve'),
    path('portal/reports/revenue/', views.portal_revenue_report, name='portal_revenue_report'),
    path('portal/bookings/<str:booking_id>/cancel/',views.portal_booking_cancel,name='portal_booking_cancel',),

    # CABS MODULE
    # Public cab URLs
    path('cabs/', cabs_views.cab_list, name='cab_list'),
    path('cabs/search/', cabs_views.cab_search, name='cab_search'),
    path('cabs/book/', cabs_views.book_cab, name='book_cab'),
    path('cabs/booking/<str:booking_id>/', cabs_views.cab_booking_detail, name='cab_booking_detail'),
    path('cabs/booking/<str:booking_id>/cancel/', cabs_views.cancel_cab_booking, name='cancel_cab_booking'),
    path('cabs/booking/<str:booking_id>/review/', cabs_views.submit_cab_review, name='submit_cab_review'),
    path('cabs/booking/<str:booking_id>/invoice/', cabs_views.cab_invoice_pdf, name='cab_invoice_pdf'),
    path('cabs/track/', cabs_views.track_cab_booking, name='track_cab_booking'),
    path('cabs/coupon/apply/', cabs_views.apply_cab_coupon_ajax, name='apply_cab_coupon_ajax'),
    
    # Admin cab URLs
    path('portal/cabs/', cabs_views.portal_cabs, name='portal_cabs'),
    path('portal/cabs/add/', cabs_views.portal_cab_add, name='portal_cab_add'),
    path('portal/cabs/<int:cab_id>/edit/', cabs_views.portal_cab_edit, name='portal_cab_edit'),
    path('portal/cabs/<int:cab_id>/delete/', cabs_views.portal_cab_delete, name='portal_cab_delete'),
    path('portal/cab-bookings/', cabs_views.portal_cab_bookings, name='portal_cab_bookings'),
    path('portal/cab-bookings/<str:booking_id>/', cabs_views.portal_cab_booking_detail, name='portal_cab_booking_detail'),
    path('portal/cab-bookings/<str:booking_id>/update/', cabs_views.portal_cab_booking_update, name='portal_cab_booking_update'),
    path('portal/cab-bookings/<str:booking_id>/cancel/', cabs_views.portal_cab_cancel, name='portal_cab_cancel'),
    path('portal/cab-revenue/', cabs_views.portal_cab_revenue, name='portal_cab_revenue'),

    
]