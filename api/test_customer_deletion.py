from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

from .models import Booking, Car, CustomerPackage, Invoice, Location, PackagePlan, Service, ServiceGroup


@override_settings(SECURE_SSL_REDIRECT=False)
class CustomerDeletionTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.manager = User.objects.create_superuser('manager', password='Strong!password937')
        self.customer = User.objects.create_user('0551231234', email='client@example.com',
                                                first_name='العميل', password='Strong!password937')
        self.worker = User.objects.create_user('worker', is_staff=True)
        self.customer_id = self.customer.pk
        self.phone = self.customer.username
        self.url = f'/api/manager/customers/{self.customer.pk}/delete/'
        self.body = {'confirm_phone': self.phone, 'reason': 'بيانات تسجيل وهمية'}
        self.service = Service.objects.create(group=ServiceGroup.objects.get(key='car_wash'),
                                              name='غسيل حذف الحساب', price=34)
        self.plan = PackagePlan.objects.create(name='٤ غسلات', washes_count=4, price=77,
                                               included_service=self.service)

    def delete(self):
        self.client.force_authenticate(self.manager)
        return self.client.post(self.url, self.body, format='json')

    def booking(self, status):
        return Booking.objects.create(customer=self.customer, service=self.service,
            customer_name='العميل', customer_phone=self.phone,
            date=timezone.localdate(), time_slot='9 صباحاً', total_price=34, status=status)

    def test_manager_only_and_staff_accounts_are_protected(self):
        for user in [None, self.customer, self.worker]:
            self.client.force_authenticate(user)
            self.assertIn(self.client.get(self.url).status_code, [401, 403])
            self.assertIn(self.client.post(self.url, self.body, format='json').status_code, [401, 403])
        self.client.force_authenticate(self.manager)
        for user in [self.manager, self.worker]:
            endpoint = f'/api/manager/customers/{user.pk}/delete/'
            self.assertEqual(self.client.post(endpoint, {'confirm_phone': user.username, 'reason': 'خطأ'}, format='json').status_code, 404)
        self.assertTrue(User.objects.filter(pk=self.customer.pk).exists())

    def test_confirmation_and_reason_required_without_mutation(self):
        self.client.force_authenticate(self.manager)
        for body in [{}, {'confirm_phone': 'wrong', 'reason': 'خطأ'},
                     {'confirm_phone': self.phone, 'reason': ''}]:
            self.assertEqual(self.client.post(self.url, body, format='json').status_code, 400)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_active)
        self.assertTrue(self.customer.has_usable_password())

    def test_unused_account_and_saved_data_are_deleted(self):
        Car.objects.create(user=self.customer, brand='تويوتا', plate_number='أ ب ١٢٣')
        Location.objects.create(user=self.customer, name='المنزل', latitude=26, longitude=44)
        response = self.delete()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['history_preserved'])
        self.assertFalse(User.objects.filter(pk=self.customer_id).exists())
        self.assertFalse(Car.objects.filter(user_id=self.customer_id).exists())
        self.assertFalse(Location.objects.filter(user_id=self.customer_id).exists())
        self.assertTrue(LogEntry.objects.filter(user=self.manager, object_id=str(self.customer_id)).exists())

    def test_existing_invoice_and_ledger_are_preserved_but_account_is_removed(self):
        booking = self.booking('completed')
        invoice = Invoice.objects.create(booking=booking)
        original_number = invoice.number
        response = self.delete()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['history_preserved'])
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)
        self.assertFalse(self.customer.has_usable_password())
        self.assertNotEqual(self.customer.username, self.phone)
        self.assertEqual(self.customer.email, '')
        self.assertEqual(self.customer.first_name, 'عميل محذوف')
        invoice.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(invoice.number, original_number)
        self.assertEqual(booking.total_price, Decimal('34'))
        customers = self.client.get('/api/manager/customers/').data
        self.assertNotIn(self.customer_id, [c['id'] for c in customers])
        export = self.client.get('/api/manager/customers/export/')
        sheet = load_workbook(BytesIO(export.content)).active
        self.assertNotIn(self.phone, str(list(sheet.values)))
        self.assertEqual(self.client.get('/api/manager/dashboard/').data['customers'], 0)

    def test_active_booking_blocks_deletion(self):
        booking = self.booking('accepted')
        self.assertEqual(self.delete().status_code, 409)
        self.customer.refresh_from_db()
        booking.refresh_from_db()
        self.assertTrue(self.customer.is_active)
        self.assertEqual(booking.status, 'accepted')

    def test_usable_package_blocks_deletion_and_canceled_package_does_not(self):
        package = CustomerPackage.objects.create(customer=self.customer, plan=self.plan,
            status='active', remaining_washes=4, expires_at=timezone.now() + timedelta(days=5))
        self.assertEqual(self.delete().status_code, 409)
        package.status = 'revoked'
        package.save()
        self.assertEqual(self.delete().status_code, 200)
        package.refresh_from_db()
        self.assertEqual(package.remaining_washes, 4)
        self.assertEqual(package.status, 'revoked')

    def test_archived_accounts_cannot_use_access_refresh_or_password(self):
        self.booking('canceled')
        refresh = RefreshToken.for_user(self.customer)
        access = str(refresh.access_token)
        self.assertEqual(self.delete().status_code, 200)
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=str(refresh['jti'])).exists())
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + access)
        self.assertEqual(self.client.get('/api/locations/').status_code, 401)
        self.client.credentials()
        self.assertEqual(self.client.post('/api/auth/refresh/', {'refresh': str(refresh)}, format='json').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login/', {'username': self.phone, 'password': 'Strong!password937'}, format='json').status_code, 400)

    def test_preview_is_read_only_and_deleted_phone_can_register_again(self):
        self.booking('canceled')
        self.client.force_authenticate(self.manager)
        preview = self.client.get(self.url)
        self.assertEqual(preview.data['phone'], self.phone)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_active)
        self.assertEqual(self.delete().status_code, 200)
        self.client.force_authenticate(user=None)
        response = self.client.post('/api/auth/register/', {
            'username': self.phone, 'name': 'صاحب الرقم',
            'email': 'client@example.com', 'password': 'New!password937Safe',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotEqual(User.objects.get(username=self.phone).pk, self.customer_id)

    def test_pending_package_is_revoked_and_hidden_phone_does_not_leak_marker(self):
        package = CustomerPackage.objects.create(customer=self.customer, plan=self.plan)
        self.assertEqual(self.delete().status_code, 200)
        package.refresh_from_db()
        self.assertEqual(package.status, 'revoked')
        purchase = next(p for p in self.client.get('/api/manager/package-purchases/').data if p['id'] == package.pk)
        self.assertEqual(purchase['customer_phone'], '')
        self.assertEqual(purchase['customer_name'], 'عميل محذوف')
