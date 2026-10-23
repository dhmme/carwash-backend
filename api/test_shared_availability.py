from datetime import timedelta, time
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from rest_framework import serializers
from rest_framework.test import APITestCase

from .booking_availability import shared_booked_labels
from .models import Booking, BookingTimeSlot, CustomerPackage, Invoice, PackagePlan, PaymentTransaction, Service, ServiceGroup
from .payment_services import verify_moyasar_transaction
from .serializers import BookingSerializer


@override_settings(SECURE_SSL_REDIRECT=False)
class SharedAvailabilityTests(APITestCase):
    def setUp(self):
        self.cars = ServiceGroup.objects.get(key='car_wash')
        self.furniture = ServiceGroup.objects.get(key='furniture_wash')
        self.user = User.objects.create_user('0551231234')
        self.other = User.objects.create_user('0559999999')
        self.day = timezone.localdate() + timedelta(days=1)
        self.car_service = Service.objects.create(group=self.cars, name='غسيل كامل مشترك', price=34)
        self.sofa = Service.objects.get(group=self.furniture, name='كنب')
        self.car_slot, _ = BookingTimeSlot.objects.get_or_create(
            group=self.cars, label='4 مساءً', defaults={'start_time': time(16)},
        )
        self.furniture_slot = BookingTimeSlot.objects.get(group=self.furniture, label='4 مساءً')

    def reserve(self, group=None, **overrides):
        group = group or self.cars
        return Booking.objects.create(**{
            'customer': self.user, 'service_group': group,
            'service': self.car_service if group == self.cars else self.sofa,
            'date': self.day, 'time_slot': '4 مساءً', 'status': 'accepted',
            'total_price': 34, 'payment_method': 'cash', **overrides,
        })

    def availability(self, group):
        response = self.client.get('/api/booked-slots/', {'date': self.day.isoformat(), 'group': group.key})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['availability_scope'], 'shared_team')
        return response.data['booked']

    def car_payload(self):
        return {'service_group': self.cars.pk, 'service': self.car_service.pk,
                'date': self.day.isoformat(), 'time_slot': '4 مساءً',
                'payment_method': 'cash', 'address_text': 'عنيزة'}

    def furniture_payload(self):
        return {'service_group': self.furniture.pk,
                'service_items': [{'id': self.sofa.pk, 'quantity': 2}],
                'date': self.day.isoformat(), 'time_slot': '4 مساءً',
                'payment_method': 'cash', 'address_text': 'عنيزة'}

    def test_existing_four_pm_booking_is_preserved_and_blocks_both_groups(self):
        booking = self.reserve(date=timezone.localdate())
        self.day = booking.date
        invoice = Invoice.objects.create(booking=booking)
        before = list(Booking.objects.values())
        invoice_before = list(Invoice.objects.values())
        self.assertIn('4 مساءً', self.availability(self.cars))
        self.assertIn('4 مساءً', self.availability(self.furniture))
        self.assertEqual(list(Booking.objects.values()), before)
        self.assertEqual(list(Invoice.objects.values()), invoice_before)
        self.assertTrue(Invoice.objects.filter(pk=invoice.pk).exists())

    def test_car_reservation_prevents_new_furniture_booking(self):
        original = self.reserve()
        self.client.force_authenticate(self.other)
        response = self.client.post('/api/bookings/', self.furniture_payload(), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('time_slot', response.data)
        original.refresh_from_db()
        self.assertEqual(original.status, 'accepted')
        self.assertEqual(Booking.objects.count(), 1)

    def test_furniture_reservation_prevents_new_car_booking(self):
        self.reserve(self.furniture)
        self.assertIn('4 مساءً', self.availability(self.cars))
        self.client.force_authenticate(self.other)
        response = self.client.post('/api/bookings/', self.car_payload(), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('time_slot', response.data)

    def test_clock_time_is_matched_even_if_labels_differ(self):
        self.reserve()
        self.furniture_slot.label = 'الساعة 16:00'
        self.furniture_slot.save()
        self.assertIn('الساعة 16:00', self.availability(self.furniture))

    def test_equal_labels_at_different_actual_times_do_not_conflict(self):
        self.reserve()
        BookingTimeSlot.objects.filter(group=self.furniture, start_time=time(17)).delete()
        self.furniture_slot.start_time = time(17)
        self.furniture_slot.save()
        self.assertNotIn('4 مساءً', self.availability(self.furniture))

    def test_disabled_schedule_still_blocks_existing_booking(self):
        self.reserve()
        self.car_slot.is_active = False
        self.car_slot.save()
        self.cars.is_active = False
        self.cars.save()
        self.assertIn('4 مساءً', self.availability(self.furniture))

    def test_legacy_label_without_schedule_is_not_ignored(self):
        self.reserve()
        self.car_slot.delete()
        self.assertIn('4 مساءً', self.availability(self.furniture))

    def test_pending_and_confirmed_states_reserve_team_until_cancellation(self):
        booking = self.reserve()
        for status in ['pending', 'accepted', 'on_the_way', 'in_progress', 'completed']:
            booking.status = status
            booking.save()
            self.assertIn('4 مساءً', self.availability(self.furniture))
        booking.status = 'canceled'
        booking.save()
        self.assertNotIn('4 مساءً', self.availability(self.furniture))
        self.assertNotIn('4 مساءً', self.availability(self.cars))

    def test_old_overlapping_bookings_remain_untouched_and_each_holds_slot(self):
        first = self.reserve()
        second = self.reserve(self.furniture)
        before = list(Booking.objects.values())
        self.assertIn('4 مساءً', self.availability(self.cars))
        self.assertEqual(list(Booking.objects.values()), before)
        first.status = 'canceled'
        first.save()
        self.assertIn('4 مساءً', self.availability(self.cars))
        second.status = 'canceled'
        second.save()
        self.assertNotIn('4 مساءً', self.availability(self.cars))

    def test_next_day_and_midnight_offsets_use_actual_date(self):
        car = BookingTimeSlot.objects.create(group=self.cars, label='نهاية اليوم 1 صباحاً', start_time=time(1), day_offset=1)
        furniture = BookingTimeSlot.objects.create(group=self.furniture, label='بداية اليوم 1 صباحاً', start_time=time(1), day_offset=0)
        self.reserve(time_slot=car.label)
        self.assertNotIn(furniture.label, self.availability(self.furniture))
        self.day += timedelta(days=1)
        self.assertIn(furniture.label, self.availability(self.furniture))
        self.assertNotIn('4 مساءً', self.availability(self.furniture))

    def test_interleaved_checkouts_are_rechecked_without_spending_package_balance(self):
        plan = PackagePlan.objects.create(name='اختبار', washes_count=4, price=77, included_service=self.car_service)
        package = CustomerPackage.objects.create(customer=self.user, plan=plan, status='active',
            remaining_washes=4, expires_at=timezone.now() + timedelta(days=60))
        car = BookingSerializer(data={**self.car_payload(), 'customer_package': package.pk},
                                context={'request': SimpleNamespace(user=self.user)})
        furniture = BookingSerializer(data=self.furniture_payload(), context={'request': SimpleNamespace(user=self.other)})
        self.assertTrue(car.is_valid(), car.errors)
        self.assertTrue(furniture.is_valid(), furniture.errors)
        # Both customers saw a free slot before the first customer confirmed it.
        furniture.save()
        with self.assertRaises(serializers.ValidationError):
            car.save()
        package.refresh_from_db()
        self.assertEqual(package.remaining_washes, 4)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_cancel_reopens_other_service_without_changing_date_or_invoice(self):
        original = self.reserve()
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(f'/api/bookings/{original.pk}/cancel/').status_code, 200)
        self.assertNotIn('4 مساءً', self.availability(self.furniture))
        response = self.client.post('/api/bookings/', self.furniture_payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        original.refresh_from_db()
        self.assertEqual(original.date, self.day)
        self.assertEqual(original.time_slot, '4 مساءً')
        self.assertEqual(original.status, 'canceled')

    def test_late_online_payment_cannot_reopen_canceled_time_taken_by_other_group(self):
        old = self.reserve(status='canceled')
        other = self.reserve(self.furniture)
        self.furniture_slot.label = 'الساعة الرابعة'
        self.furniture_slot.save()
        other.time_slot = self.furniture_slot.label
        other.save()
        payment = PaymentTransaction.objects.create(booking=old, amount=34,
            expires_at=timezone.now() + timedelta(minutes=15))
        payment_id = str(uuid.uuid4())
        provider = {'id': payment_id, 'amount': 3400, 'currency': 'SAR', 'status': 'paid',
                    'metadata': {'booking_id': str(old.pk)}}
        with patch('api.payment_services.fetch_moyasar_payment', return_value=provider):
            verify_moyasar_transaction(payment.public_token, payment_id)
        old.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(old.status, 'canceled')
        self.assertEqual(other.status, 'accepted')
        self.assertEqual(Booking.objects.count(), 2)

    def test_schedule_group_cannot_be_changed_under_existing_booking(self):
        self.reserve()
        manager = User.objects.create_superuser('manager', password='Strong!password937')
        self.client.force_authenticate(manager)
        response = self.client.patch(f'/api/manager/time-slots/{self.car_slot.pk}/',
                                     {'group': self.furniture.pk, 'label': 'نقل موعد'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.car_slot.refresh_from_db()
        self.assertEqual(self.car_slot.group_id, self.cars.pk)
