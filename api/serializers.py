from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
from rest_framework import serializers
from .models import (
    AddOn, Service, Car, Booking, Location, VehicleCategory, Invoice, Expense,
    PaymentTransaction, BookingTimeSlot, ServiceGroup, PaymentMethod,
)
from .payment_services import expire_stale_payments, moyasar_mode


BOOKING_WINDOW_DAYS = 3
def booking_slot_datetime(booking_date, time_slot, service_group=None):
    slot = BookingTimeSlot.objects.filter(label=time_slot, group=service_group, is_active=True).first()
    if slot is None:
        return None
    booking_date += timedelta(days=slot.day_offset)
    naive_value = datetime.combine(booking_date, slot.start_time)
    return timezone.make_aware(naive_value, timezone.get_current_timezone())


def is_booking_slot_past(booking_date, time_slot, service_group=None, now=None):
    slot_value = booking_slot_datetime(booking_date, time_slot, service_group)
    if slot_value is None:
        return False
    current_value = now or timezone.localtime()
    return slot_value <= current_value


def past_booking_slots(booking_date, service_group=None, now=None):
    current_value = now or timezone.localtime()
    return [
        slot.label for slot in BookingTimeSlot.objects.filter(group=service_group, is_active=True)
        if is_booking_slot_past(booking_date, slot.label, service_group, current_value)
    ]


class ServiceGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceGroup
        fields = ['id', 'key', 'name', 'description', 'is_active', 'ordering']


class ServiceSerializer(serializers.ModelSerializer):
    group_key = serializers.CharField(source='group.key', read_only=True)
    group_name = serializers.CharField(source='group.name', read_only=True)
    class Meta:
        model = Service
        fields = ['id', 'group', 'group_key', 'group_name', 'name', 'description', 'price', 'unit', 'allows_quantity', 'is_active']

    def validate(self, attrs):
        if not attrs.get('group') and not self.instance:
            attrs['group'] = ServiceGroup.objects.get(key='car_wash')
        return attrs


class AddOnSerializer(serializers.ModelSerializer):
    class Meta:
        model = AddOn
        fields = ['id', 'name', 'unit', 'price', 'allows_quantity', 'is_active']


class VehicleCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleCategory
        fields = ['id', 'key', 'name', 'price_adjustment', 'is_active']


class BookingTimeSlotSerializer(serializers.ModelSerializer):
    group_key = serializers.CharField(source='group.key', read_only=True)
    class Meta:
        model = BookingTimeSlot
        fields = ['id', 'group', 'group_key', 'label', 'start_time', 'day_offset', 'is_active']

    def validate_label(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('اكتب اسم الوقت.')
        return value

    def validate(self, attrs):
        instance = self.instance
        if not attrs.get('group') and not instance:
            attrs['group'] = ServiceGroup.objects.get(key='car_wash')
        if instance:
            protected_fields = ('label', 'start_time', 'day_offset')
            changed = any(
                field in attrs and attrs[field] != getattr(instance, field)
                for field in protected_fields
            )
            if changed and Booking.objects.filter(
                service_group=instance.group,
                date__gte=timezone.localdate(),
                time_slot=instance.label,
            ).exclude(status='canceled').exists():
                raise serializers.ValidationError({
                    'detail': 'لا يمكن تغيير وقت عليه حجوزات حالية أو مستقبلية.'
                })
        return attrs


class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = [
            'id', 'code', 'name', 'instructions', 'requires_gateway',
            'is_active', 'ordering',
        ]
        read_only_fields = ['code', 'requires_gateway']


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
    address_text = serializers.CharField(
        required=False,
        allow_blank=True,
        default='',
    )

    class Meta:
        model = Location
        fields = ['id', 'name', 'address_text', 'latitude', 'longitude']

    def validate(self, attrs):
        name = (attrs.get('name') or '').strip()
        if not name:
            raise serializers.ValidationError({'name': 'اسم الموقع مطلوب.'})
        attrs['name'] = name
        # The map coordinates are the actual service address. Keep a readable
        # fallback for existing booking/invoice screens without asking the
        # customer to type the same information twice.
        if not (attrs.get('address_text') or '').strip():
            attrs['address_text'] = name
        return attrs

    def validate_latitude(self, value):
        if not -90 <= value <= 90:
            raise serializers.ValidationError('خط العرض غير صالح.')
        return value

    def validate_longitude(self, value):
        if not -180 <= value <= 180:
            raise serializers.ValidationError('خط الطول غير صالح.')
        return value


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

    def validate_username(self, value):
        value = value.strip()
        if len(value) != 10 or not value.startswith('05') or not value.isdigit():
            raise serializers.ValidationError('أدخل رقم جوال سعودي صحيح يبدأ بـ 05.')
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        name = validated_data.pop('name')
        return User.objects.create_user(first_name=name, **validated_data)


class BookingSerializer(serializers.ModelSerializer):
    customer = UserSerializer(read_only=True)
    service_name = serializers.SerializerMethodField()
    maps_url = serializers.SerializerMethodField()
    invoice_url = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    payment_checkout_url = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            'id', 'customer', 'car', 'service_group', 'service', 'service_name', 'service_items',
            'customer_name', 'customer_phone', 'car_size', 'address_text',
            'latitude', 'longitude', 'maps_url', 'date', 'time_slot',
            'status', 'payment_method', 'total_price', 'created_at',
            'add_ons',
            'invoice_url', 'payment_status', 'payment_checkout_url',
        ]
        read_only_fields = ['status', 'total_price', 'created_at']

    def validate(self, attrs):
        expire_stale_payments()
        booking_date = attrs.get('date')
        time_slot = attrs.get('time_slot')
        request = self.context['request']
        group = attrs.get('service_group')
        if not group:
            group = ServiceGroup.objects.get(key='car_wash')
            attrs['service_group'] = group
        if not group or not group.is_active:
            raise serializers.ValidationError({'service_group': 'نوع الخدمة غير متاح حاليًا.'})

        now = timezone.localtime()
        today = now.date()
        if booking_date < today:
            raise serializers.ValidationError({
                'date': 'لا يمكن الحجز في تاريخ سابق.'
            })
        if booking_date > today + timedelta(days=BOOKING_WINDOW_DAYS):
            raise serializers.ValidationError({
                'date': 'الحجز متاح لليوم الحالي والثلاثة أيام القادمة فقط.'
            })
        if not BookingTimeSlot.objects.filter(
            group=group,
            label=time_slot,
            is_active=True,
        ).exists():
            raise serializers.ValidationError({
                'time_slot': 'وقت الحجز غير متاح.'
            })
        if is_booking_slot_past(booking_date, time_slot, group, now):
            raise serializers.ValidationError({
                'time_slot': 'هذا الوقت انتهى، اختر وقتاً قادماً.'
            })

        car = attrs.get('car')
        if car and car.user_id != request.user.id:
            raise serializers.ValidationError({
                'car': 'المركبة المختارة لا تخص هذا الحساب.'
            })

        service = attrs.get('service')
        if service and (not service.is_active or service.group_id != group.id):
            raise serializers.ValidationError({
                'service': 'الخدمة المختارة غير متاحة حاليًا.'
            })

        payment_code = attrs.get('payment_method', 'cash')
        payment_method = PaymentMethod.objects.filter(
            code=payment_code,
            is_active=True,
        ).first()
        if payment_method is None:
            raise serializers.ValidationError({
                'payment_method': 'طريقة الدفع غير متاحة حاليًا.'
            })
        if payment_method.requires_gateway and moyasar_mode() == 'disabled':
            raise serializers.ValidationError({
                'payment_method': 'الدفع الإلكتروني غير مفعّل حاليًا.'
            })

        if group.key == 'car_wash' and not service:
            raise serializers.ValidationError({'service': 'اختر خدمة الغسيل.'})
        if group.key == 'furniture_wash' and not attrs.get('service_items'):
            raise serializers.ValidationError({'service_items': 'اختر خدمة أثاث واحدة على الأقل.'})

        if Booking.objects.filter(service_group=group, date=booking_date, time_slot=time_slot).exclude(
            status='canceled'
        ).exists():
            raise serializers.ValidationError({
                'time_slot': 'هذا الوقت محجوز بالفعل، اختر وقتاً آخر.'
            })

        return attrs

    def create(self, validated_data):
        request = self.context['request']
        group = validated_data['service_group']
        service = validated_data.get('service')
        requested_items = validated_data.pop('service_items', [])
        requested_add_ons = validated_data.pop('add_ons', [])
        car_size = validated_data.get('car_size')
        total_price = service.price if service else Decimal('0')
        item_snapshot = []
        if group.key == 'furniture_wash':
            total_price = Decimal('0')
            seen = set()
            for item in requested_items:
                try:
                    selected = Service.objects.get(pk=item.get('id'), group=group, is_active=True)
                    quantity = max(1, min(int(item.get('quantity', 1)), 100))
                except (Service.DoesNotExist, TypeError, ValueError, AttributeError):
                    continue
                if selected.id in seen:
                    continue
                seen.add(selected.id)
                subtotal = selected.price * quantity
                total_price += subtotal
                item_snapshot.append({'id': selected.id, 'name': selected.name, 'unit': selected.unit,
                    'quantity': quantity, 'unit_price': str(selected.price), 'subtotal': str(subtotal)})
            if not item_snapshot:
                raise serializers.ValidationError({'service_items': 'الخدمات المحددة غير متاحة.'})
        car = validated_data.get('car')
        category_key = car.category if car else None
        category = VehicleCategory.objects.filter(key=category_key, is_active=True).first()
        if category:
            total_price += category.price_adjustment
        elif service and service.name.strip() == 'غسيل كامل' and car_size == 'big':
            total_price += 10

        add_on_snapshot = []
        seen_add_on_ids = set()
        for item in requested_add_ons:
            if not isinstance(item, dict):
                continue
            add_on_id = item.get('id')
            if add_on_id in seen_add_on_ids:
                continue
            try:
                add_on = AddOn.objects.get(pk=add_on_id, is_active=True)
            except (AddOn.DoesNotExist, TypeError, ValueError):
                continue
            seen_add_on_ids.add(add_on_id)
            try:
                quantity = int(item.get('quantity', 1)) if add_on.allows_quantity else 1
            except (TypeError, ValueError):
                quantity = 1
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
                is_online = validated_data.get('payment_method') == 'online'
                booking = Booking.objects.create(
                    customer=request.user,
                    status='pending' if is_online else 'accepted',
                    total_price=total_price,
                    add_ons=add_on_snapshot,
                    service_items=item_snapshot,
                    **validated_data,
                )
                if is_online:
                    PaymentTransaction.objects.create(
                        booking=booking,
                        amount=total_price,
                        expires_at=timezone.now() + timedelta(minutes=15),
                    )
                else:
                    invoice = Invoice.objects.create(booking=booking)
                    invoice.ensure_snapshot()
                return booking
        except IntegrityError as exc:
            raise serializers.ValidationError({
                'time_slot': 'هذا الوقت محجوز بالفعل، اختر وقتاً آخر.'
            }) from exc

    def get_service_name(self, obj):
        if obj.service_items:
            return '، '.join(item.get('name', '') for item in obj.service_items)
        return obj.service.name if obj.service else obj.service_group.name

    def get_maps_url(self, obj):
        return obj.maps_url()

    def get_invoice_url(self, obj):
        if obj.payment_method == 'online':
            payment = getattr(obj, 'payment', None)
            if not payment or payment.status != 'paid':
                return ''
        invoice, _ = Invoice.objects.get_or_create(booking=obj)
        invoice.ensure_snapshot()
        request = self.context.get('request')
        path = reverse('invoice-print', kwargs={'token': invoice.public_token})
        return request.build_absolute_uri(path) if request else path

    def get_payment_status(self, obj):
        if obj.payment_method != 'online':
            return 'pay_on_service'
        payment = getattr(obj, 'payment', None)
        return payment.status if payment else 'pending'

    def get_payment_checkout_url(self, obj):
        if obj.payment_method != 'online':
            return ''
        payment = getattr(obj, 'payment', None)
        if not payment or payment.status != 'pending':
            return ''
        request = self.context.get('request')
        path = reverse('moyasar-checkout', kwargs={'token': payment.public_token})
        return request.build_absolute_uri(path) if request else path


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
    service_name = serializers.SerializerMethodField()
    service_group_name = serializers.CharField(source='service_group.name', read_only=True)
    maps_url = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()

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
            'service_group_name', 'service_items',
            'address_text',
            'latitude',
            'longitude',
            'maps_url',
            'total_price',
            'payment_method',
            'payment_status',
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

    def get_payment_status(self, obj):
        if obj.payment_method != 'online':
            return 'pay_on_service'
        payment = getattr(obj, 'payment', None)
        return payment.status if payment else 'pending'

    def get_service_name(self, obj):
        if obj.service_items:
            return '، '.join(f"{x.get('name')} × {x.get('quantity')}" for x in obj.service_items)
        return obj.service.name if obj.service else obj.service_group.name


class ManagerBookingSerializer(WorkerBookingSerializer):
    created_at = serializers.DateTimeField(read_only=True)
    invoice_number = serializers.CharField(source='invoice.number', read_only=True, default='')

    class Meta(WorkerBookingSerializer.Meta):
        fields = WorkerBookingSerializer.Meta.fields + ['created_at', 'invoice_number']


class InvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.SerializerMethodField()
    customer_phone = serializers.SerializerMethodField()
    service_name = serializers.SerializerMethodField()
    date = serializers.DateField(source='booking.date', read_only=True)
    total = serializers.DecimalField(source='booking.total_price', max_digits=10, decimal_places=2, read_only=True)
    payment_method = serializers.CharField(source='booking.payment_method', read_only=True)
    add_ons = serializers.JSONField(source='booking.add_ons', read_only=True)
    line_items = serializers.JSONField(read_only=True)
    print_url = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = ['id', 'number', 'booking', 'issued_at', 'customer_name', 'customer_phone',
                  'service_name', 'date', 'total', 'payment_method', 'add_ons',
                  'line_items', 'notes', 'print_url']

    def get_print_url(self, obj):
        request = self.context.get('request')
        path = reverse('invoice-print', kwargs={'token': obj.public_token})
        return request.build_absolute_uri(path) if request else path

    def get_customer_name(self, obj):
        b = obj.booking
        return b.customer_name or b.customer.first_name or b.customer.username

    def get_customer_phone(self, obj):
        return obj.booking.customer_phone or obj.booking.customer.username

    def get_service_name(self, obj):
        booking = obj.booking
        if booking.service_items:
            return '، '.join(item.get('name', '') for item in booking.service_items)
        return booking.service.name if booking.service else booking.service_group.name


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


class ExpenseSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.first_name', read_only=True, default='')

    class Meta:
        model = Expense
        fields = ['id', 'date', 'description', 'category', 'amount', 'payment_method',
                  'created_by_name', 'created_at']
        read_only_fields = ['created_by_name', 'created_at']
