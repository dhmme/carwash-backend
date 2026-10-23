from datetime import date
from io import StringIO
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import AuditLog, Expense


@override_settings(SECURE_SSL_REDIRECT=False)
class WorkerDeletionTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser('manager', password='Strong!password937')
        self.worker = User.objects.create_user('0551231234', first_name='عامل',
                                               password='Strong!password937', is_staff=True)
        self.customer = User.objects.create_user('0550000000')
        self.url = f'/api/manager/workers/{self.worker.pk}/'

    def delete(self):
        self.client.force_authenticate(self.manager)
        return self.client.delete(self.url)

    def test_only_managers_can_delete_workers(self):
        for user in [None, self.worker, self.customer]:
            self.client.force_authenticate(user)
            self.assertIn(self.client.delete(self.url).status_code, [401, 403])
        self.worker.refresh_from_db()
        self.assertTrue(self.worker.is_active)

    def test_managers_and_customers_are_protected(self):
        self.client.force_authenticate(self.manager)
        for user in [self.manager, self.customer]:
            self.assertEqual(self.client.delete(f'/api/manager/workers/{user.pk}/').status_code, 404)
            self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_deleted_worker_disappears_and_history_is_preserved(self):
        expense = Expense.objects.create(date=date.today(), description='وقود', amount=10, created_by=self.worker)
        audit = AuditLog.objects.create(user=self.worker, method='PATCH', path='/test/', status_code=200)
        self.assertEqual(self.delete().status_code, 204)
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.is_active)
        self.assertFalse(self.worker.has_usable_password())
        workers = self.client.get('/api/manager/workers/').data
        self.assertNotIn(self.worker.pk, [w['id'] for w in workers])
        expense.refresh_from_db()
        audit.refresh_from_db()
        self.assertEqual(expense.created_by_id, self.worker.pk)
        self.assertEqual(audit.user_id, self.worker.pk)
        self.assertTrue(LogEntry.objects.filter(user=self.manager, object_id=str(self.worker.pk)).exists())
        self.assertEqual(self.client.patch(self.url, {'is_active': True}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(self.url).status_code, 204)

    def test_redeployment_cannot_recreate_deleted_default_worker(self):
        self.delete()
        with patch.dict('os.environ', {'WORKER_PHONE': self.worker.username,
                                     'WORKER_PASSWORD': 'New!password937', 'WORKER_NAME': 'عامل'}):
            call_command('ensure_staff', stdout=StringIO())
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.is_active)
        self.assertFalse(self.worker.has_usable_password())
        self.assertEqual(User.objects.filter(username=self.worker.username).count(), 1)

    def test_explicit_readd_uses_new_identity_and_old_sessions_stay_invalid(self):
        refresh = RefreshToken.for_user(self.worker)
        access = str(refresh.access_token)
        phone = self.worker.username
        self.delete()
        response = self.client.post('/api/manager/workers/', {
            'username': phone, 'first_name': 'عامل جديد', 'password': 'New!password937',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotEqual(response.data['id'], self.worker.pk)
        self.assertTrue(User.objects.get(pk=response.data['id']).is_active)
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.is_active)
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + access)
        self.assertEqual(self.client.get('/api/worker/bookings/').status_code, 401)
        self.client.credentials()
        self.assertEqual(self.client.post('/api/auth/refresh/', {'refresh': str(refresh)}, format='json').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login/', {'username': phone, 'password': 'Strong!password937'}, format='json').status_code, 400)

    def test_failed_readd_does_not_change_archived_identity(self):
        phone = self.worker.username
        self.delete()
        response = self.client.post('/api/manager/workers/', {
            'username': phone, 'first_name': 'عامل', 'password': 'short',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.username, phone)
