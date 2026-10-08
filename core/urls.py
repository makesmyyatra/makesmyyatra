from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.user_dashboard, name='user_dashboard'),
    path('booking/<str:booking_id>/', views.booking_detail, name='booking_detail'),

    # Public Client Facing
    path('', views.index, name='index'),
    path('taxi/', views.taxi, name='taxi'),
    path('support-request/', views.support_request, name='support_request'),
    path('book-hotel/', views.book_hotel, name='book_hotel'),

    # Custom Staff & Admin Portal Auth
    path('portal/login/', views.portal_login, name='portal_login'),
    path('portal/logout/', views.portal_logout, name='portal_logout'),
    path('portal/dashboard/', views.portal_dashboard, name='portal_dashboard'),

    # Hotel Inventory CRUD
    path('portal/hotels/', views.portal_hotels, name='portal_hotels'),
    path('portal/hotels/add/', views.portal_hotel_add, name='portal_hotel_add'),
    path('portal/hotels/edit/<int:hotel_id>/', views.portal_hotel_edit, name='portal_hotel_edit'),
    path('portal/hotels/delete/<int:hotel_id>/', views.portal_hotel_delete, name='portal_hotel_delete'),

    # Bookings, Status & Excel Export
    path('portal/bookings/', views.portal_bookings, name='portal_bookings'),
    path('portal/bookings/<str:booking_id>/update/', views.portal_booking_update, name='portal_booking_update'),
    path('portal/bookings/status/<str:booking_id>/<str:new_status>/', views.portal_booking_status_change, name='portal_booking_status_change'),
    path('portal/bookings/export-excel/', views.export_bookings_excel, name='export_bookings_excel'),

    # Urgent Support Desk
    path('portal/urgent-help/', views.portal_urgent_help, name='portal_urgent_help'),
    path('portal/urgent-help/<int:support_id>/resolve/', views.portal_urgent_help_resolve, name='portal_urgent_help_resolve'),
    path('portal/bookings/<int:booking_id>/status/', views.portal_booking_status, name='portal_booking_status'),

    path('custom-admin/hotels/', views.portal_hotels, name='portal_hotels'),
    path('custom-admin/hotels/add/', views.portal_hotel_add, name='portal_hotel_add'),
    path('custom-admin/hotels/edit/<int:hotel_id>/', views.portal_hotel_edit, name='portal_hotel_edit'),
    path('custom-admin/hotels/delete/<int:hotel_id>/', views.portal_hotel_delete, name='portal_hotel_delete'),
]