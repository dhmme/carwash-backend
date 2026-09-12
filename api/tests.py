from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlparse
import uuid

from django.test import override_settings
from django.utils import timezone
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import (
    AddOn, Booking, Car, Expense, Invoice, PaymentTransaction, Service,
)


@override_settings(SECURE_SSL_REDIRECT=False)
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

    def create_car(self, user=None, plate_number='أ ب ج 1234'):
        return Car.objects.create(
            user=user or self.user,
            category='sedan',
            vehicle_name='تويوتا كامري',
            brand='تويوتا كامري',
            model='',
            color='أبيض',
            plate_number=plate_number,
        )

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
        invoice = Invoice.objects.get(booking=booking)
        self.assertEqual(invoice.total_amount, self.service.price)
        self.assertEqual(invoice.line_items[0]['name'], self.service.name)
        self.assertEqual(
            Decimal(invoice.line_items[0]['subtotal']),
            invoice.total_amount,
        )

        invoice_url = response.data['invoice_url']
        self.assertIn('/api/invoices/', invoice_url)
        self.client.credentials()
        invoice_response = self.client.get(urlparse(invoice_url).path)
        self.assertEqual(invoice_response.status_code, 200)
        self.assertContains(invoice_response, self.service.name)

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

    def test_customer_cannot_book_another_customers_car(self):
        other_car = self.create_car(self.other_user, 'د هـ و 5678')
        self.authenticate()
        payload = self.booking_payload()
        payload['car'] = other_car.id
        response = self.client.post('/api/bookings/', payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('car', response.data)

    def test_booking_rejects_inactive_service(self):
        self.service.is_active = False
        self.service.save(update_fields=['is_active'])
        self.authenticate()
        response = self.client.post('/api/bookings/', self.booking_payload())
        self.assertEqual(response.status_code, 400)
        self.assertIn('service', response.data)

    def test_booking_rejects_past_or_out_of_window_dates(self):
        self.authenticate()
        past = self.booking_payload()
        past['date'] = (timezone.localdate() - timedelta(days=1)).isoformat()
        response = self.client.post('/api/bookings/', past)
        self.assertEqual(response.status_code, 400)
        self.assertIn('date', response.data)

        future = self.booking_payload()
        future['date'] = (timezone.localdate() + timedelta(days=4)).isoformat()
        response = self.client.post('/api/bookings/', future)
        self.assertEqual(response.status_code, 400)
        self.assertIn('date', response.data)

    def test_booking_rejects_unknown_time_slot(self):
        self.authenticate()
        response = self.client.post(
            '/api/bookings/', self.booking_payload('3 فجراً')
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('time_slot', response.data)

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

    def test_worker_can_only_complete_active_booking(self):
        worker = User.objects.create_user(
            username='0550000098', password='password123', is_staff=True
        )
        booking = Booking.objects.create(
            customer=self.user,
            service=self.service,
            date=date.today(),
            time_slot='10 صباحاً',
            total_price=35,
            status='accepted',
        )
        self.authenticate(worker)

        response = self.client.patch(
            f'/api/worker/bookings/{booking.id}/status/',
            {'status': 'in_progress'},
        )
        self.assertEqual(response.status_code, 400)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'accepted')

        response = self.client.patch(
            f'/api/worker/bookings/{booking.id}/status/',
            {'status': 'completed'},
        )
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'completed')

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

    def test_manager_ledger_calculates_cash_and_expenses(self):
        manager = User.objects.create_superuser(
            username='0550000066', password='password123'
        )
        Booking.objects.create(
            customer=self.user, service=self.service, date=date.today(),
            time_slot='12 مساءً', total_price=100, status='completed', payment_method='cash',
        )
        Expense.objects.create(
            date=date.today(), description='وقود', amount=25, payment_method='cash', created_by=manager,
        )
        self.authenticate(manager)
        response = self.client.get('/api/manager/ledger/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_receipts'], 100)
        self.assertEqual(response.data['total_expenses'], 25)
        self.assertEqual(response.data['cash_available'], 75)

    @override_settings(
        MOYASAR_PUBLISHABLE_KEY='pk_test_example',
        MOYASAR_SECRET_KEY='sk_test_example',
    )
    def test_online_booking_waits_for_verified_payment(self):
        self.authenticate()
        payload = self.booking_payload()
        payload['payment_method'] = 'online'
        response = self.client.post('/api/bookings/', payload)
        self.assertEqual(response.status_code, 201)

        booking = Booking.objects.get()
        payment = PaymentTransaction.objects.get(booking=booking)
        self.assertEqual(booking.status, 'pending')
        self.assertEqual(payment.status, 'pending')
        self.assertFalse(Invoice.objects.filter(booking=booking).exists())
        self.assertIn('/checkout/', response.data['payment_checkout_url'])
        self.assertEqual(response.data['invoice_url'], '')

    @override_settings(
        MOYASAR_PUBLISHABLE_KEY='pk_test_example',
        MOYASAR_SECRET_KEY='sk_test_example',
    )
    def test_verified_online_payment_confirms_booking_and_invoice(self):
        self.authenticate()
        payload = self.booking_payload()
        payload['payment_method'] = 'online'
        response = self.client.post('/api/bookings/', payload)
        booking = Booking.objects.get()
        payment = booking.payment
        provider_id = str(uuid.uuid4())
        provider_response = {
            'id': provider_id,
            'status': 'paid',
            'amount': int(payment.amount * Decimal('100')),
            'currency': 'SAR',
            'metadata': {'booking_id': str(booking.id)},
            'source': {'type': 'creditcard', 'company': 'visa'},
        }

        with patch(
            'api.payment_services.fetch_moyasar_payment',
            return_value=provider_response,
        ):
            callback = self.client.get(
                f'/api/payments/{payment.public_token}/callback/?id={provider_id}'
            )

        self.assertEqual(callback.status_code, 200)
        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, 'accepted')
        self.assertEqual(payment.status, 'paid')
        self.assertTrue(Invoice.objects.filter(booking=booking).exists())

    @override_settings(
        MOYASAR_PUBLISHABLE_KEY='pk_test_example',
        MOYASAR_SECRET_KEY='sk_test_example',
    )
    def test_payment_reference_is_saved_only_after_server_verification(self):
        self.authenticate()
        payload = self.booking_payload()
        payload['payment_method'] = 'online'
        self.client.post('/api/bookings/', payload)
        payment = PaymentTransaction.objects.get()
        provider_id = str(uuid.uuid4())
        provider_response = {
            'id': provider_id,
            'status': 'initiated',
            'amount': int(payment.amount * Decimal('100')) + 1,
            'currency': 'SAR',
            'metadata': {'booking_id': str(payment.booking_id)},
            'source': {'type': 'creditcard', 'company': 'mada'},
        }
        reference_url = (
            f'/api/payments/{payment.public_token}/reference/'
        )

        with patch(
            'api.payment_services.fetch_moyasar_payment',
            return_value=provider_response,
        ):
            rejected = self.client.post(
                reference_url, {'id': provider_id}, format='json'
            )
        self.assertEqual(rejected.status_code, 400)
        payment.refresh_from_db()
        self.assertIsNone(payment.provider_payment_id)

        provider_response['amount'] -= 1
        with patch(
            'api.payment_services.fetch_moyasar_payment',
            return_value=provider_response,
        ):
            accepted = self.client.post(
                reference_url, {'id': provider_id}, format='json'
            )
        self.assertEqual(accepted.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.provider_payment_id, provider_id)

    @override_settings(
        MOYASAR_PUBLISHABLE_KEY='pk_test_example',
        MOYASAR_SECRET_KEY='sk_live_example',
    )
    def test_mismatched_moyasar_keys_keep_online_payment_disabled(self):
        config = self.client.get('/api/payment-config/')
        self.assertEqual(config.status_code, 200)
        self.assertFalse(config.data['online_enabled'])
        self.assertEqual(config.data['mode'], 'disabled')

        self.authenticate()
        payload = self.booking_payload()
        payload['payment_method'] = 'online'
        response = self.client.post('/api/bookings/', payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('payment_method', response.data)

    @override_settings(
        MOYASAR_PUBLISHABLE_KEY='pk_test_example',
        MOYASAR_SECRET_KEY='sk_test_example',
    )
    def test_expired_online_payment_releases_time_slot(self):
        self.authenticate()
        payload = self.booking_payload()
        payload['payment_method'] = 'online'
        self.client.post('/api/bookings/', payload)
        payment = PaymentTransaction.objects.get()
        payment.expires_at = timezone.now() - timedelta(minutes=1)
        payment.save(update_fields=['expires_at'])

        slots = self.client.get(
            f"/api/booked-slots/?date={payload['date']}"
        )
        self.assertEqual(slots.status_code, 200)
        self.assertNotIn(payload['time_slot'], slots.data['booked'])
        payment.refresh_from_db()
        payment.booking.refresh_from_db()
        self.assertEqual(payment.status, 'expired')
        self.assertEqual(payment.booking.status, 'canceled')
