from datetime import timedelta
from django.test import override_settings
from django.utils import timezone
from . import test_shared_availability as fixtures
from rest_framework.test import APITestCase
from .models import PaymentTransaction


@override_settings(SECURE_SSL_REDIRECT=False, MOYASAR_PUBLISHABLE_KEY='pk_test_hold', MOYASAR_SECRET_KEY='sk_test_hold')
class PaymentHoldTests(APITestCase):
    setUp = fixtures.SharedAvailabilityTests.setUp
    reserve = fixtures.SharedAvailabilityTests.reserve
    availability = fixtures.SharedAvailabilityTests.availability
    car_payload = fixtures.SharedAvailabilityTests.car_payload
    def test_five_minute_hold_exposes_expiry_and_releases_both_services(self):
        self.client.force_authenticate(self.user)
        payload=self.car_payload();payload['payment_method']='online'
        before=timezone.now()
        response=self.client.post('/api/bookings/',payload,format='json')
        self.assertEqual(response.status_code,201,response.data)
        payment=PaymentTransaction.objects.get(booking_id=response.data['id'])
        self.assertGreaterEqual(payment.expires_at,before+timedelta(minutes=5))
        self.assertLess(payment.expires_at,timezone.now()+timedelta(minutes=5,seconds=1))
        self.assertIsNotNone(response.data['payment_expires_at'])
        self.assertIn('4 مساءً',self.availability(self.furniture))
        PaymentTransaction.objects.filter(pk=payment.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertNotIn('4 مساءً',self.availability(self.furniture))
        payment.refresh_from_db();payment.booking.refresh_from_db()
        self.assertEqual(payment.status,'expired');self.assertEqual(payment.booking.status,'canceled')
        response=self.client.get('/api/bookings/')
        self.assertIsNone(response.data[0]['payment_expires_at'])

    def test_unpaid_nearby_booking_can_be_canceled_without_refund(self):
        booking=self.reserve(status='pending',payment_method='online')
        PaymentTransaction.objects.create(booking=booking,amount=34,expires_at=timezone.now()+timedelta(minutes=5))
        # An unpaid hold can be released even if its legacy time cannot be resolved.
        booking.time_slot='unknown legacy time';booking.save()
        self.client.force_authenticate(self.user)
        response=self.client.post(f'/api/bookings/{booking.pk}/cancel/')
        self.assertEqual(response.status_code,200,response.data)
        booking.refresh_from_db();self.assertEqual(booking.status,'canceled')
