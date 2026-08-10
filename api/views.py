from django.contrib.auth import authenticate
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from .models import Booking, Car, Service
from .serializers import (
    BookingSerializer,
    BookingStatusSerializer,
    CarSerializer,
    RegisterSerializer,
    ServiceSerializer,
    UserSerializer,
    WorkerBookingSerializer,
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
    bookings = Booking.objects.filter(date=date).select_related(
        'service'
    ).order_by('time_slot')
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
