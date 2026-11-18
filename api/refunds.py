"""Full refunds: persist intent before network I/O; ambiguous results never resend."""
import json
from base64 import b64encode
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import Booking, BookingTimeSlot, CustomerPackage, PaymentTransaction, PaymentRefund
from .booking_availability import lock_shared_team
from .payment_services import fetch_moyasar_payment, _validate_purchase, _release_package_wash


class RefundError(Exception):
    pass


def eligible_booking(booking, manager=False):
    if booking.status == 'completed':
        raise RefundError('لا يمكن إلغاء طلب مكتمل.')
    if manager:
        return
    if booking.status not in ['pending', 'accepted']:
        raise RefundError('لا يمكن الإلغاء بعد بدء تنفيذ الخدمة. تواصل مع الدعم.')
    slot = BookingTimeSlot.objects.filter(group_id=booking.service_group_id, label=booking.time_slot).first()
    if not slot:
        raise RefundError('تعذر تحديد وقت الموعد القديم؛ تواصل مع الدعم.')
    start = timezone.make_aware(datetime.combine(booking.date + timedelta(days=slot.day_offset), slot.start_time))
    if start - timezone.now() < timedelta(hours=1):
        raise RefundError('الإلغاء متاح قبل الموعد بساعة على الأقل. تواصل مع الدعم.')


def post_refund(payment):
    request = Request(f'{settings.MOYASAR_API_URL}/payments/{payment.provider_payment_id}/refund',
        data=json.dumps({'amount': int(payment.amount * 100)}).encode(),
        headers={'Authorization': 'Basic ' + b64encode(f'{settings.MOYASAR_SECRET_KEY}:'.encode()).decode(),
                 'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode())


def cancel_purchase(*, actor, booking_id=None, package_id=None, manager=False):
    fresh = False
    with transaction.atomic():
        lock_shared_team()
        if booking_id:
            item = Booking.objects.select_for_update().get(pk=booking_id)
        else:
            item = CustomerPackage.objects.select_for_update().get(pk=package_id)
        if not manager and item.customer_id != actor.pk:
            raise RefundError('هذا الطلب لا يخص حسابك.')
        payment = PaymentTransaction.objects.select_for_update().filter(
            **({'booking_id': booking_id} if booking_id else {'customer_package_id': package_id})).first()
        refund = PaymentRefund.objects.filter(payment=payment).first() if payment else None
        if refund and refund.state == 'refunded':
            return 'تم الاسترجاع سابقًا؛ لن يتكرر رد المبلغ.'
        if not refund:
            if booking_id:
                if item.status == 'canceled':
                    return 'الحجز ملغي بالفعل.'
                eligible_booking(item, manager)
            else:
                if item.status != 'active' or timezone.now() - item.purchased_at > timedelta(days=7):
                    raise RefundError('الاسترجاع متاح للباقة النشطة خلال 7 أيام من الشراء. للحالات الاستثنائية تواصل مع الدعم.')
                if item.bookings.filter(status='completed').exists():
                    raise RefundError('لا يمكن استرجاع باقة استُخدمت إحدى غسلاتها.')
                if item.bookings.exclude(status='canceled').exists():
                    raise RefundError('ألغِ مواعيد الباقة القائمة أولًا، ثم اطلب الاسترجاع.')
            if not payment or payment.status != 'paid':
                if not booking_id:
                    raise RefundError('استرجاع هذه الباقة يحتاج مراجعة الإدارة حسب وسيلة الدفع.')
                item.status = 'canceled'
                item.save(update_fields=['status'])
                _release_package_wash(item)
                if payment and payment.status == 'pending':
                    payment.status = 'expired'
                    payment.save(update_fields=['status'])
                return 'تم إلغاء الحجز وإتاحة الوقت من جديد.'
            refund = PaymentRefund.objects.create(payment=payment, actor=actor, original_status=item.status)
            item.status = 'refund_pending'
            item.save(update_fields=['status'])
            fresh = True
    # The committed intent prevents duplicate POSTs even on a crash/timeout.
    try:
        payload = fetch_moyasar_payment(payment.provider_payment_id)
        _validate_purchase(payment, payload)
        amount = int(payment.amount * 100)
        if payload.get('refunded', 0) < amount:
            if not fresh or payload.get('refunded', 0) or payload.get('status') not in ['paid', 'captured']:
                raise RefundError('الاسترجاع قيد التحقق؛ لن نكرر طلب رد المبلغ. تواصل مع الدعم.')
            payload = post_refund(payment)
            _validate_purchase(payment, payload)
        if payload.get('id') != payment.provider_payment_id or payload.get('refunded', 0) != amount:
            raise RefundError('الاسترجاع قيد التحقق لدى مزود الدفع. تواصل مع الدعم.')
    except Exception:
        PaymentRefund.objects.filter(pk=refund.pk).exclude(state='refunded').update(state='unknown')
        raise RefundError('الاسترجاع قيد التحقق. لم يُلغَ رصيدك نهائيًا؛ تواصل مع الدعم ولا تعِد الدفع.')
    with transaction.atomic():
        lock_shared_team()
        payment = PaymentTransaction.objects.select_for_update().get(pk=payment.pk)
        refund = PaymentRefund.objects.select_for_update().get(pk=refund.pk)
        if refund.state != 'refunded':
            if booking_id:
                item = Booking.objects.select_for_update().get(pk=booking_id)
                item.status = 'canceled'
                item.save(update_fields=['status'])
                _release_package_wash(item)
            else:
                item = CustomerPackage.objects.select_for_update().get(pk=package_id)
                item.status = 'revoked'
                item.remaining_washes = 0
                item.save(update_fields=['status', 'remaining_washes'])
            payment.status = 'refunded'
            payment.save(update_fields=['status', 'updated_at'])
            refund.state = 'refunded'
            refund.completed_at = timezone.now()
            refund.save(update_fields=['state', 'completed_at'])
    return 'تم الإلغاء واسترجاع المبلغ إلى وسيلة الدفع الأصلية. ظهوره يعتمد على البنك ومزود الدفع.'
