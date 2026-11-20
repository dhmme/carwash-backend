"""Manual monthly bonus totals; no automatic payroll or payment side effects."""
from datetime import datetime
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from .models import WorkerMonthlyBonus
from .permissions import IsManager
from .staff_accounts import DELETED_WORKERS_GROUP


def current_month():
    return timezone.localdate().replace(day=1)


def month_value(value):
    try:
        parsed = datetime.strptime(value, '%Y-%m').date()
        if parsed.strftime('%Y-%m') != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise serializers.ValidationError({'month': 'اكتب الشهر بصيغة YYYY-MM.'})


def workers():
    return User.objects.filter(is_staff=True, is_superuser=False).exclude(groups__name=DELETED_WORKERS_GROUP).order_by('id')


def bonus_data(item, month):
    return {'month': month.strftime('%Y-%m'), 'amount': str(item.amount) if item else '0.00',
            'currency': 'SAR', 'updated_at': item.updated_at.isoformat() if item else None}


class BonusInput(serializers.Serializer):
    month = serializers.CharField()
    amount = serializers.DecimalField(max_digits=9, decimal_places=2, min_value=0)
    expected_updated_at = serializers.DateTimeField(allow_null=True)

    def validate_month(self, value):
        return month_value(value)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def worker_bonus(request):
    if request.user.groups.filter(name=DELETED_WORKERS_GROUP).exists():
        return Response(status=403)
    month = current_month()
    item = WorkerMonthlyBonus.objects.filter(worker=request.user, month=month).first()
    # Always own current month; query parameters cannot reveal other workers/months.
    return Response(bonus_data(item, month))


@api_view(['GET'])
@permission_classes([IsManager])
def manager_worker_bonuses(request):
    month = month_value(request.GET['month']) if 'month' in request.GET else current_month()
    amounts = {b.worker_id: b for b in WorkerMonthlyBonus.objects.filter(month=month)}
    return Response({'month': month.strftime('%Y-%m'), 'workers': [
        {'id': w.pk, 'name': w.first_name or w.username, **bonus_data(amounts.get(w.pk), month)}
        for w in workers()
    ]})


@api_view(['PUT'])
@permission_classes([IsManager])
def manager_worker_bonus(request, item_id):
    data = BonusInput(data=request.data)
    data.is_valid(raise_exception=True)
    values = data.validated_data
    with transaction.atomic():
        worker = get_object_or_404(workers().select_for_update(), pk=item_id)
        item = WorkerMonthlyBonus.objects.select_for_update().filter(worker=worker, month=values['month']).first()
        stamp = item.updated_at if item else None
        if stamp != values['expected_updated_at']:
            return Response({'detail': 'تغيّر البونص؛ حدّث الصفحة ثم أعد التعديل.'}, status=409)
        before = str(item.amount) if item else '0.00'
        if item is None:
            item = WorkerMonthlyBonus(worker=worker, month=values['month'])
        item.amount = values['amount']
        item.updated_by = request.user
        item.save()
        LogEntry.objects.create(user=request.user,
            content_type=ContentType.objects.get_for_model(WorkerMonthlyBonus),
            object_id=str(item.pk), object_repr=f'Worker bonus #{worker.pk} {item.month:%Y-%m}',
            action_flag=CHANGE, change_message=f'Manual monthly bonus: {before} -> {item.amount} SAR')
    return Response(bonus_data(item, values['month']))
