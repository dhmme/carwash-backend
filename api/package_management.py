from io import BytesIO
from math import ceil

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from openpyxl import Workbook
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import CustomerPackage, PackageAdjustment, PackagePlan
from .permissions import IsManager
from .serializers import CustomerPackageSerializer


def package_catalog_queryset():
    now = timezone.now()
    accepted = (Q(purchases__status__in=['active', 'expired']) |
                Q(purchases__status='revoked', purchases__activated_at__isnull=False))
    paid = accepted & ~Q(purchases__payment_method='manager_grant')
    return PackagePlan.objects.select_related('included_service').annotate(
        customer_count=Count('purchases__customer', filter=accepted, distinct=True),
        buyers_count=Count('purchases__customer', filter=paid, distinct=True),
        purchase_count=Count('purchases', filter=paid),
        grants_count=Count('purchases', filter=accepted & Q(purchases__payment_method='manager_grant')),
        active_count=Count('purchases', filter=Q(
            purchases__status='active', purchases__remaining_washes__gt=0,
            purchases__expires_at__gt=now,
        )),
        pending_count=Count('purchases', filter=Q(purchases__status='pending')),
        expired_count=Count('purchases', filter=(
            Q(purchases__status='expired') |
            Q(purchases__status='active', purchases__expires_at__lte=now) |
            Q(purchases__status='active', purchases__expires_at__isnull=True)
        )),
        exhausted_count=Count('purchases', filter=Q(
            purchases__status='active', purchases__remaining_washes=0,
            purchases__expires_at__gt=now,
        )),
    )


def freeze_legacy_package_terms(plan):
    # Also covers requests created by the previous app version during a rolling deploy.
    items = CustomerPackage.objects.filter(plan=plan).filter(
        Q(plan_snapshot={}) | Q(included_service_snapshot__isnull=True)
    ).select_related('plan__included_service').select_for_update(of=('self',))
    for item in items:
        item.save(update_fields=['plan_snapshot', 'included_service_snapshot'])


def package_purchases_queryset(request, plan_id=None):
    items = CustomerPackage.objects.select_related('customer', 'plan').annotate(
        used_washes_count=Count('bookings', filter=Q(bookings__package_wash_used=True)),
    )
    for key in ['customer', 'plan']:
        value = plan_id if key == 'plan' and plan_id is not None else request.GET.get(key)
        if value not in [None, '']:
            try:
                number = int(value)
                if number < 1:
                    raise ValueError()
            except (ValueError, TypeError):
                raise serializers.ValidationError({key: 'القيمة غير صحيحة.'})
            items = items.filter(**{key + '_id': number})
    search = request.GET.get('search', '').strip()
    if search:
        items = items.filter(
            Q(customer__first_name__icontains=search) |
            Q(customer__last_name__icontains=search) |
            Q(customer__username__icontains=search)
        )
    now = timezone.now()
    state = request.GET.get('status', '')
    if state == 'active':
        items = items.filter(status='active', remaining_washes__gt=0, expires_at__gt=now)
    elif state == 'exhausted':
        items = items.filter(status='active', remaining_washes=0, expires_at__gt=now)
    elif state == 'expired':
        items = items.filter(Q(status='expired') | Q(status='active', expires_at__lte=now) |
                             Q(status='active', expires_at__isnull=True))
    elif state in ['pending', 'revoked', 'rejected']:
        items = items.filter(status=state)
    elif state:
        raise serializers.ValidationError({'status': 'الحالة غير صحيحة.'})
    source = request.GET.get('source', '')
    if source == 'purchase':
        items = items.exclude(payment_method='manager_grant')
    elif source == 'grant':
        items = items.filter(payment_method='manager_grant')
    elif source:
        raise serializers.ValidationError({'source': 'المصدر غير صحيح.'})
    dates = {}
    for key, lookup in [('from', 'purchased_at__date__gte'), ('to', 'purchased_at__date__lte')]:
        if request.GET.get(key):
            try:
                parsed = parse_date(request.GET[key])
            except ValueError:
                parsed = None
            if parsed is None:
                raise serializers.ValidationError({key: 'التاريخ غير صحيح.'})
            dates[key] = parsed
            items = items.filter(**{lookup: parsed})
    if 'from' in dates and 'to' in dates and dates['from'] > dates['to']:
        raise serializers.ValidationError({'detail': 'بداية الفترة بعد نهايتها.'})
    return items.order_by('-id')


def package_state(item):
    return {
        'remaining_washes': item.remaining_washes, 'status': item.status,
        'expires_at': item.expires_at.isoformat() if item.expires_at else None,
    }


def record_package_adjustment(request, item, action, reason, before, reference=None):
    return PackageAdjustment.objects.create(
        package=item, actor=request.user, action=action, reason=reason,
        before=before, after=package_state(item), reference=reference,
    )


class PackageBalanceSerializer(serializers.Serializer):
    remaining_washes = serializers.IntegerField(min_value=0, max_value=10000)
    expires_at = serializers.DateTimeField()
    expected_remaining_washes = serializers.IntegerField(min_value=0)
    expected_expires_at = serializers.DateTimeField(allow_null=True)
    expected_status = serializers.ChoiceField(choices=['active', 'expired'])
    reason = serializers.CharField(max_length=500, allow_blank=False)
    reference = serializers.UUIDField()


@api_view(['POST'])
@permission_classes([IsManager])
def manager_adjust_package(request, item_id):
    serializer = PackageBalanceSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    try:
        with transaction.atomic():
            item = get_object_or_404(CustomerPackage.objects.select_for_update(), pk=item_id)
            previous = PackageAdjustment.objects.filter(reference=data['reference']).first()
            if previous:
                if (previous.package_id != item.pk or previous.action != 'adjust' or
                    previous.after['remaining_washes'] != data['remaining_washes'] or
                    previous.after['expires_at'] != data['expires_at'].isoformat()):
                    return Response({'detail': 'مرجع التعديل مستخدم لعملية أخرى.'}, status=409)
                return Response(CustomerPackageSerializer(item).data)
            if item.status not in ['active', 'expired']:
                return Response({'detail': 'يمكن تعديل الباقات المعتمدة فقط. الباقة الملغاة لا تتفعل بالتعديل.'}, status=400)
            if (item.remaining_washes != data['expected_remaining_washes'] or
                item.expires_at != data['expected_expires_at'] or item.status != data['expected_status']):
                return Response({'detail': 'تغير رصيد الباقة أو صلاحيتها. حدّث البيانات ثم أعد التعديل.'}, status=409)
            if item.activated_at and data['expires_at'] <= item.activated_at:
                return Response({'detail': 'انتهاء الصلاحية يجب أن يكون بعد تفعيل الباقة.'}, status=400)
            before = package_state(item)
            item.remaining_washes = data['remaining_washes']
            item.expires_at = data['expires_at']
            if item.status == 'expired' and item.expires_at > timezone.now():
                item.status = 'active'
            if before == package_state(item):
                return Response({'detail': 'لم تغيّر الرصيد أو الصلاحية.'}, status=400)
            item.save(update_fields=['remaining_washes', 'expires_at', 'status'])
            record_package_adjustment(request, item, 'adjust', data['reason'], before, data['reference'])
    except IntegrityError:
        return Response({'detail': 'مرجع التعديل مستخدم. حدّث البيانات قبل المحاولة.'}, status=409)
    return Response(CustomerPackageSerializer(item).data)


@api_view(['GET'])
@permission_classes([IsManager])
def manager_package_history(request, item_id):
    item = get_object_or_404(CustomerPackage, pk=item_id)
    return Response([{
        'id': entry.pk, 'action': entry.action, 'reason': entry.reason,
        'before': entry.before, 'after': entry.after,
        'actor': (entry.actor.first_name or entry.actor.username) if entry.actor else 'حساب محذوف',
        'created_at': entry.created_at,
    } for entry in item.adjustments.select_related('actor')[:200]])


@api_view(['GET'])
@permission_classes([IsManager])
def manager_package_export(request, item_id):
    plan = get_object_or_404(PackagePlan, pk=item_id)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'مشتركو الباقة'
    sheet.sheet_view.rightToLeft = True
    sheet.append(['رقم الاشتراك', 'اسم العميل', 'الجوال', 'الباقة عند الشراء', 'المصدر',
                  'الحالة', 'تاريخ الشراء', 'تاريخ الانتهاء', 'الغسلات الأصلية',
                  'المستخدمة والمحجوزة', 'المتبقية'])
    for item in package_purchases_queryset(request, plan.pk):
        data = CustomerPackageSerializer(item).data
        row = [item.pk, data['customer_name'], data['customer_phone'], data['plan_name'],
               'منح إداري' if item.payment_method == 'manager_grant' else 'شراء',
               {'active': 'سارية', 'exhausted': 'مستهلكة', 'expired': 'منتهية', 'pending': 'بانتظار الاعتماد',
                'revoked': 'ملغاة', 'rejected': 'مرفوضة'}.get(data['effective_status'], data['effective_status']),
               str(data['purchased_at']), str(data['expires_at'] or ''),
               data['washes_count'], data['used_washes'], data['remaining_washes']]
        sheet.append(row)
        # Names and phone numbers remain text even when they start with =, +, or @.
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = 's'
                cell.number_format = '@'
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = 23
    buffer = BytesIO()
    workbook.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="package-{plan.pk}-members.xlsx"'
    response['Cache-Control'] = 'no-store'
    return response


def paginated_package_purchases(request):
    items = package_purchases_queryset(request)
    if 'page' not in request.GET:
        return Response(CustomerPackageSerializer(items[:500], many=True).data)
    try:
        page = int(request.GET.get('page', '1'))
        size = int(request.GET.get('page_size', '50'))
        if page < 1 or not 1 <= size <= 100:
            raise ValueError()
    except (ValueError, TypeError):
        raise serializers.ValidationError({'detail': 'رقم الصفحة أو حجمها غير صحيح.'})
    count = items.count()
    return Response({
        'count': count, 'page': page, 'page_size': size, 'total_pages': max(1, ceil(count / size)),
        'results': CustomerPackageSerializer(items[(page - 1) * size:page * size], many=True).data,
    })
