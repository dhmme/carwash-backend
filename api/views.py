from django.contrib.auth import authenticate
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.contrib.auth.models import User
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.http import FileResponse, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from decimal import Decimal
from pathlib import Path
from io import BytesIO
from openpyxl import Workbook
from django.shortcuts import get_object_or_404, render
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken, TokenError

from .models import (
    AddOn, Booking, Car, Location, Service, VehicleCategory, Invoice, Expense,
    PaymentTransaction, BookingTimeSlot, ServiceGroup, PaymentMethod, PromoCode,
)
from .payment_services import (
    PaymentVerificationError,
    expire_stale_payments,
    moyasar_mode,
    record_moyasar_reference,
    verify_moyasar_transaction,
)
from .permissions import IsManager
from .throttles import LoginRateThrottle, RegisterRateThrottle
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
    ExpenseSerializer, PaymentMethodSerializer, PromoCodeSerializer,
    past_booking_slots,
    BookingTimeSlotSerializer,
    ServiceGroupSerializer,
)


def _cancel_booking(booking, *, manager=False):
    if booking.status == 'canceled':
        return None
    if booking.status == 'completed':
        return 'لا يمكن إلغاء طلب مكتمل.'
    if not manager and booking.status not in ['pending', 'accepted']:
        return 'لا يمكن إلغاء الطلب بعد بدء العامل في تنفيذه.'
    # Accessing a missing reverse one-to-one relation raises
    # RelatedObjectDoesNotExist, even when getattr has a default. Cash/POS
    # bookings normally have no PaymentTransaction, so query explicitly.
    payment = PaymentTransaction.objects.filter(booking=booking).first()
    if payment and payment.status == 'paid':
        return 'الطلب مدفوع إلكترونيًا ويجب معالجة الاسترجاع قبل الإلغاء.'
    booking.status = 'canceled'
    booking.save(update_fields=['status'])
    return None


@api_view(['GET'])
@permission_classes([AllowAny])
def hello_view(request):
    return Response({'message': 'Car Wash API is working!'})


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([RegisterRateThrottle])
def register_view(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.save()
    refresh = RefreshToken.for_user(user)
    return Response(
        {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data,
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def login_view(request):
    username = request.data.get('username', '').strip()
    password = request.data.get('password', '')
    user = authenticate(request, username=username, password=password)
    if user is None:
        return Response(
            {'detail': 'رقم الجوال أو كلمة المرور غير صحيحة.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    refresh = RefreshToken.for_user(user)
    return Response({
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'user': UserSerializer(user).data,
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    refresh_value = request.data.get('refresh', '')
    if refresh_value:
        try:
            RefreshToken(refresh_value).blacklist()
        except TokenError:
            pass
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def password_reset_request(request):
    email = str(request.data.get('email', '')).strip().lower()
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user:
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_url = f"{settings.CUSTOMER_APP_URL}/?reset_uid={uid}&reset_token={token}"
        try:
            send_mail(
                'إعادة تعيين كلمة مرور Code Care',
                f'مرحبًا {user.first_name or user.username}\n\nلإنشاء كلمة مرور جديدة افتح الرابط التالي:\n{reset_url}\n\nإذا لم تطلب ذلك فتجاهل الرسالة.',
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
        except Exception:
            return Response(
                {'detail': 'خدمة البريد غير جاهزة حاليًا. تواصل مع الدعم الفني.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
    return Response({'detail': 'إذا كان البريد مسجلًا فسيصلك رابط إعادة التعيين.'})


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def password_reset_confirm(request):
    try:
        user_id = force_str(urlsafe_base64_decode(str(request.data.get('uid', ''))))
        user = User.objects.get(pk=user_id, is_active=True)
    except Exception:
        return Response({'detail': 'رابط إعادة التعيين غير صالح.'}, status=status.HTTP_400_BAD_REQUEST)
    token = str(request.data.get('token', ''))
    if not default_token_generator.check_token(user, token):
        return Response({'detail': 'انتهت صلاحية الرابط أو تم استخدامه سابقًا.'}, status=status.HTTP_400_BAD_REQUEST)
    password = str(request.data.get('password', ''))
    try:
        validate_password(password, user=user)
    except ValidationError as exc:
        return Response({'detail': list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
    user.set_password(password)
    user.save(update_fields=['password'])
    return Response({'detail': 'تم تغيير كلمة المرور بنجاح.'})


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def customer_email(request):
    if request.method == 'GET':
        return Response({'email': request.user.email})
    email = str(request.data.get('email', '')).strip().lower()
    if not email:
        return Response({'email': ['البريد الإلكتروني مطلوب.']}, status=status.HTTP_400_BAD_REQUEST)
    if User.objects.exclude(pk=request.user.pk).filter(email__iexact=email).exists():
        return Response({'email': ['البريد الإلكتروني مستخدم في حساب آخر.']}, status=status.HTTP_400_BAD_REQUEST)
    request.user.email = email
    request.user.save(update_fields=['email'])
    return Response({'email': request.user.email})


@api_view(['GET'])
@permission_classes([AllowAny])
def service_list(request):
    services = Service.objects.filter(is_active=True, group__is_active=True)
    group = request.GET.get('group')
    if group:
        services = services.filter(group__key=group)
    return Response(ServiceSerializer(services, many=True).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def service_group_list(request):
    return Response(ServiceGroupSerializer(ServiceGroup.objects.all(), many=True).data)


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
def booking_time_slot_list(request):
    slots = BookingTimeSlot.objects.filter(is_active=True, group__is_active=True)
    group = request.GET.get('group')
    if group:
        slots = slots.filter(group__key=group)
    return Response(BookingTimeSlotSerializer(slots, many=True).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def payment_config_view(request):
    mode = moyasar_mode()
    methods = PaymentMethod.objects.filter(is_active=True)
    if mode == 'disabled':
        methods = methods.filter(requires_gateway=False)
    return Response({
        'online_enabled': mode != 'disabled',
        'mode': mode,
        'methods': PaymentMethodSerializer(methods, many=True).data,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def validate_promo_code(request):
    code = str(request.data.get('code', '')).strip().upper()
    try:
        subtotal = max(Decimal(str(request.data.get('subtotal', '0'))), Decimal('0'))
    except Exception:
        subtotal = Decimal('0')
    promo = PromoCode.objects.filter(code__iexact=code, is_active=True).first()
    if promo is None:
        return Response({'detail': 'كود الخصم غير صحيح أو غير مفعّل.'}, status=status.HTTP_400_BAD_REQUEST)
    discount = min(promo.discount_amount, subtotal)
    return Response({'code': promo.code, 'discount_amount': discount, 'total': subtotal - discount})


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
        ).select_related('service', 'service_group', 'customer', 'payment').order_by('-id')
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


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_booking(request, booking_id):
    with transaction.atomic():
        booking = get_object_or_404(
            Booking.objects.select_for_update(),
            pk=booking_id,
            customer=request.user,
        )
        error = _cancel_booking(booking)
    if error:
        return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'id': booking.id, 'status': booking.status})


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
    booking_date = parse_date(date)
    if booking_date is None:
        return Response(
            {'error': 'date query parameter is invalid'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    group = get_object_or_404(ServiceGroup, key=request.GET.get('group', 'car_wash'))
    slots = Booking.objects.filter(service_group=group, date=booking_date).exclude(
        status='canceled'
    ).values_list('time_slot', flat=True)
    return Response({
        'booked': list(slots),
        'unavailable': past_booking_slots(booking_date, group),
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def worker_bookings(request):
    date = request.GET.get('date') or timezone.localdate()
    bookings = Booking.objects.filter(date=date).exclude(status='pending').exclude(
        status__in=['completed', 'canceled']
    ).select_related('service', 'service_group', 'car', 'customer', 'payment').order_by('time_slot')
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
    requested_status = request.data.get('status')
    if not request.user.is_superuser and requested_status != 'completed':
        return Response(
            {'status': ['العامل يستطيع فقط إنهاء طلب الغسيل.']},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if booking.status in ['completed', 'canceled']:
        return Response(
            {'status': ['لا يمكن تعديل طلب منتهٍ أو ملغي.']},
            status=status.HTTP_400_BAD_REQUEST,
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
def manager_service_groups(request):
    return _catalog(request, ServiceGroup, ServiceGroupSerializer)


@api_view(['PATCH'])
@permission_classes([IsManager])
def manager_service_group_detail(request, item_id):
    return _catalog_detail(request, ServiceGroup, ServiceGroupSerializer, item_id)


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


@api_view(['GET', 'POST'])
@permission_classes([IsManager])
def manager_promo_codes(request):
    return _catalog(request, PromoCode, PromoCodeSerializer)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsManager])
def manager_promo_code_detail(request, item_id):
    return _catalog_detail(request, PromoCode, PromoCodeSerializer, item_id)


@api_view(['GET', 'POST'])
@permission_classes([IsManager])
def manager_payment_methods(request):
    return _catalog(request, PaymentMethod, PaymentMethodSerializer)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsManager])
def manager_payment_method_detail(request, item_id):
    try:
        item = PaymentMethod.objects.get(pk=item_id)
    except PaymentMethod.DoesNotExist:
        return Response({'detail': 'طريقة الدفع غير موجودة.'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'DELETE':
        if Booking.objects.filter(payment_method=item.code).exists() or Expense.objects.filter(payment_method=item.code).exists():
            return Response(
                {'detail': 'لا يمكن حذف طريقة مرتبطة بسجلات سابقة. أوقفها بدلًا من ذلك.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = PaymentMethodSerializer(item, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(['GET', 'POST'])
@permission_classes([IsManager])
def manager_time_slots(request):
    if request.method == 'GET':
        slots = BookingTimeSlot.objects.all()
        return Response(BookingTimeSlotSerializer(slots, many=True).data)

    serializer = BookingTimeSlotSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsManager])
def manager_time_slot_detail(request, item_id):
    try:
        item = BookingTimeSlot.objects.get(pk=item_id)
    except BookingTimeSlot.DoesNotExist:
        return Response(
            {'detail': 'الوقت غير موجود.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    if request.method == 'DELETE':
        has_future_bookings = Booking.objects.filter(
            service_group=item.group,
            date__gte=timezone.localdate(),
            time_slot=item.label,
        ).exclude(status='canceled').exists()
        if has_future_bookings:
            return Response(
                {'detail': 'لا يمكن حذف وقت عليه حجوزات حالية أو مستقبلية.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = BookingTimeSlotSerializer(item, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsManager])
def manager_bookings(request):
    bookings = Booking.objects.select_related(
        'service', 'service_group', 'car', 'customer', 'invoice', 'payment'
    ).order_by('-date', '-id')
    status_filter = request.GET.get('status')
    if status_filter:
        bookings = bookings.filter(status=status_filter)
    date_filter = parse_date(request.GET.get('date', ''))
    if date_filter:
        bookings = bookings.filter(date=date_filter)
    return Response(ManagerBookingSerializer(bookings[:500], many=True).data)


@api_view(['POST'])
@permission_classes([IsManager])
def manager_cancel_booking(request, booking_id):
    with transaction.atomic():
        booking = get_object_or_404(
            Booking.objects.select_for_update(),
            pk=booking_id,
        )
        error = _cancel_booking(booking, manager=True)
    if error:
        return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'id': booking.id, 'status': booking.status})


@api_view(['GET'])
@permission_classes([IsManager])
def manager_invoices(request):
    for booking in Booking.objects.exclude(
        status__in=['canceled', 'pending']
    ).filter(invoice__isnull=True):
        Invoice.objects.get_or_create(booking=booking)
    invoices = Invoice.objects.select_related('booking__service', 'booking__service_group', 'booking__customer').order_by('-id')
    for invoice in invoices:
        invoice.ensure_snapshot()
    return Response(InvoiceSerializer(invoices[:500], many=True, context={'request': request}).data)


def _customer_rows():
    customers = User.objects.filter(is_staff=False).annotate(
        booking_count=Count('booking'),
        completed_count=Count('booking', filter=Q(booking__status='completed')),
        total_spent=Sum('booking__total_price', filter=Q(booking__status='completed')),
        last_booking_at=Max('booking__created_at'),
    ).order_by('-date_joined')
    return customers


@api_view(['GET'])
@permission_classes([IsManager])
def manager_customers(request):
    customers = _customer_rows()
    query = request.GET.get('q', '').strip()
    if query:
        customers = customers.filter(
            Q(first_name__icontains=query) |
            Q(username__icontains=query) |
            Q(email__icontains=query)
        )
    data = [{
        'id': user.id,
        'name': user.first_name or user.username,
        'phone': user.username,
        'email': user.email,
        'date_joined': user.date_joined,
        'is_active': user.is_active,
        'booking_count': user.booking_count,
        'completed_count': user.completed_count,
        'total_spent': user.total_spent or 0,
        'last_booking_at': user.last_booking_at,
    } for user in customers[:1000]]
    return Response(data)


@api_view(['GET'])
@permission_classes([IsManager])
def manager_customers_export(request):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'العملاء'
    sheet.sheet_view.rightToLeft = True
    sheet.append([
        'رقم العميل', 'الاسم', 'رقم الجوال', 'البريد الإلكتروني',
        'تاريخ التسجيل', 'عدد الحجوزات', 'الحجوزات المكتملة',
        'إجمالي المبالغ (ر.س)', 'آخر حجز', 'حالة الحساب',
    ])
    for user in _customer_rows():
        sheet.append([
            user.id,
            user.first_name or user.username,
            user.username,
            user.email,
            timezone.localtime(user.date_joined).strftime('%Y-%m-%d %H:%M'),
            user.booking_count,
            user.completed_count,
            float(user.total_spent or 0),
            timezone.localtime(user.last_booking_at).strftime('%Y-%m-%d %H:%M') if user.last_booking_at else '',
            'نشط' if user.is_active else 'موقوف',
        ])
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    widths = [14, 24, 18, 30, 22, 16, 20, 23, 22, 16]
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[chr(64 + index)].width = width
    output = BytesIO()
    workbook.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="code-care-customers.xlsx"'
    response['Cache-Control'] = 'no-store'
    return response


def invoice_print_view(request, token):
    invoice = get_object_or_404(
        Invoice.objects.select_related('booking__service', 'booking__customer'),
        public_token=token,
    )
    invoice.ensure_snapshot()
    booking = invoice.booking
    payment_names = dict(PaymentMethod.objects.values_list('code', 'name'))
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
        'configured': moyasar_mode() != 'disabled',
        'customer_app_url': settings.CUSTOMER_APP_URL,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def moyasar_reference_view(request, token):
    try:
        payment = record_moyasar_reference(token, request.data.get('id', ''))
    except PaymentTransaction.DoesNotExist:
        return Response(
            {'detail': 'عملية الدفع غير موجودة.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    except PaymentVerificationError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'saved': True, 'status': payment.status})


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
