from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status , viewsets
from django.utils import timezone


from .models import Service, Car, Booking
from .serializers import ServiceSerializer, CarSerializer , BookingSerializer , WorkerBookingSerializer


@api_view(['GET'])
def hello_view(request):
    return Response({"message": "Car Wash API is working!"})


# ===== SERVICES =====
@api_view(['GET'])
def service_list(request):
    services = Service.objects.filter(is_active=True)
    serializer = ServiceSerializer(services, many=True)
    return Response(serializer.data)


# ===== CARS =====
@api_view(['GET', 'POST'])
def car_list_create(request):
    """
    GET: list cars (optionally filter by user_id ?user_id=1)
    POST: create car
    """
    if request.method == 'GET':
        user_id = request.GET.get('user_id')
        if user_id:
            cars = Car.objects.filter(user_id=user_id)
        else:
            cars = Car.objects.all()  # later we will secure this
        serializer = CarSerializer(cars, many=True)
        return Response(serializer.data)

    if request.method == 'POST':
        serializer = CarSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ===== BOOKINGS =====
@api_view(['GET', 'POST'])
def booking_list_create(request):
    """
    GET: list bookings (optionally filter by customer_id ?customer_id=1)
    POST: create booking
    """
    if request.method == 'GET':
        customer_id = request.GET.get('customer_id')
        if customer_id:
            bookings = Booking.objects.filter(customer_id=customer_id)
        else:
            bookings = Booking.objects.all()  # later we will secure this
        serializer = BookingSerializer(bookings, many=True)
        return Response(serializer.data)

    if request.method == 'POST':
        serializer = BookingSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class BookingViewSet(viewsets.ModelViewSet):
    queryset = Booking.objects.all().order_by('-id')
    serializer_class = BookingSerializer    


@api_view(['GET'])
def booked_slots(request):
    """
    يرجّع الأوقات المحجوزة في يوم معيّن.
    مثال: /api/booked-slots/?date=2025-12-02
    """
    date = request.GET.get('date')  # صيغة YYYY-MM-DD

    if not date:
        return Response({'error': 'date query parameter is required'}, status=400)

    slots_qs = Booking.objects.filter(date=date).values_list('time_slot', flat=True)
    return Response({'booked': list(slots_qs)})


@api_view(['GET'])
def worker_bookings(request):
    """
    واجهة العمالة: ترجع حجوزات اليوم (أو تاريخ معين)
    /api/worker/bookings/        -> اليوم
    /api/worker/bookings/?date=2025-12-02
    """
    date_str = request.GET.get('date')
    if date_str:
        date = date_str  # DateField يقبل string بصيغة YYYY-MM-DD
    else:
        date = timezone.localdate()

    qs = Booking.objects.filter(date=date).order_by('time_slot')
    serializer = WorkerBookingSerializer(qs, many=True)
    return Response(serializer.data)
