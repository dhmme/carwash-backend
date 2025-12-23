from rest_framework import serializers
from .models import Service, Car, Booking


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = '__all__'


class CarSerializer(serializers.ModelSerializer):
    class Meta:
        model = Car
        fields = '__all__'


class BookingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = '__all__'

    def validate(self, attrs):
        date = attrs.get('date')
        time_slot = attrs.get('time_slot')

        if Booking.objects.filter(date=date, time_slot=time_slot).exists():
            raise serializers.ValidationError({
                'time_slot': 'هذا الوقت محجوز بالفعل، اختر وقتاً آخر.'
            })

        return attrs


class WorkerBookingSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(read_only=True)
    customer_phone = serializers.CharField(read_only=True)
    car_size = serializers.CharField(read_only=True)

    service_name = serializers.CharField(source='service.name', read_only=True)
    maps_url = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            'id',
            'date',
            'time_slot',
            'status',
            'customer_name',
            'customer_phone',
            'car_size',
            'service_name',
            'latitude',
            'longitude',
            'maps_url',
            'total_price',
            'payment_method',
        ]

    def get_maps_url(self, obj):
        if obj.latitude is not None and obj.longitude is not None:
            return f"https://www.google.com/maps?q={obj.latitude},{obj.longitude}"
        return None
