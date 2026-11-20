from datetime import date
from unittest.mock import patch
from django.contrib.auth.models import User, Group
from django.contrib.admin.models import LogEntry
from django.test import override_settings
from rest_framework.test import APITestCase
from .models import WorkerMonthlyBonus
from .staff_accounts import DELETED_WORKERS_GROUP


@override_settings(SECURE_SSL_REDIRECT=False)
class WorkerBonusTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create_user('bonus-manager', is_staff=True, is_superuser=True)
        self.worker = User.objects.create_user('bonus-worker', first_name='Worker', is_staff=True)
        self.other = User.objects.create_user('bonus-other', is_staff=True)
        self.customer = User.objects.create_user('bonus-customer')
        self.client.force_authenticate(self.manager)
        self.month = '2026-11'
        self.url = f'/api/manager/workers/{self.worker.pk}/bonus/'

    def save(self, amount, expected=None, month=None):
        return self.client.put(self.url, {'month': month or self.month, 'amount': amount, 'expected_updated_at': expected}, format='json')

    def test_manual_total_replaces_and_previous_month_is_preserved_with_audit(self):
        old=WorkerMonthlyBonus.objects.create(worker=self.worker,month=date(2026,10,1),amount=40)
        first=self.save('100.50');self.assertEqual(first.status_code,200,first.data)
        second=self.save('125.00',first.data['updated_at']);self.assertEqual(second.status_code,200)
        item=WorkerMonthlyBonus.objects.get(worker=self.worker,month=date(2026,11,1))
        self.assertEqual(str(item.amount),'125.00');self.assertEqual(item.updated_by,self.manager)
        old.refresh_from_db();self.assertEqual(old.amount,40)
        self.assertEqual(LogEntry.objects.filter(user=self.manager).count(),2)

    def test_stale_edit_cannot_overwrite_current_value(self):
        first=self.save('50');self.assertEqual(first.status_code,200)
        stale=self.save('200');self.assertEqual(stale.status_code,409)
        self.assertEqual(WorkerMonthlyBonus.objects.get(worker=self.worker).amount,50)

    def test_worker_sees_only_own_current_riyadh_month(self):
        WorkerMonthlyBonus.objects.create(worker=self.worker,month=date(2026,11,1),amount=75)
        WorkerMonthlyBonus.objects.create(worker=self.other,month=date(2026,11,1),amount=900)
        self.client.force_authenticate(self.worker)
        with patch('api.worker_bonuses.timezone.localdate',return_value=date(2026,11,30)):
            response=self.client.get(f'/api/worker/bonus/?worker={self.other.pk}&month=2026-10')
        self.assertEqual(response.data['amount'],'75.00');self.assertEqual(response.data['month'],'2026-11')
        with patch('api.worker_bonuses.timezone.localdate',return_value=date(2026,12,1)):
            response=self.client.get('/api/worker/bonus/')
        self.assertEqual(response.data['amount'],'0.00');self.assertEqual(response.data['month'],'2026-12')
        self.assertEqual(WorkerMonthlyBonus.objects.count(),2)

    def test_customer_and_worker_cannot_edit_or_read_other_worker_totals(self):
        for actor in [self.worker,self.customer]:
            self.client.force_authenticate(actor)
            self.assertEqual(self.save('5').status_code,403)
            self.assertEqual(self.client.get('/api/manager/worker-bonuses/').status_code,403)
        self.assertEqual(self.client.get('/api/worker/bonus/').status_code,403)
        self.client.force_authenticate(self.worker)
        self.assertEqual(self.client.put('/api/worker/bonus/',{'amount':'100'}).status_code,405)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get('/api/worker/bonus/').status_code,[401,403])

    def test_validation_rejects_negative_nonfinite_and_invalid_month(self):
        for amount in ['-1','NaN','Infinity','100000000','1.001']:
            self.assertEqual(self.save(amount).status_code,400)
        self.assertEqual(self.save('10',month='2026-11-02').status_code,400)
        self.assertEqual(self.client.get('/api/manager/worker-bonuses/?month=wrong').status_code,400)
        self.assertFalse(WorkerMonthlyBonus.objects.exists())

    def test_deleted_worker_hidden_but_bonus_history_preserved(self):
        self.save('50')
        self.worker.groups.add(Group.objects.get_or_create(name=DELETED_WORKERS_GROUP)[0])
        response=self.client.get('/api/manager/worker-bonuses/?month=2026-11')
        self.assertNotIn(self.worker.pk,[w['id'] for w in response.data['workers']])
        self.assertEqual(self.save('60').status_code,404)
        self.assertTrue(WorkerMonthlyBonus.objects.filter(worker=self.worker).exists())

    def test_missing_month_defaults_and_zero_has_clear_record(self):
        with patch('api.worker_bonuses.timezone.localdate',return_value=date(2026,11,20)):
            response=self.client.get('/api/manager/worker-bonuses/')
        self.assertEqual(response.data['month'],'2026-11')
        self.assertTrue(all(w['amount']=='0.00' for w in response.data['workers']))
        first=self.save('5')
        self.assertEqual(self.save('0',first.data['updated_at']).status_code,200)
        self.assertEqual(WorkerMonthlyBonus.objects.get().amount,0)
