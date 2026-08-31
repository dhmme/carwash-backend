from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from .models import AddOn, Booking, Car, Location, Service, VehicleCategory, Invoice
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
    if request.method == 'GET':
        bookings = Booking.objects.filter(
            customer=request.user
        ).select_related('service', 'customer').order_by('-id')
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
    bookings = Booking.objects.filter(date=date).exclude(
        status__in=['completed', 'canceled']
    ).select_related('service', 'car', 'customer').order_by('time_slot')
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
        'today_revenue': today_qs.exclude(status='canceled').aggregate(v=Sum('total_price'))['v'] or 0,
        'month_revenue': month_qs.exclude(status='canceled').aggregate(v=Sum('total_price'))['v'] or 0,
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
    bookings = Booking.objects.select_related('service', 'car', 'customer', 'invoice').order_by('-date', '-id')
    status_filter = request.GET.get('status')
    if status_filter:
        bookings = bookings.filter(status=status_filter)
    return Response(ManagerBookingSerializer(bookings[:500], many=True).data)


@api_view(['GET'])
@permission_classes([IsManager])
def manager_invoices(request):
    for booking in Booking.objects.exclude(status='canceled').filter(invoice__isnull=True):
        Invoice.objects.get_or_create(booking=booking)
    invoices = Invoice.objects.select_related('booking__service', 'booking__customer').order_by('-id')
    return Response(InvoiceSerializer(invoices[:500], many=True).data)


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
