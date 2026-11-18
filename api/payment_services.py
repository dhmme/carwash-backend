import json
import uuid
from base64 import b64encode
from decimal import Decimal
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import IntegrityError, transaction as db_transaction
from django.utils import timezone

from .models import Booking, CustomerPackage, Invoice, PaymentTransaction
from .booking_availability import lock_shared_team, shared_slot_is_booked


class PaymentVerificationError(Exception):
    pass


def moyasar_mode():
    publishable_key = settings.MOYASAR_PUBLISHABLE_KEY
    secret_key = settings.MOYASAR_SECRET_KEY
    if publishable_key.startswith('pk_test_') and secret_key.startswith('sk_test_'):
        return 'test'
    if publishable_key.startswith('pk_live_') and secret_key.startswith('sk_live_'):
        return 'live'
    return 'disabled'


def _release_package_wash(booking):
    if booking.package_wash_used and booking.customer_package_id:
        package = CustomerPackage.objects.select_for_update().get(pk=booking.customer_package_id)
        package.remaining_washes += 1
        package.save(update_fields=['remaining_washes'])
        booking.package_wash_used = False
        booking.save(update_fields=['package_wash_used'])


def expire_stale_payments():
    expired_ids = PaymentTransaction.objects.filter(
        status='pending', expires_at__lt=timezone.now(),
    ).values_list('id', flat=True)
    for payment_id in list(expired_ids):
        with db_transaction.atomic():
            lock_shared_team()
            payment = PaymentTransaction.objects.select_for_update().get(pk=payment_id)
            if payment.status != 'pending' or payment.expires_at >= timezone.now():
                continue
            payment.status = 'expired'
            payment.save(update_fields=['status', 'updated_at'])
            if payment.booking_id:
                booking = Booking.objects.select_for_update().get(pk=payment.booking_id)
                if booking.status == 'pending':
                    booking.status = 'canceled'
                    booking.save(update_fields=['status'])
                    _release_package_wash(booking)


def _validate_purchase(payment, payload):
    expected_amount = int(payment.amount * Decimal('100'))
    if payload.get('amount') != expected_amount or payload.get('currency') != payment.currency:
        raise PaymentVerificationError('قيمة أو عملة عملية الدفع غير مطابقة للطلب.')
    metadata = payload.get('metadata') or {}
    field = 'booking_id' if payment.booking_id else 'customer_package_id'
    expected_id = payment.booking_id or payment.customer_package_id
    if str(metadata.get(field, '')) != str(expected_id):
        raise PaymentVerificationError('عملية الدفع لا تخص هذا الطلب.')



def _safe_provider_response(payload):
    source = payload.get('source') or {}
    return {
        'id': payload.get('id'),
        'status': payload.get('status'),
        'amount': payload.get('amount'),
        'currency': payload.get('currency'),
        'description': payload.get('description'),
        'metadata': payload.get('metadata') or {},
        'source': {
            'type': source.get('type'),
            'company': source.get('company'),
            'number': source.get('number'),
            'message': source.get('message'),
            'reference_number': source.get('reference_number'),
        },
    }


def fetch_moyasar_payment(payment_id):
    try:
        clean_id = str(uuid.UUID(str(payment_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PaymentVerificationError('رقم عملية الدفع غير صالح.') from exc

    secret_key = settings.MOYASAR_SECRET_KEY
    if not secret_key:
        raise PaymentVerificationError('مفتاح ميسر السري غير مضاف في الخادم.')

    credentials = b64encode(f'{secret_key}:'.encode()).decode()
    request = Request(
        f'{settings.MOYASAR_API_URL}/payments/{clean_id}',
        headers={
            'Authorization': f'Basic {credentials}',
            'Accept': 'application/json',
            'User-Agent': 'Code-Care/1.0',
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        raise PaymentVerificationError('رفضت ميسر التحقق من العملية.') from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PaymentVerificationError('تعذر الاتصال بميسر للتحقق من الدفع.') from exc


def record_moyasar_reference(transaction_token, payment_id):
    # Verify immediately as well as on redirect, so closing the result page
    # after a successful wallet payment does not leave the purchase pending.
    return verify_moyasar_transaction(transaction_token, payment_id)


def verify_moyasar_transaction(transaction_token, payment_id):
    try:
        clean_payment_id = str(uuid.UUID(str(payment_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PaymentVerificationError('رقم عملية الدفع غير صالح.') from exc

    payload = fetch_moyasar_payment(clean_payment_id)

    try:
        provider_payload_id = str(uuid.UUID(str(payload.get('id'))))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PaymentVerificationError('استجابة ميسر لا تحتوي رقم عملية صالحًا.') from exc
    if provider_payload_id != clean_payment_id:
        raise PaymentVerificationError('رقم العملية في استجابة ميسر غير مطابق.')

    with db_transaction.atomic():
        lock_shared_team()
        payment = PaymentTransaction.objects.select_for_update().get(public_token=transaction_token)
        if payment.provider_payment_id and payment.provider_payment_id != clean_payment_id:
            raise PaymentVerificationError('رقم العملية لا يطابق محاولة الدفع الحالية.')
        _validate_purchase(payment, payload)
        if payment.status == 'paid':
            return payment

        payment.provider_payment_id = clean_payment_id
        payment.provider_response = _safe_provider_response(payload)
        provider_status = payload.get('status')

        if provider_status == 'paid':
            payment.status = 'paid'
            payment.paid_at = timezone.now()
            if payment.customer_package_id:
                package = CustomerPackage.objects.select_for_update().get(pk=payment.customer_package_id)
                if package.status == 'pending':
                    terms = package.purchase_terms
                    package.status = 'active'
                    package.remaining_washes = terms['washes_count']
                    package.activated_at = payment.paid_at
                    package.expires_at = payment.paid_at + timedelta(days=terms['validity_days'])
                    package.save(update_fields=['status', 'remaining_washes', 'activated_at', 'expires_at'])
            else:
                booking = Booking.objects.select_for_update().get(pk=payment.booking_id)
                if booking.status == 'pending':
                    booking.status = 'accepted'
                    booking.save(update_fields=['status'])
                    invoice, _ = Invoice.objects.get_or_create(booking=booking)
                    invoice.ensure_snapshot()
        elif provider_status == 'failed':
            if payment.status == 'pending':
                payment.status = 'failed'
                if payment.booking_id:
                    booking = Booking.objects.select_for_update().get(pk=payment.booking_id)
                    if booking.status == 'pending':
                        booking.status = 'canceled'
                        booking.save(update_fields=['status'])
                        _release_package_wash(booking)
        # Pending responses cannot revive a failed or expired attempt.
        try:
            payment.save()
        except IntegrityError as exc:
            raise PaymentVerificationError('عملية الدفع مرتبطة بطلب آخر.') from exc
        return payment
