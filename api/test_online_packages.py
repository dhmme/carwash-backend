from unittest.mock import patch
import uuid

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import CustomerPackage, PackagePlan, Service, ServiceGroup
from .payment_services import PaymentVerificationError, verify_moyasar_transaction


@override_settings(SECURE_SSL_REDIRECT=False, MOYASAR_PUBLISHABLE_KEY='pk_test_example',
                   MOYASAR_SECRET_KEY='sk_test_example')
class OnlinePackageTests(APITestCase):
    def setUp(self):
        group = ServiceGroup.objects.get(key='car_wash')
        service = Service.objects.create(group=group, name='Full wash', price=34)
        self.user = User.objects.create_user(username='payment-test', password='test-password-123')
        self.plan = PackagePlan.objects.create(
            name='Four washes', included_service=service, washes_count=4,
            price=99, validity_days=60,
        )
        self.client.force_authenticate(self.user)

    def purchase(self):
        response = self.client.post('/api/customer-packages/',
                                    {'plan': self.plan.pk, 'payment_method': 'online'})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data['payment_checkout_url'].startswith('https://www.codecare.sa/pay/'))
        return CustomerPackage.objects.get(pk=response.data['id'])

    def provider(self, item, **overrides):
        payment = item.payment
        payload = {'id': str(uuid.uuid4()), 'status': 'paid', 'amount': 9900,
                   'currency': 'SAR', 'metadata': {'customer_package_id': str(item.pk)}}
        payload.update(overrides)
        return payment, payload

    def verify(self, payment, payload):
        with patch('api.payment_services.fetch_moyasar_payment', return_value=payload):
            return verify_moyasar_transaction(payment.public_token, payload['id'])

    def test_payment_activates_snapshot_once_and_preserves_adjustments(self):
        item = self.purchase()
        self.assertEqual(item.remaining_washes, 0)
        self.plan.price = 129
        self.plan.washes_count = 6
        self.plan.save()
        payment, payload = self.provider(item)
        self.verify(payment, payload)
        item.refresh_from_db()
        self.assertEqual(item.remaining_washes, 4)
        self.assertEqual(item.purchase_terms['price'], '99.00')
        expires = item.expires_at
        item.remaining_washes = 2
        item.save()
        self.verify(payment, payload)
        item.refresh_from_db()
        self.assertEqual(item.remaining_washes, 2)
        self.assertEqual(item.expires_at, expires)

    def test_wrong_amount_or_purchase_never_activates(self):
        item = self.purchase()
        payment, payload = self.provider(item, amount=1)
        with self.assertRaises(PaymentVerificationError):
            self.verify(payment, payload)
        payload['amount'] = 9900
        payload['metadata'] = {'customer_package_id': '99999'}
        with self.assertRaises(PaymentVerificationError):
            self.verify(payment, payload)
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')

    def test_paid_callback_cannot_reactivate_revoked_package(self):
        item = self.purchase()
        payment, payload = self.provider(item)
        self.verify(payment, payload)
        item.refresh_from_db()
        item.status = 'revoked'
        item.save()
        self.verify(payment, payload)
        item.refresh_from_db()
        self.assertEqual(item.status, 'revoked')

    def test_live_checkout_has_apple_pay_and_no_test_claim(self):
        item = self.purchase()
        self.client.force_authenticate(None)
        with override_settings(MOYASAR_PUBLISHABLE_KEY='pk_live_example', MOYASAR_SECRET_KEY='sk_live_example'):
            response = self.client.get(f'/api/payments/{item.payment.public_token}/checkout/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('applepay', response.context['payment_config']['methods'])
        self.assertNotIn('دفع تجريبي', response.content.decode())
        self.assertNotIn('sk_live_example', response.content.decode())

    def test_manual_approval_cannot_grant_unpaid_online_purchase(self):
        item = self.purchase()
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        response = self.client.patch(f'/api/manager/package-purchases/{item.pk}/', {'status': 'active'})
        self.assertEqual(response.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.remaining_washes, 0)

    def test_webhook_requires_secret_and_verifies_with_provider(self):
        item = self.purchase()
        payment, payload = self.provider(item)
        payload['metadata']['payment_token'] = str(payment.public_token)
        body = {'type': 'payment_paid', 'secret_token': 'hook-secret', 'data': payload}
        with override_settings(MOYASAR_WEBHOOK_SECRET='hook-secret'):
            with patch('api.payment_services.fetch_moyasar_payment', return_value=payload):
                response = self.client.post('/api/payments/webhook/', body, format='json')
            self.assertEqual(response.status_code, 200)
            body['secret_token'] = 'wrong'
            self.assertEqual(self.client.post('/api/payments/webhook/', body, format='json').status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.remaining_washes, 4)

    def test_failed_payment_never_grants_balance(self):
        item = self.purchase()
        payment, payload = self.provider(item, status='failed')
        self.verify(payment, payload)
        item.refresh_from_db()
        self.assertEqual(item.remaining_washes, 0)
        self.assertEqual(item.status, 'pending')
