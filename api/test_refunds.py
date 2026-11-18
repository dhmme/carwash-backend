from datetime import timedelta, time
from unittest.mock import patch
import uuid
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Booking, BookingTimeSlot, CustomerPackage, PackagePlan, PaymentTransaction, Service, ServiceGroup, PaymentRefund
from .refunds import cancel_purchase, RefundError, eligible_booking
from .payment_services import verify_moyasar_transaction


@override_settings(SECURE_SSL_REDIRECT=False)
class RefundTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('refund-test')
        self.group = ServiceGroup.objects.get(key='car_wash')
        self.service = Service.objects.create(group=self.group, name='wash', price=34)
        self.slot = BookingTimeSlot.objects.get(group=self.group, start_time=time(16), day_offset=0)
        self.booking = Booking.objects.create(customer=self.user, service_group=self.group, service=self.service,
            date=timezone.localdate()+timedelta(days=2), time_slot=self.slot.label, status='accepted', total_price=34, payment_method='online')
        self.payment = PaymentTransaction.objects.create(booking=self.booking, amount=34, status='paid',
            provider_payment_id=str(uuid.uuid4()), paid_at=timezone.now(), expires_at=timezone.now()+timedelta(minutes=30))

    def payload(self, payment=None, refunded=0):
        p = payment or self.payment
        return {'id': p.provider_payment_id, 'status': 'refunded' if refunded else 'paid', 'amount': int(p.amount*100),
            'refunded': refunded, 'currency': 'SAR', 'metadata': {'booking_id': str(p.booking_id)} if p.booking_id else {'customer_package_id': str(p.customer_package_id)}}

    def test_refund_and_repeated_cancel_and_callback_do_not_repeat(self):
        with patch('api.refunds.fetch_moyasar_payment', return_value=self.payload()), patch('api.refunds.post_refund', return_value=self.payload(refunded=3400)) as post:
            cancel_purchase(actor=self.user, booking_id=self.booking.pk)
            cancel_purchase(actor=self.user, booking_id=self.booking.pk)
            self.assertEqual(post.call_count, 1)
        with patch('api.payment_services.fetch_moyasar_payment', return_value=self.payload()):
            verify_moyasar_transaction(self.payment.public_token, self.payment.provider_payment_id)
        self.booking.refresh_from_db(); self.payment.refresh_from_db()
        self.assertEqual(self.booking.status, 'canceled')
        self.assertEqual(self.payment.status, 'refunded')

    def test_uncertain_network_never_reposts_and_can_reconcile(self):
        with patch('api.refunds.fetch_moyasar_payment', return_value=self.payload()), patch('api.refunds.post_refund', side_effect=TimeoutError) as post:
            for _ in range(2):
                with self.assertRaises(RefundError):
                    cancel_purchase(actor=self.user, booking_id=self.booking.pk)
            self.assertEqual(post.call_count, 1)
        with patch('api.refunds.fetch_moyasar_payment', return_value=self.payload(refunded=3400)):
            cancel_purchase(actor=self.user, booking_id=self.booking.pk)
        self.assertEqual(PaymentRefund.objects.get().state, 'refunded')

    def test_policy_boundary_and_disabled_slot_and_day_offset(self):
        now = timezone.make_aware(__import__('datetime').datetime.combine(self.booking.date, time(15)))
        self.slot.is_active = False; self.slot.save()
        with patch('api.refunds.timezone.now', return_value=now):
            eligible_booking(self.booking)
        with patch('api.refunds.timezone.now', return_value=now+timedelta(seconds=1)):
            with self.assertRaises(RefundError): eligible_booking(self.booking)
            eligible_booking(self.booking, manager=True)
        self.slot.day_offset=1; self.slot.save()
        with patch('api.refunds.timezone.now', return_value=now+timedelta(seconds=1)):
            eligible_booking(self.booking)

    def package(self):
        plan = PackagePlan.objects.create(name='old', price=77, washes_count=4, validity_days=60, included_service=self.service)
        item = CustomerPackage.objects.create(customer=self.user, plan=plan, status='active', remaining_washes=4, expires_at=timezone.now()+timedelta(days=60))
        payment = PaymentTransaction.objects.create(customer_package=item, amount=77, status='paid', provider_payment_id=str(uuid.uuid4()), expires_at=timezone.now())
        return item, payment

    def test_unused_package_refund_uses_original_amount(self):
        item,payment = self.package()
        item.plan.price=99; item.plan.save()
        with patch('api.refunds.fetch_moyasar_payment', return_value=self.payload(payment)), patch('api.refunds.post_refund', return_value=self.payload(payment,7700)):
            cancel_purchase(actor=self.user, package_id=item.pk)
        item.refresh_from_db(); self.assertEqual(item.status, 'revoked'); self.assertEqual(item.remaining_washes,0)

    def test_package_with_booking_or_usage_or_older_than_seven_days_is_rejected(self):
        item,payment = self.package()
        self.booking.customer_package=item; self.booking.save()
        with self.assertRaises(RefundError): cancel_purchase(actor=self.user,package_id=item.pk)
        self.booking.status='completed'; self.booking.save()
        with self.assertRaises(RefundError): cancel_purchase(actor=self.user,package_id=item.pk)
        self.booking.customer_package=None; self.booking.save()
        CustomerPackage.objects.filter(pk=item.pk).update(purchased_at=timezone.now()-timedelta(days=8))
        with self.assertRaises(RefundError): cancel_purchase(actor=self.user,package_id=item.pk)

    def test_package_wash_restored_once_and_expiry_unchanged(self):
        item,_ = self.package(); expiry=item.expires_at
        self.booking.customer_package=item; self.booking.package_wash_used=True; self.booking.save()
        with patch('api.refunds.fetch_moyasar_payment',return_value=self.payload()),patch('api.refunds.post_refund',return_value=self.payload(refunded=3400)):
            cancel_purchase(actor=self.user,booking_id=self.booking.pk)
            cancel_purchase(actor=self.user,booking_id=self.booking.pk)
        item.refresh_from_db();self.assertEqual(item.remaining_washes,5);self.assertEqual(item.expires_at,expiry)

    def test_wrong_owner_and_provider_amount_never_refund(self):
        other=User.objects.create_user('other-refund')
        with self.assertRaises(RefundError): cancel_purchase(actor=other,booking_id=self.booking.pk)
        bad=self.payload();bad['amount']=1
        with patch('api.refunds.fetch_moyasar_payment',return_value=bad),patch('api.refunds.post_refund') as post:
            with self.assertRaises(RefundError): cancel_purchase(actor=self.user,booking_id=self.booking.pk)
            post.assert_not_called()
