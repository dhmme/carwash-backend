import uuid
from datetime import timedelta, time

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import Booking, BookingTimeSlot, Car, CustomerPackage, PackagePlan, Service, ServiceGroup


@override_settings(SECURE_SSL_REDIRECT=False)
class ManagerPackageTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser('manager', password='test-password')
        self.customer = User.objects.create_user('0551231234', first_name='عميل')
        self.worker = User.objects.create_user('worker', is_staff=True)
        self.service = Service.objects.create(
            group=ServiceGroup.objects.get(key='car_wash'), name='غسيل اختبار الباقات', price=34,
        )
        self.plan = PackagePlan.objects.create(
            name='٤ غسلات', washes_count=4, price=77,
            included_service=self.service, validity_days=60,
        )
        self.url = '/api/manager/package-purchases/'

    def payload(self, **overrides):
        return {'customer': self.customer.pk, 'plan': self.plan.pk,
                'reference': str(uuid.uuid4()), 'reason': 'تحويل مؤكد خارج الموقع', **overrides}

    def grant(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return CustomerPackage.objects.get(pk=response.data['id'])

    def test_only_managers_can_grant_or_revoke(self):
        item = self.grant()
        for user in [None, self.customer, self.worker]:
            self.client.force_authenticate(user)
            grant = self.client.post(self.url, self.payload(), format='json')
            revoke = self.client.post(f'{self.url}{item.pk}/revoke/', {'reason': 'خطأ'}, format='json')
            self.assertIn(grant.status_code, [401, 403])
            self.assertIn(revoke.status_code, [401, 403])
        item.refresh_from_db()
        self.assertEqual(item.status, 'active')

    def test_grant_is_active_with_balance_expiry_and_audit(self):
        before = timezone.now()
        item = self.grant()
        self.assertEqual(item.customer_id, self.customer.pk)
        self.assertEqual(item.remaining_washes, 4)
        self.assertTrue(item.usable)
        self.assertEqual(item.payment_method, 'manager_grant')
        self.assertGreaterEqual(item.activated_at, before)
        self.assertEqual(item.expires_at - item.activated_at, timedelta(days=60))
        log = LogEntry.objects.get(object_id=str(item.pk))
        self.assertEqual(log.user_id, self.manager.pk)
        self.assertIn('تحويل مؤكد', log.change_message)
        self.client.force_authenticate(self.customer)
        owned = self.client.get('/api/customer-packages/').data
        self.assertIn(item.pk, [row['id'] for row in owned])

    def test_retry_does_not_grant_twice_or_reactivate_revoked_package(self):
        self.client.force_authenticate(self.manager)
        data = self.payload()
        first = self.client.post(self.url, data, format='json')
        second = self.client.post(self.url, data, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data['id'], second.data['id'])
        item = CustomerPackage.objects.get(pk=first.data['id'])
        self.client.post(f'{self.url}{item.pk}/revoke/', {'reason': 'اعتماد خاطئ'}, format='json')
        third = self.client.post(self.url, data, format='json')
        self.assertEqual(third.data['status'], 'revoked')
        self.assertEqual(CustomerPackage.objects.count(), 1)
        other = User.objects.create_user('0555555555')
        data['customer'] = other.pk
        self.assertEqual(self.client.post(self.url, data, format='json').status_code, 409)

    def test_invalid_grants_and_inactive_plan_are_rejected(self):
        self.client.force_authenticate(self.manager)
        for changes in [{'customer': self.worker.pk}, {'customer': 999999},
                        {'plan': 999999}, {'reason': ' '}, {'reference': 'invalid'}]:
            self.assertEqual(self.client.post(self.url, self.payload(**changes), format='json').status_code, 400)
        self.plan.is_active = False
        self.plan.save()
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 400)
        self.assertFalse(CustomerPackage.objects.exists())

    def test_revoke_preserves_history_and_blocks_use(self):
        item = self.grant()
        item.remaining_washes = 3
        item.save()
        booking = Booking.objects.create(
            customer=self.customer, service=self.service, customer_package=item,
            package_wash_used=True, date=timezone.localdate() + timedelta(days=1),
            time_slot='9 صباحاً', total_price=0, status='accepted',
        )
        endpoint = f'{self.url}{item.pk}/revoke/'
        self.assertEqual(self.client.post(endpoint, {'reason': ''}, format='json').status_code, 400)
        response = self.client.post(endpoint, {'reason': 'اعتماد دون دفع'}, format='json')
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(item.status, 'revoked')
        self.assertEqual(item.remaining_washes, 3)
        self.assertFalse(item.usable)
        self.assertEqual(booking.status, 'accepted')
        self.assertEqual(booking.customer_package_id, item.pk)
        self.assertEqual(self.client.patch(f'{self.url}{item.pk}/', {'status': 'active'}, format='json').status_code, 400)
        self.client.post(endpoint, {'reason': 'إعادة المحاولة'}, format='json')
        self.assertEqual(LogEntry.objects.filter(object_id=str(item.pk)).count(), 2)
        # Returning a canceled wash must not restore usability to a revoked package.
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.post(f'/api/bookings/{booking.pk}/cancel/').status_code, 200)
        item.refresh_from_db()
        self.assertFalse(item.usable)
        car = Car.objects.create(user=self.customer, brand='تويوتا', plate_number='أ ب 123')
        BookingTimeSlot.objects.get_or_create(group=self.service.group, label='9 صباحاً', defaults={'start_time': time(9)})
        attempt = self.client.post('/api/bookings/', {
            'car': car.pk, 'service': self.service.pk, 'customer_package': item.pk,
            'date': (timezone.localdate() + timedelta(days=1)).isoformat(),
            'time_slot': '9 صباحاً', 'payment_method': 'cash', 'address_text': 'عنيزة',
        }, format='json')
        self.assertEqual(attempt.status_code, 400)
        self.assertIn('customer_package', attempt.data)

    def test_repeated_approval_never_resets_balance_or_expiry(self):
        item = self.grant()
        item.remaining_washes = 2
        item.save()
        expiry = item.expires_at
        response = self.client.patch(f'{self.url}{item.pk}/', {'status': 'active'}, format='json')
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.remaining_washes, 2)
        self.assertEqual(item.expires_at, expiry)

    def test_customer_filter_does_not_mix_customer_packages(self):
        item = self.grant()
        other = User.objects.create_user('0559999999')
        CustomerPackage.objects.create(customer=other, plan=self.plan)
        response = self.client.get(f'{self.url}?customer={self.customer.pk}')
        self.assertEqual([row['id'] for row in response.data], [item.pk])
