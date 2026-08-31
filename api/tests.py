from datetime import date

from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import Booking, Service, Invoice


class AuthAndBookingTests(APITestCase):
    def setUp(self):
        self.service = Service.objects.create(
            name='غسيل كامل',
            price=35,
        )
        self.user = User.objects.create_user(
            username='0550000000',
            password='password123',
            first_name='محمد',
        )
        self.other_user = User.objects.create_user(
            username='0550000001',
            password='password123',
        )

    def authenticate(self, user=None):
        token, _ = Token.objects.get_or_create(user=user or self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

    def booking_payload(self, time_slot='9 صباحاً'):
        return {
            'service': self.service.id,
            'customer_name': 'محمد',
            'customer_phone': '0550000000',
            'car_size': 'small',
            'address_text': 'الرياض',
            'date': date.today().isoformat(),
            'time_slot': time_slot,
            'payment_method': 'cash',
        }

    def test_register_returns_token(self):
        response = self.client.post('/api/auth/register/', {
            'username': '0550000002',
            'name': 'عميل جديد',
            'email': '',
            'password': 'password123',
        })
        self.assertEqual(response.status_code, 201)
        self.assertIn('token', response.data)

    def test_booking_requires_authentication(self):
        response = self.client.post('/api/bookings/', self.booking_payload())
        self.assertEqual(response.status_code, 401)

    def test_booking_uses_authenticated_customer_and_server_price(self):
        self.authenticate()
        payload = self.booking_payload()
        payload['total_price'] = 1
        response = self.client.post('/api/bookings/', payload)
        self.assertEqual(response.status_code, 201)
        booking = Booking.objects.get()
        self.assertEqual(booking.customer, self.user)
        self.assertEqual(booking.total_price, self.service.price)
        self.assertEqual(booking.status, 'accepted')
        self.assertTrue(Invoice.objects.filter(booking=booking).exists())

    def test_customer_only_sees_own_bookings(self):
        Booking.objects.create(
            customer=self.other_user,
            service=self.service,
            date=date.today(),
            time_slot='10 صباحاً',
            total_price=35,
        )
        self.authenticate()
        response = self.client.get('/api/bookings/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_worker_endpoint_requires_staff(self):
        self.authenticate()
        response = self.client.get('/api/worker/bookings/')
        self.assertEqual(response.status_code, 403)

    def test_completed_booking_is_hidden_from_worker_list(self):
        worker = User.objects.create_user(
            username='0550000099', password='password123', is_staff=True
        )
        Booking.objects.create(
            customer=self.user,
            service=self.service,
            date=date.today(),
            time_slot='11 صباحاً',
            total_price=35,
            status='completed',
        )
        self.authenticate(worker)
        response = self.client.get('/api/worker/bookings/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_worker_cannot_access_manager_dashboard(self):
        worker = User.objects.create_user(
            username='0550000088', password='password123', is_staff=True
        )
        self.authenticate(worker)
        response = self.client.get('/api/manager/dashboard/')
        self.assertEqual(response.status_code, 403)

    def test_manager_can_manage_services(self):
        manager = User.objects.create_superuser(
            username='0550000077', password='password123'
        )
        self.authenticate(manager)
        response = self.client.post('/api/manager/services/', {
            'name': 'غسيل تجريبي', 'description': '', 'price': '60.00', 'is_active': True,
        })
        self.assertEqual(response.status_code, 201)
