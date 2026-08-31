from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from rest_framework import serializers
from .models import AddOn, Service, Car, Booking, Location, VehicleCategory, Invoice


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = '__all__'


class AddOnSerializer(serializers.ModelSerializer):
    class Meta:
        model = AddOn
        fields = ['id', 'name', 'unit', 'price', 'allows_quantity', 'is_active']


class VehicleCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleCategory
        fields = ['id', 'key', 'name', 'price_adjustment', 'is_active']


class CarSerializer(serializers.ModelSerializer):
    class Meta:
        model = Car
        fields = [
            'id', 'category', 'vehicle_name', 'vehicle_type', 'size',
            'brand', 'model', 'color', 'plate_number', 'image_data',
        ]
        extra_kwargs = {
            'brand': {'required': False},
            'model': {'required': False},
            'vehicle_type': {'required': False},
            'size': {'required': False},
        }

    def validate(self, attrs):
        category = attrs.get('category', 'sedan')
        attrs['size'] = 'big' if category == 'family_suv' else 'small'
        category_record = VehicleCategory.objects.filter(key=category, is_active=True).first()
        if not category_record:
            raise serializers.ValidationError({'category': 'فئة المركبة غير متاحة.'})
        attrs['vehicle_type'] = category_record.name
        vehicle_name = attrs.get('vehicle_name', 'مركبة').strip()
        image_data = attrs.get('image_data', '')
        if len(image_data) > 2_500_000:
            raise serializers.ValidationError({
                'image_data': 'حجم الصورة كبير، اختر صورة أصغر.'
            })
        attrs['brand'] = vehicle_name
        attrs['model'] = ''
        return attrs


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ['id', 'name', 'address_text', 'latitude', 'longitude']


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
            'add_ons',
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
        requested_add_ons = validated_data.pop('add_ons', [])
        car_size = validated_data.get('car_size')
        total_price = service.price
        car = validated_data.get('car')
        category_key = car.category if car else None
        category = VehicleCategory.objects.filter(key=category_key, is_active=True).first()
        if category:
            total_price += category.price_adjustment
        elif service.name.strip() == 'غسيل كامل' and car_size == 'big':
            total_price += 10

        add_on_snapshot = []
        for item in requested_add_ons:
            try:
                add_on = AddOn.objects.get(pk=item.get('id'), is_active=True)
            except (AddOn.DoesNotExist, TypeError, ValueError):
                continue
            quantity = int(item.get('quantity', 1)) if add_on.allows_quantity else 1
            quantity = max(1, min(quantity, 20))
            subtotal = add_on.price * quantity
            total_price += subtotal
            add_on_snapshot.append({
                'id': add_on.id,
                'name': add_on.name,
                'unit': add_on.unit,
                'quantity': quantity,
                'unit_price': str(add_on.price),
                'subtotal': str(subtotal),
            })

        try:
            with transaction.atomic():
                booking = Booking.objects.create(
                    customer=request.user,
                    status='accepted',
                    total_price=total_price,
                    add_ons=add_on_snapshot,
                    **validated_data,
                )
                Invoice.objects.create(booking=booking)
                return booking
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
    customer_name = serializers.SerializerMethodField()
    customer_phone = serializers.SerializerMethodField()
    car_name = serializers.CharField(source='car.vehicle_name', read_only=True)
    car_category = serializers.CharField(source='car.category', read_only=True)
    car_color = serializers.CharField(source='car.color', read_only=True)
    plate_number = serializers.CharField(source='car.plate_number', read_only=True)
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
            'car_name',
            'car_category',
            'car_color',
            'plate_number',
            'service_name',
            'address_text',
            'latitude',
            'longitude',
            'maps_url',
            'total_price',
            'payment_method',
            'add_ons',
        ]

    def get_customer_name(self, obj):
        return obj.customer_name or obj.customer.first_name or obj.customer.username

    def get_customer_phone(self, obj):
        return obj.customer_phone or obj.customer.username

    def get_maps_url(self, obj):
        if obj.latitude is not None and obj.longitude is not None:
            return f"https://www.google.com/maps?q={obj.latitude},{obj.longitude}"
        return None


class ManagerBookingSerializer(WorkerBookingSerializer):
    created_at = serializers.DateTimeField(read_only=True)
    invoice_number = serializers.CharField(source='invoice.number', read_only=True, default='')

    class Meta(WorkerBookingSerializer.Meta):
        fields = WorkerBookingSerializer.Meta.fields + ['created_at', 'invoice_number']


class InvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.SerializerMethodField()
    customer_phone = serializers.SerializerMethodField()
    service_name = serializers.CharField(source='booking.service.name', read_only=True)
    date = serializers.DateField(source='booking.date', read_only=True)
    total = serializers.DecimalField(source='booking.total_price', max_digits=10, decimal_places=2, read_only=True)
    payment_method = serializers.CharField(source='booking.payment_method', read_only=True)
    add_ons = serializers.JSONField(source='booking.add_ons', read_only=True)

    class Meta:
        model = Invoice
        fields = ['id', 'number', 'booking', 'issued_at', 'customer_name', 'customer_phone',
                  'service_name', 'date', 'total', 'payment_method', 'add_ons', 'notes']

    def get_customer_name(self, obj):
        b = obj.booking
        return b.customer_name or b.customer.first_name or b.customer.username

    def get_customer_phone(self, obj):
        return obj.booking.customer_phone or obj.booking.customer.username


class ManagerStaffSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=8)

    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'is_active', 'password']

    def create(self, validated_data):
        password = validated_data.pop('password')
        return User.objects.create_user(password=password, is_staff=True, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.is_staff = True
        if password:
            instance.set_password(password)
        instance.save()
        return instance
