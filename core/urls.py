from django.urls import path
from . import views

urlpatterns = [
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
    
    # --- Hotel Public Reviews & Gallery ---
    path('hotel/<int:hotel_id>/review/', views.submit_hotel_review, name='submit_hotel_review'),
    path('hotel/<int:hotel_id>/gallery/', views.hotel_gallery, name='hotel_gallery'),

    # --- AJAX Endpoints ---
    path('api/apply-coupon/', views.apply_coupon_ajax, name='apply_coupon_ajax'),

    # --- Partner Auth & Dashboard ---
    path('partner/login/', views.partner_login, name='partner_login'),
    path('partner/dashboard/', views.partner_dashboard, name='partner_dashboard'),

    # --- Operations / Portal Auth & Dashboard ---
    path('portal/login/', views.portal_login, name='portal_login'),
    path('portal/logout/', views.portal_logout, name='portal_logout'),
    path('portal/dashboard/', views.portal_dashboard, name='portal_dashboard'),
    path('portal/customers/', views.portal_customers, name='portal_customers'),

    # --- Hotel & Room Management ---
    path('portal/hotels/', views.portal_hotels, name='portal_hotels'),
    path('portal/hotels/add/', views.portal_hotel_add, name='portal_hotel_add'),
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
]