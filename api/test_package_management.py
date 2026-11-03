import uuid
from datetime import timedelta, time
from io import BytesIO

from django.contrib.auth.models import User
from django.db.migrations.executor import MigrationExecutor
from django.db import connection
from django.test import override_settings, TransactionTestCase
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework.test import APITestCase

from .models import BookingTimeSlot, Car, CustomerPackage, PackageAdjustment, PackagePlan, Service, ServiceGroup


@override_settings(SECURE_SSL_REDIRECT=False)
class PackageManagementTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create(username='manager', is_staff=True, is_superuser=True)
        self.customer = User.objects.create(username='0551112233', first_name='عميل أول')
        self.other = User.objects.create(username='0551112244', first_name='عميل ثان')
        self.service = Service.objects.create(group=ServiceGroup.objects.get(key='car_wash'), name='غسيل قديم', price=34)
        self.plan = PackagePlan.objects.create(name='باقة أصلية', price=99, washes_count=4,
                                               validity_days=60, included_service=self.service)
        self.other_plan = PackagePlan.objects.create(name='باقة ثانية', price=129, washes_count=6,
                                                     validity_days=90, included_service=self.service)
        self.client.force_authenticate(self.manager)

    def purchase(self, plan=None, customer=None, **kwargs):
        values = dict(customer=customer or self.customer, plan=plan or self.plan, status='active',
                      remaining_washes=4, activated_at=timezone.now(),
                      expires_at=timezone.now() + timedelta(days=60))
        values.update(kwargs)
        return CustomerPackage.objects.create(**values)

    def adjust(self, item, **kwargs):
        data = dict(remaining_washes=item.remaining_washes + 1, expires_at=item.expires_at.isoformat(),
                    expected_remaining_washes=item.remaining_washes,
                    expected_expires_at=item.expires_at.isoformat(), expected_status=item.status,
                    reason='تعويض عن الخدمة', reference=str(uuid.uuid4()))
        data.update(kwargs)
        return self.client.post(f'/api/manager/package-purchases/{item.pk}/adjust/', data, format='json'), data

    def test_catalog_changes_and_new_plans_never_change_existing_terms(self):
        item = self.purchase()
        original = item.plan_snapshot.copy()
        expiry = item.expires_at
        new_service = Service.objects.create(group=self.service.group, name='غسيل جديد', price=50)
        response = self.client.patch(f'/api/manager/packages/{self.plan.pk}/', {
            'name': 'اسم جديد', 'price': '150.00', 'washes_count': 8,
            'validity_days': 30, 'included_service': new_service.pk,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.purchase(self.other_plan)
        item.refresh_from_db()
        self.assertEqual(item.plan_snapshot, original)
        self.assertEqual(item.remaining_washes, 4)
        self.assertEqual(item.expires_at, expiry)
        self.client.force_authenticate(self.customer)
        owned = self.client.get('/api/customer-packages/').data
        row = next(x for x in owned if x['id'] == item.pk)
        self.assertEqual(row['plan_name'], 'باقة أصلية')
        self.assertEqual(row['price'], '99')
        self.assertEqual(row['included_service'], self.service.pk)
        car = Car.objects.create(user=self.customer, brand='Toyota', plate_number='123')
        BookingTimeSlot.objects.get_or_create(group=self.service.group, label='9 صباحاً', defaults={'start_time': time(9)})
        booked = self.client.post('/api/bookings/', {
            'car': car.pk, 'service': self.service.pk, 'customer_package': item.pk,
            'date': (timezone.localdate() + timedelta(days=1)).isoformat(),
            'time_slot': '9 صباحاً', 'payment_method': 'cash', 'address_text': 'عنيزة',
        }, format='json')
        self.assertEqual(booked.status_code, 201, booked.data)

    def test_pending_approval_uses_purchase_terms_after_plan_changes(self):
        item = self.purchase(status='pending', remaining_washes=0, activated_at=None, expires_at=None)
        self.plan.washes_count, self.plan.validity_days = 10, 7
        self.plan.save()
        response = self.client.patch(f'/api/manager/package-purchases/{item.pk}/', {'status': 'active'}, format='json')
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.remaining_washes, 4)
        self.assertEqual(item.expires_at - item.activated_at, timedelta(days=60))

    def test_legacy_writer_terms_freeze_before_catalog_edit_without_balance_changes(self):
        item = self.purchase()
        expiry = item.expires_at
        CustomerPackage.objects.filter(pk=item.pk).update(plan_snapshot={}, included_service_snapshot=None)
        response = self.client.patch(f'/api/manager/packages/{self.plan.pk}/', {
            'name': 'باقة بديلة', 'washes_count': 20, 'price': '199.00', 'validity_days': 7,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.plan_snapshot['name'], 'باقة أصلية')
        self.assertEqual(item.plan_snapshot['washes_count'], 4)
        self.assertEqual(item.remaining_washes, 4)
        self.assertEqual(item.expires_at, expiry)

    def test_archive_preserves_subscriptions_and_can_still_manage_one_customer(self):
        item, other = self.purchase(), self.purchase(customer=self.other)
        original = item.plan_snapshot.copy()
        archived = self.client.delete(f'/api/manager/packages/{self.plan.pk}/')
        self.assertEqual(archived.status_code, 204)
        self.assertTrue(PackagePlan.objects.get(pk=self.plan.pk).is_archived)
        self.assertNotIn(self.plan.pk, [p['id'] for p in self.client.get('/api/packages/').data])
        item.refresh_from_db()
        self.assertTrue(item.usable)
        self.assertEqual(item.plan_snapshot, original)
        response, data = self.adjust(item)
        self.assertEqual(response.status_code, 200, response.data)
        item.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(item.remaining_washes, 5)
        self.assertEqual(other.remaining_washes, 4)
        revoked = self.client.post(f'/api/manager/package-purchases/{item.pk}/revoke/', {'reason': 'طلب العميل'}, format='json')
        self.assertEqual(revoked.status_code, 200)
        self.assertTrue(CustomerPackage.objects.filter(pk=item.pk).exists())
        self.assertTrue(CustomerPackage.objects.get(pk=other.pk).usable)

    def test_archived_plan_cannot_be_bought_or_granted(self):
        self.client.delete(f'/api/manager/packages/{self.plan.pk}/')
        grant = self.client.post('/api/manager/package-purchases/', {
            'customer': self.customer.pk, 'plan': self.plan.pk, 'reason': 'هدية', 'reference': str(uuid.uuid4()),
        }, format='json')
        self.assertEqual(grant.status_code, 400)
        self.client.force_authenticate(self.customer)
        bought = self.client.post('/api/customer-packages/', {'plan': self.plan.pk, 'payment_method': 'cash'}, format='json')
        self.assertEqual(bought.status_code, 404)

    def test_plan_counts_deduplicate_buyers_and_separate_purchases_from_grants(self):
        self.purchase()
        self.purchase()
        self.purchase(customer=self.other, payment_method='manager_grant')
        self.purchase(self.other_plan)
        rows = self.client.get('/api/manager/packages/').data
        row = next(x for x in rows if x['id'] == self.plan.pk)
        self.assertEqual((row['customer_count'], row['buyers_count'], row['purchase_count'], row['grants_count']), (2, 1, 2, 1))
        self.assertEqual(row['active_count'], 3)
        response = self.client.get(f'/api/manager/package-purchases/?plan={self.plan.pk}&page=1')
        self.assertEqual(response.data['count'], 3)
        self.assertTrue(all(x['plan'] == self.plan.pk for x in response.data['results']))

    def test_adjustment_has_reason_before_after_and_idempotent_retry(self):
        item = self.purchase()
        response, data = self.adjust(item)
        self.assertEqual(response.status_code, 200, response.data)
        repeated = self.client.post(f'/api/manager/package-purchases/{item.pk}/adjust/', data, format='json')
        self.assertEqual(repeated.status_code, 200)
        entry = PackageAdjustment.objects.get(package=item)
        self.assertEqual(entry.before['remaining_washes'], 4)
        self.assertEqual(entry.after['remaining_washes'], 5)
        self.assertEqual(entry.actor_id, self.manager.pk)
        self.assertEqual(entry.reason, 'تعويض عن الخدمة')
        history = self.client.get(f'/api/manager/package-purchases/{item.pk}/history/').data
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['after']['remaining_washes'], 5)
        self.assertEqual(self.adjust(self.purchase(customer=self.other), reference=data['reference'])[0].status_code, 409)

    def test_revoked_unapproved_request_is_not_counted_as_a_sale(self):
        item = self.purchase(status='pending', activated_at=None, expires_at=None, remaining_washes=0)
        self.client.post(f'/api/manager/package-purchases/{item.pk}/revoke/', {'reason': 'طلب بالخطأ'}, format='json')
        row = self.client.get(f'/api/manager/packages/{self.plan.pk}/').data
        self.assertEqual(row['purchase_count'], 0)
        self.assertEqual(row['buyers_count'], 0)
        self.assertEqual(row['customer_count'], 0)

    def test_negative_stale_and_reasonless_updates_do_not_overwrite_balance(self):
        item = self.purchase()
        for changes in [{'remaining_washes': -1}, {'reason': ' '}, {'expected_remaining_washes': 3},
                        {'expected_expires_at': (item.expires_at + timedelta(days=1)).isoformat()}]:
            response, _ = self.adjust(item, **changes)
            self.assertIn(response.status_code, [400, 409])
        item.refresh_from_db()
        self.assertEqual(item.remaining_washes, 4)
        self.assertFalse(item.adjustments.exists())

    def test_expired_and_exhausted_filters_and_extension(self):
        expired = self.purchase(expires_at=timezone.now() - timedelta(minutes=1))
        exhausted = self.purchase(remaining_washes=0)
        self.assertEqual(self.client.get(f'/api/manager/package-purchases/?plan={self.plan.pk}&page=1&status=expired').data['count'], 1)
        self.assertEqual(self.client.get(f'/api/manager/package-purchases/?plan={self.plan.pk}&page=1&status=exhausted').data['count'], 1)
        self.assertEqual(self.adjust(expired, expires_at=(timezone.now() + timedelta(days=10)).isoformat())[0].status_code, 200)
        expired.refresh_from_db()
        self.assertTrue(expired.usable)
        self.assertEqual(self.adjust(exhausted)[0].status_code, 200)

    def test_only_managers_can_adjust_history_export_and_archive(self):
        item = self.purchase()
        for user in [None, self.customer, User.objects.create(username='worker', is_staff=True)]:
            self.client.force_authenticate(user)
            self.assertIn(self.adjust(item)[0].status_code, [401, 403])
            for path in [f'package-purchases/{item.pk}/history/', f'packages/{self.plan.pk}/export/']:
                self.assertIn(self.client.get('/api/manager/' + path).status_code, [401, 403])
            self.assertIn(self.client.delete(f'/api/manager/packages/{self.plan.pk}/').status_code, [401, 403])

    def test_export_is_plan_scoped_filtered_and_preserves_phone_text(self):
        self.customer.first_name = '=1+1'
        self.customer.save()
        self.purchase()
        self.purchase(customer=self.other)
        self.purchase(self.other_plan)
        response = self.client.get(f'/api/manager/packages/{self.plan.pk}/export/?search=0551112233')
        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(sheet.max_row, 2)
        self.assertEqual(sheet['B2'].value, '=1+1')
        self.assertEqual(sheet['B2'].data_type, 's')
        self.assertEqual(sheet['C2'].value, self.customer.username)
        self.assertEqual(sheet['C2'].data_type, 's')

    def test_invalid_filters_are_rejected_and_pagination_has_exact_count(self):
        for query in ['from=2026-99-99', 'status=unknown', 'plan=bad', 'page=0', 'page_size=101&page=1']:
            self.assertEqual(self.client.get('/api/manager/package-purchases/?' + query).status_code, 400)
        self.purchase()
        self.purchase()
        page = self.client.get(f'/api/manager/package-purchases/?plan={self.plan.pk}&page=2&page_size=1').data
        self.assertEqual((page['count'], page['total_pages'], len(page['results'])), (2, 2, 1))


class PackageSnapshotMigrationTests(TransactionTestCase):
    def test_existing_balance_and_expiry_survive_backfill(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('api', '0026_package_manager_controls')])
        old = executor.loader.project_state([('api', '0026_package_manager_controls')]).apps
        UserModel = old.get_model('auth', 'User')
        customer = UserModel.objects.create(username='migration-customer')
        ServiceModel = old.get_model('api', 'Service')
        service = ServiceModel.objects.create(name='غسيل قديم', price=34)
        plan = old.get_model('api', 'PackagePlan').objects.create(
            name='باقة قديمة', price=99, washes_count=4, validity_days=60, included_service=service, is_active=False)
        for name, count in [('باقة جديدة أولى', 6), ('باقة جديدة ثانية', 8)]:
            old.get_model('api', 'PackagePlan').objects.create(
                name=name, price=129, washes_count=count, validity_days=90, included_service=service)
        expiry = timezone.now() + timedelta(days=17)
        package = old.get_model('api', 'CustomerPackage').objects.create(
            customer=customer, plan=plan, status='active', remaining_washes=2, expires_at=expiry)
        old.get_model('api', 'CustomerPackage').objects.create(
            customer=customer, plan=plan, status='active', remaining_washes=1,
            expires_at=expiry + timedelta(days=5))
        preserved_fields = ['id', 'customer_id', 'plan_id', 'status', 'remaining_washes',
                            'purchased_at', 'activated_at', 'expires_at', 'payment_method', 'grant_reference']
        original_rows = list(old.get_model('api', 'CustomerPackage').objects.order_by('id').values(*preserved_fields))
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([('api', '0027_package_history_and_archive')])
            migrated = CustomerPackage.objects.get(pk=package.pk)
            self.assertEqual(migrated.remaining_washes, 2)
            self.assertEqual(migrated.expires_at, expiry)
            self.assertEqual(migrated.status, 'active')
            self.assertEqual(migrated.plan_snapshot['name'], 'باقة قديمة')
            self.assertEqual(migrated.included_service_snapshot_id, service.pk)
            self.assertEqual(list(CustomerPackage.objects.order_by('id').values(*preserved_fields)), original_rows)
            self.assertTrue(migrated.usable)
            self.assertFalse(PackagePlan.objects.get(pk=plan.pk).is_active)
            self.assertEqual(PackagePlan.objects.filter(
                name__in=['باقة جديدة أولى', 'باقة جديدة ثانية'], is_active=True,
            ).count(), 2)
            # The older app can still create requests while the new version starts.
            late = old.get_model('api', 'CustomerPackage').objects.create(
                customer=customer, plan=plan, status='pending', remaining_washes=0)
            late_row = CustomerPackage.objects.get(pk=late.pk)
            self.assertEqual(late_row.purchase_terms['name'], 'باقة قديمة')
            self.assertEqual(late_row.remaining_washes, 0)
        finally:
            MigrationExecutor(connection).migrate([('api', '0027_package_history_and_archive')])
