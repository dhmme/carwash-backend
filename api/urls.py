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
)

urlpatterns = [
    path('hello/', hello_view, name='hello'),
    path('auth/register/', register_view, name='register'),
    path('auth/login/', login_view, name='login'),
    path('auth/logout/', logout_view, name='logout'),

    # Services
    path('services/', service_list, name='service-list'),

    # Cars
    path('cars/', car_list_create, name='car-list-create'),
    path('locations/', location_list_create, name='location-list-create'),

    # Bookings
    path('bookings/', booking_list_create, name='booking-list-create'),

    # Booked time slots for a given date
    path('booked-slots/', booked_slots, name='booked-slots'),  

    # For Worker
    path('worker/bookings/', worker_bookings, name='worker-bookings'),
    path('worker/bookings/<int:booking_id>/status/', update_booking_status,
         name='worker-booking-status'),

]
