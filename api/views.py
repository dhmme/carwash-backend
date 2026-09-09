from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.conf import settings
from django.db.models import Count, Sum
from django.http import FileResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from decimal import Decimal
from pathlib import Path
from django.shortcuts import get_object_or_404, render
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from .models import (
    AddOn, Booking, Car, Location, Service, VehicleCategory, Invoice, Expense,
    PaymentTransaction,
)
from .payment_services import (
    PaymentVerificationError,
    expire_stale_payments,
    verify_moyasar_transaction,
)
from .permissions import IsManager
from .serializers import (
    BookingSerializer,
    AddOnSerializer,
    BookingStatusSerializer,
    CarSerializer,
    LocationSerializer,
    RegisterSerializer,
    ServiceSerializer,
    UserSerializer,
    WorkerBookingSerializer,
    VehicleCategorySerializer,
    ManagerBookingSerializer,
    InvoiceSerializer,
    ManagerStaffSerializer,
    ExpenseSerializer,
)


@api_view(['GET'])
@permission_classes([AllowAny])
def hello_view(request):
    return Response({'message': 'Car Wash API is working!'})


@api_view(['POST'])
@permission_classes([AllowAny])
def register_view(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.save()
    token, _ = Token.objects.get_or_create(user=user)
    return Response(
        {'token': token.key, 'user': UserSerializer(user).data},
        status=status.HTTP_201_CREATED,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    username = request.data.get('username', '').strip()
    password = request.data.get('password', '')
    user = authenticate(request, username=username, password=password)
    if user is None:
        return Response(
            {'detail': 'رقم الجوال أو كلمة المرور غير صحيحة.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    token, _ = Token.objects.get_or_create(user=user)
    return Response({
        'token': token.key,
        'user': UserSerializer(user).data,
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    Token.objects.filter(user=request.user).delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([AllowAny])
def service_list(request):
    services = Service.objects.filter(is_active=True)
    return Response(ServiceSerializer(services, many=True).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def add_on_list(request):
    add_ons = AddOn.objects.filter(is_active=True)
    return Response(AddOnSerializer(add_ons, many=True).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def vehicle_category_list(request):
    categories = VehicleCategory.objects.filter(is_active=True).order_by('id')
    return Response(VehicleCategorySerializer(categories, many=True).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def payment_config_view(request):
    publishable_key = settings.MOYASAR_PUBLISHABLE_KEY
    online_enabled = bool(publishable_key and settings.MOYASAR_SECRET_KEY)
    return Response({
        'online_enabled': online_enabled,
        'mode': (
            'test' if publishable_key.startswith('pk_test_')
            else 'live' if online_enabled
            else 'disabled'
        ),
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def car_list_create(request):
    if request.method == 'GET':
        cars = Car.objects.filter(user=request.user)
        return Response(CarSerializer(cars, many=True).data)

    serializer = CarSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save(user=request.user)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def location_list_create(request):
    if request.method == 'GET':
        locations = Location.objects.filter(user=request.user).order_by('-id')
        return Response(LocationSerializer(locations, many=True).data)

    serializer = LocationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save(user=request.user)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def booking_list_create(request):
    expire_stale_payments()
    if request.method == 'GET':
        bookings = Booking.objects.filter(
            customer=request.user
        ).select_related('service', 'customer', 'payment').order_by('-id')
        serializer = BookingSerializer(
            bookings,
            many=True,
            context={'request': request},
        )
        return Response(serializer.data)

    serializer = BookingSerializer(
        data=request.data,
        context={'request': request},
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([AllowAny])
def booked_slots(request):
    expire_stale_payments()
    date = request.GET.get('date')
    if not date:
        return Response(
            {'error': 'date query parameter is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    slots = Booking.objects.filter(date=date).exclude(
        status='canceled'
    ).values_list('time_slot', flat=True)
    return Response({'booked': list(slots)})


@api_view(['GET'])
@permission_classes([IsAdminUser])
def worker_bookings(request):
    date = request.GET.get('date') or timezone.localdate()
    bookings = Booking.objects.filter(date=date).exclude(status='pending').exclude(
        status__in=['completed', 'canceled']
    ).select_related('service', 'car', 'customer', 'payment').order_by('time_slot')
    return Response(WorkerBookingSerializer(bookings, many=True).data)


@api_view(['PATCH'])
@permission_classes([IsAdminUser])
def update_booking_status(request, booking_id):
    try:
        booking = Booking.objects.get(pk=booking_id)
    except Booking.DoesNotExist:
        return Response(
            {'detail': 'الطلب غير موجود.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    serializer = BookingStatusSerializer(
        booking,
        data=request.data,
        partial=True,
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsManager])
def manager_dashboard(request):
    today = timezone.localdate()
    today_qs = Booking.objects.filter(date=today)
    month_qs = Booking.objects.filter(date__year=today.year, date__month=today.month)
    return Response({
        'today_total': today_qs.count(),
        'today_active': today_qs.exclude(status__in=['completed', 'canceled']).count(),
        'today_completed': today_qs.filter(status='completed').count(),
        'today_revenue': today_qs.filter(status='completed').aggregate(v=Sum('total_price'))['v'] or 0,
        'month_revenue': month_qs.filter(status='completed').aggregate(v=Sum('total_price'))['v'] or 0,
        'customers': User.objects.filter(is_staff=False).count(),
        'workers': User.objects.filter(is_staff=True, is_superuser=False, is_active=True).count(),
    })


def _catalog(request, model, serializer_class):
    if request.method == 'GET':
        return Response(serializer_class(model.objects.all().order_by('id'), many=True).data)
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


def _catalog_detail(request, model, serializer_class, item_id):
    try:
        item = model.objects.get(pk=item_id)
    except model.DoesNotExist:
        return Response({'detail': 'العنصر غير موجود.'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'DELETE':
        item.is_active = False
        item.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = serializer_class(item, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(['GET', 'POST'])
@permission_classes([IsManager])
def manager_services(request):
    return _catalog(request, Service, ServiceSerializer)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsManager])
def manager_service_detail(request, item_id):
    return _catalog_detail(request, Service, ServiceSerializer, item_id)


@api_view(['GET', 'POST'])
@permission_classes([IsManager])
def manager_add_ons(request):
    return _catalog(request, AddOn, AddOnSerializer)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsManager])
def manager_add_on_detail(request, item_id):
    return _catalog_detail(request, AddOn, AddOnSerializer, item_id)


@api_view(['GET', 'POST'])
@permission_classes([IsManager])
def manager_categories(request):
    return _catalog(request, VehicleCategory, VehicleCategorySerializer)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsManager])
def manager_category_detail(request, item_id):
    return _catalog_detail(request, VehicleCategory, VehicleCategorySerializer, item_id)


@api_view(['GET'])
@permission_classes([IsManager])
def manager_bookings(request):
    bookings = Booking.objects.select_related(
        'service', 'car', 'customer', 'invoice', 'payment'
    ).order_by('-date', '-id')
    status_filter = request.GET.get('status')
    if status_filter:
        bookings = bookings.filter(status=status_filter)
    return Response(ManagerBookingSerializer(bookings[:500], many=True).data)


@api_view(['GET'])
@permission_classes([IsManager])
def manager_invoices(request):
    for booking in Booking.objects.exclude(
        status__in=['canceled', 'pending']
    ).filter(invoice__isnull=True):
        Invoice.objects.get_or_create(booking=booking)
    invoices = Invoice.objects.select_related('booking__service', 'booking__customer').order_by('-id')
    for invoice in invoices:
        invoice.ensure_snapshot()
    return Response(InvoiceSerializer(invoices[:500], many=True).data)


def invoice_print_view(request, token):
    invoice = get_object_or_404(
        Invoice.objects.select_related('booking__service', 'booking__customer'),
        public_token=token,
    )
    invoice.ensure_snapshot()
    booking = invoice.booking
    payment_names = {
        'cash': 'كاش',
        'card': 'شبكة',
        'bank_transfer': 'تحويل بنكي',
        'online': 'دفع إلكتروني',
    }
    return render(request, 'api/invoice.html', {
        'invoice': invoice,
        'booking': booking,
        'customer_name': booking.customer_name or booking.customer.first_name or booking.customer.username,
        'customer_phone': booking.customer_phone or booking.customer.username,
        'payment_name': payment_names.get(booking.payment_method, booking.payment_method),
    })


def invoice_logo_view(request):
    logo_path = Path(__file__).resolve().parent / 'assets' / 'code-care-logo.png'
    response = FileResponse(logo_path.open('rb'), content_type='image/png')
    response['Cache-Control'] = 'public, max-age=86400'
    return response


def moyasar_checkout_view(request, token):
    expire_stale_payments()
    payment = get_object_or_404(
        PaymentTransaction.objects.select_related('booking__service'),
        public_token=token,
    )
    booking = payment.booking
    publishable_key = settings.MOYASAR_PUBLISHABLE_KEY
    callback_url = request.build_absolute_uri(
        reverse('moyasar-callback', kwargs={'token': payment.public_token})
    )
    reference_url = request.build_absolute_uri(
        reverse('moyasar-reference', kwargs={'token': payment.public_token})
    )
    payment_config = {
        'amount': int(payment.amount * Decimal('100')),
        'currency': payment.currency,
        'description': f'Code Care booking #{booking.id}',
        'publishable_api_key': publishable_key,
        'callback_url': callback_url,
        'supported_networks': ['mada', 'visa', 'mastercard'],
        'methods': ['creditcard'],
        'language': 'ar',
        'fixed_width': False,
        'metadata': {
            'booking_id': str(booking.id),
        },
    }
    return render(request, 'api/moyasar_checkout.html', {
        'payment': payment,
        'booking': booking,
        'payment_config': payment_config,
        'reference_url': reference_url,
        'configured': bool(publishable_key and settings.MOYASAR_SECRET_KEY),
        'customer_app_url': settings.CUSTOMER_APP_URL,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def moyasar_reference_view(request, token):
    payment = get_object_or_404(PaymentTransaction, public_token=token)
    payment_id = str(request.data.get('id', '')).strip()
    try:
        import uuid
        payment_id = str(uuid.UUID(payment_id))
    except (ValueError, TypeError, AttributeError):
        return Response({'detail': 'رقم العملية غير صالح.'}, status=status.HTTP_400_BAD_REQUEST)
    if payment.provider_payment_id and payment.provider_payment_id != payment_id:
        return Response({'detail': 'العملية لا تطابق الطلب.'}, status=status.HTTP_409_CONFLICT)
    payment.provider_payment_id = payment_id
    payment.save(update_fields=['provider_payment_id', 'updated_at'])
    return Response({'saved': True})


def moyasar_callback_view(request, token):
    payment_id = request.GET.get('id', '')
    result = 'error'
    title = 'تعذر تأكيد الدفع'
    message = 'لم نتمكن من التحقق من العملية. يمكنك المحاولة مرة أخرى من صفحة طلباتي.'
    try:
        payment = verify_moyasar_transaction(token, payment_id)
        if payment.status == 'paid' and payment.booking.status == 'accepted':
            result = 'success'
            title = 'تم الدفع وتأكيد الحجز'
            message = 'تم استلام الدفعة بنجاح، وسيظهر الطلب الآن لدى فريق الغسيل.'
        elif payment.status == 'paid':
            title = 'تم الدفع ويحتاج الطلب إلى مراجعة'
            message = 'استلمنا الدفعة، لكن الموعد يحتاج مراجعة من الإدارة. لن يتم تكرار الخصم.'
        elif payment.status == 'failed':
            title = 'لم تنجح عملية الدفع'
            message = 'لم يتم خصم المبلغ. عد إلى طلباتك واختر موعدًا جديدًا.'
        else:
            result = 'pending'
            title = 'العملية قيد التحقق'
            message = 'لم تصل نتيجة نهائية بعد. تحقق من الطلب بعد قليل.'
    except (PaymentTransaction.DoesNotExist, PaymentVerificationError):
        pass
    return render(request, 'api/payment_result.html', {
        'result': result,
        'title': title,
        'message': message,
        'customer_app_url': settings.CUSTOMER_APP_URL,
    })


@api_view(['GET', 'POST'])
@permission_classes([IsManager])
def manager_workers(request):
    if request.method == 'GET':
        workers = User.objects.filter(is_staff=True, is_superuser=False).order_by('id')
        return Response(ManagerStaffSerializer(workers, many=True).data)
    serializer = ManagerStaffSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['PATCH'])
@permission_classes([IsManager])
def manager_worker_detail(request, item_id):
    try:
        worker = User.objects.get(pk=item_id, is_staff=True, is_superuser=False)
    except User.DoesNotExist:
        return Response({'detail': 'العامل غير موجود.'}, status=status.HTTP_404_NOT_FOUND)
    serializer = ManagerStaffSerializer(worker, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsManager])
def manager_ledger(request):
    today = timezone.localdate()
    date_from = parse_date(request.GET.get('from', '')) or today
    date_to = parse_date(request.GET.get('to', '')) or date_from
    if date_to < date_from:
        return Response({'detail': 'تاريخ النهاية يجب أن يكون بعد البداية.'}, status=status.HTTP_400_BAD_REQUEST)

    income = Booking.objects.filter(
        date__range=(date_from, date_to), status='completed'
    ).select_related('service', 'customer')
    expenses = Expense.objects.filter(date__range=(date_from, date_to)).select_related('created_by')

    def total(queryset, method=None):
        if method:
            queryset = queryset.filter(payment_method=method)
        return queryset.aggregate(value=Sum('total_price' if queryset.model is Booking else 'amount'))['value'] or Decimal('0')

    cash_income = total(income, 'cash')
    card_income = total(income, 'card')
    transfer_income = total(income, 'bank_transfer')
    online_income = total(income, 'online')
    cash_expense = total(expenses, 'cash')
    card_expense = total(expenses, 'card')
    transfer_expense = total(expenses, 'bank_transfer')
    online_expense = total(expenses, 'online')
    receipts = cash_income + card_income + transfer_income + online_income
    expense_total = cash_expense + card_expense + transfer_expense + online_expense

    movements = []
    for booking in income:
        movements.append({
            'type': 'income', 'date': booking.date, 'description': f'طلب #{booking.id} - {booking.service.name}',
            'amount': booking.total_price, 'payment_method': booking.payment_method,
        })
    for expense in expenses:
        movements.append({
            'type': 'expense', 'id': expense.id, 'date': expense.date,
            'description': expense.description, 'category': expense.category,
            'amount': expense.amount, 'payment_method': expense.payment_method,
        })
    movements.sort(key=lambda item: (str(item['date']), item.get('id', 0)), reverse=True)

    return Response({
        'from': date_from, 'to': date_to,
        'total_receipts': receipts,
        'total_expenses': expense_total,
        'cash_available': cash_income - cash_expense,
        'net_non_cash': (
            card_income + transfer_income + online_income
        ) - (card_expense + transfer_expense + online_expense),
        'net_total': receipts - expense_total,
        'breakdown': {
            'cash_income': cash_income, 'card_income': card_income,
            'transfer_income': transfer_income, 'online_income': online_income,
            'cash_expenses': cash_expense, 'card_expenses': card_expense,
            'transfer_expenses': transfer_expense, 'online_expenses': online_expense,
        },
        'movements': movements,
    })


@api_view(['GET', 'POST'])
@permission_classes([IsManager])
def manager_expenses(request):
    if request.method == 'GET':
        expenses = Expense.objects.select_related('created_by').order_by('-date', '-id')[:500]
        return Response(ExpenseSerializer(expenses, many=True).data)
    serializer = ExpenseSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save(created_by=request.user)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsManager])
def manager_expense_detail(request, item_id):
    try:
        expense = Expense.objects.get(pk=item_id)
    except Expense.DoesNotExist:
        return Response({'detail': 'المصروف غير موجود.'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'DELETE':
        expense.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = ExpenseSerializer(expense, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)
