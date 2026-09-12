import json
import uuid
from base64 import b64encode
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import IntegrityError, transaction as db_transaction
from django.utils import timezone

from .models import Booking, Invoice, PaymentTransaction


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


def expire_stale_payments():
    expired_ids = list(
        PaymentTransaction.objects.filter(
            status='pending',
            expires_at__lt=timezone.now(),
        ).values_list('id', flat=True)
    )
    if not expired_ids:
        return
    PaymentTransaction.objects.filter(id__in=expired_ids).update(status='expired')
    Booking.objects.filter(
        payment__id__in=expired_ids,
        status='pending',
    ).update(status='canceled')


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
    try:
        clean_payment_id = str(uuid.UUID(str(payment_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PaymentVerificationError('رقم عملية الدفع غير صالح.') from exc

    payload = fetch_moyasar_payment(clean_payment_id)
    try:
        provider_payload_id = str(uuid.UUID(str(payload.get('id'))))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PaymentVerificationError('استجابة ميسر لا تحتوي رقم عملية صالحًا.') from exc

    with db_transaction.atomic():
        payment = PaymentTransaction.objects.select_for_update().select_related(
            'booking'
        ).get(public_token=transaction_token)
        metadata = payload.get('metadata') or {}
        expected_amount = int(payment.amount * Decimal('100'))
        if provider_payload_id != clean_payment_id:
            raise PaymentVerificationError('رقم العملية في استجابة ميسر غير مطابق.')
        if payload.get('amount') != expected_amount or payload.get('currency') != payment.currency:
            raise PaymentVerificationError('قيمة أو عملة عملية الدفع غير مطابقة للطلب.')
        if str(metadata.get('booking_id', '')) != str(payment.booking_id):
            raise PaymentVerificationError('عملية الدفع لا تخص هذا الطلب.')
        if payment.provider_payment_id and payment.provider_payment_id != clean_payment_id:
            raise PaymentVerificationError('رقم العملية لا يطابق محاولة الدفع الحالية.')

        payment.provider_payment_id = clean_payment_id
        payment.provider_response = _safe_provider_response(payload)
        try:
            payment.save(update_fields=[
                'provider_payment_id', 'provider_response', 'updated_at',
            ])
        except IntegrityError as exc:
            raise PaymentVerificationError('عملية الدفع مرتبطة بطلب آخر.') from exc
        return payment


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
        payment = PaymentTransaction.objects.select_for_update().select_related(
            'booking__service'
        ).get(public_token=transaction_token)
        booking = payment.booking

        if payment.provider_payment_id and payment.provider_payment_id != clean_payment_id:
            raise PaymentVerificationError('رقم العملية لا يطابق محاولة الدفع الحالية.')

        expected_amount = int(payment.amount * Decimal('100'))
        metadata = payload.get('metadata') or {}
        if payload.get('amount') != expected_amount or payload.get('currency') != payment.currency:
            raise PaymentVerificationError('قيمة أو عملة عملية الدفع غير مطابقة للطلب.')
        if str(metadata.get('booking_id', '')) != str(booking.id):
            raise PaymentVerificationError('عملية الدفع لا تخص هذا الطلب.')

        payment.provider_payment_id = clean_payment_id
        payment.provider_response = _safe_provider_response(payload)
        provider_status = payload.get('status')

        if provider_status == 'paid':
            payment.status = 'paid'
            payment.paid_at = timezone.now()
            if booking.status == 'canceled':
                slot_taken = Booking.objects.filter(
                    date=booking.date,
                    time_slot=booking.time_slot,
                ).exclude(pk=booking.pk).exclude(status='canceled').exists()
                if slot_taken:
                    payment.save()
                    return payment
            booking.status = 'accepted'
            booking.save(update_fields=['status'])
            invoice, _ = Invoice.objects.get_or_create(booking=booking)
            invoice.ensure_snapshot()
        elif provider_status == 'failed':
            payment.status = 'failed'
            booking.status = 'canceled'
            booking.save(update_fields=['status'])
        else:
            payment.status = 'pending'

        try:
            payment.save()
        except IntegrityError as exc:
            raise PaymentVerificationError('عملية الدفع مرتبطة بطلب آخر.') from exc
        return payment
