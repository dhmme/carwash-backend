from django.urls import path
from .views import (
    hello_view,
    service_list,
    car_list_create,
    booking_list_create,
    booked_slots, 
    worker_bookings,
)

urlpatterns = [
    path('hello/', hello_view, name='hello'),

    # Services
    path('services/', service_list, name='service-list'),

    # Cars
    path('cars/', car_list_create, name='car-list-create'),

    # Bookings
    path('bookings/', booking_list_create, name='booking-list-create'),

    # Booked time slots for a given date
    path('booked-slots/', booked_slots, name='booked-slots'),  

    # For Worker
    path('worker/bookings/', worker_bookings, name='worker-bookings'),

]
