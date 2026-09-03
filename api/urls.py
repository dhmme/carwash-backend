from django.urls import path
from .views import (
    hello_view,
    service_list,
    car_list_create,
    booking_list_create,
    booked_slots, 
    worker_bookings,
    register_view,
    login_view,
    logout_view,
    update_booking_status,
    location_list_create,
    add_on_list,
    vehicle_category_list,
    manager_dashboard, manager_services, manager_service_detail,
    manager_add_ons, manager_add_on_detail, manager_categories,
    manager_category_detail, manager_bookings, manager_invoices,
    manager_workers, manager_worker_detail,
    manager_ledger, manager_expenses, manager_expense_detail,
    invoice_print_view,
)

urlpatterns = [
    path('hello/', hello_view, name='hello'),
    path('auth/register/', register_view, name='register'),
    path('auth/login/', login_view, name='login'),
    path('auth/logout/', logout_view, name='logout'),

    # Services
    path('services/', service_list, name='service-list'),
    path('add-ons/', add_on_list, name='add-on-list'),
    path('vehicle-categories/', vehicle_category_list, name='vehicle-category-list'),

    # Cars
    path('cars/', car_list_create, name='car-list-create'),
    path('locations/', location_list_create, name='location-list-create'),

    # Bookings
    path('bookings/', booking_list_create, name='booking-list-create'),
    path('invoices/<uuid:token>/print/', invoice_print_view, name='invoice-print'),

    # Booked time slots for a given date
    path('booked-slots/', booked_slots, name='booked-slots'),  

    # For Worker
    path('worker/bookings/', worker_bookings, name='worker-bookings'),
    path('worker/bookings/<int:booking_id>/status/', update_booking_status,
         name='worker-booking-status'),

    path('manager/dashboard/', manager_dashboard),
    path('manager/services/', manager_services),
    path('manager/services/<int:item_id>/', manager_service_detail),
    path('manager/add-ons/', manager_add_ons),
    path('manager/add-ons/<int:item_id>/', manager_add_on_detail),
    path('manager/categories/', manager_categories),
    path('manager/categories/<int:item_id>/', manager_category_detail),
    path('manager/bookings/', manager_bookings),
    path('manager/invoices/', manager_invoices),
    path('manager/workers/', manager_workers),
    path('manager/workers/<int:item_id>/', manager_worker_detail),
    path('manager/ledger/', manager_ledger),
    path('manager/expenses/', manager_expenses),
    path('manager/expenses/<int:item_id>/', manager_expense_detail),

]
