from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from rest_framework import serializers
from .models import Service, Car, Booking


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = '__all__'


class CarSerializer(serializers.ModelSerializer):
    class Meta:
        model = Car
        fields = ['id', 'brand', 'model', 'color', 'plate_number']


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'email']


class RegisterSerializer(serializers.ModelSerializer):
    name = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ['username', 'name', 'email', 'password']

    def create(self, validated_data):
        name = validated_data.pop('name')
        return User.objects.create_user(first_name=name, **validated_data)


class BookingSerializer(serializers.ModelSerializer):
    customer = UserSerializer(read_only=True)
    service_name = serializers.CharField(source='service.name', read_only=True)
    maps_url = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            'id', 'customer', 'car', 'service', 'service_name',
            'customer_name', 'customer_phone', 'car_size', 'address_text',
            'latitude', 'longitude', 'maps_url', 'date', 'time_slot',
            'status', 'payment_method', 'total_price', 'created_at',
        ]
        read_only_fields = ['status', 'total_price', 'created_at']

    def validate(self, attrs):
        date = attrs.get('date')
        time_slot = attrs.get('time_slot')

        if Booking.objects.filter(date=date, time_slot=time_slot).exclude(
            status='canceled'
        ).exists():
            raise serializers.ValidationError({
                'time_slot': 'هذا الوقت محجوز بالفعل، اختر وقتاً آخر.'
            })

        return attrs

    def create(self, validated_data):
        request = self.context['request']
        service = validated_data['service']
        car_size = validated_data.get('car_size')
        total_price = service.price
        if service.name.strip() == 'غسيل كامل' and car_size == 'big':
            total_price += 10

        try:
            with transaction.atomic():
                return Booking.objects.create(
                    customer=request.user,
                    total_price=total_price,
                    **validated_data,
                )
        except IntegrityError as exc:
            raise serializers.ValidationError({
                'time_slot': 'هذا الوقت محجوز بالفعل، اختر وقتاً آخر.'
            }) from exc

    def get_maps_url(self, obj):
        return obj.maps_url()


class BookingStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ['status']


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
