from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import uuid
from decimal import Decimal

class PaymentMethod(models.Model):
    code = models.CharField(max_length=50, unique=True, blank=True)
    name = models.CharField(max_length=100)
    instructions = models.CharField(max_length=255, blank=True)
    requires_gateway = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    ordering = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['ordering', 'id']

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = f'method_{uuid.uuid4().hex[:12]}'
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class ServiceGroup(models.Model):
    key = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    ordering = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['ordering', 'id']

    def __str__(self):
        return self.name


class Service(models.Model):
    group = models.ForeignKey(ServiceGroup, on_delete=models.PROTECT, related_name='services', null=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=30, default='خدمة')
    allows_quantity = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class AddOn(models.Model):
    name = models.CharField(max_length=100)
    unit = models.CharField(max_length=30, default='خدمة')
    price = models.DecimalField(max_digits=10, decimal_places=2)
    allows_quantity = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class VehicleCategory(models.Model):
    key = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    price_adjustment = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class PromoCode(models.Model):
    code = models.CharField(max_length=40, unique=True)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class PackagePlan(models.Model):
    name = models.CharField(max_length=100)
    washes_count = models.PositiveSmallIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    included_service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name='package_plans')
    validity_days = models.PositiveSmallIntegerField(default=60)
    is_active = models.BooleanField(default=True)
    ordering = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['ordering', 'id']

    def __str__(self):
        return self.name


class CustomerPackage(models.Model):
    STATUS_CHOICES = [('pending', 'بانتظار الاعتماد'), ('active', 'نشطة'), ('rejected', 'مرفوضة'), ('expired', 'منتهية')]
    customer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='wash_packages')
    plan = models.ForeignKey(PackagePlan, on_delete=models.PROTECT, related_name='purchases')
    payment_method = models.CharField(max_length=50, default='bank_transfer')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    remaining_washes = models.PositiveSmallIntegerField(default=0)
    purchased_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-id']

    @property
    def usable(self):
        return self.status == 'active' and self.remaining_washes > 0 and self.expires_at and self.expires_at > timezone.now()


class BookingTimeSlot(models.Model):
    group = models.ForeignKey(ServiceGroup, on_delete=models.CASCADE, related_name='time_slots', null=True)
    label = models.CharField(max_length=50)
    start_time = models.TimeField()
    day_offset = models.PositiveSmallIntegerField(
        default=0,
        choices=[(0, 'نفس اليوم'), (1, 'نهاية اليوم بعد منتصف الليل')],
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['day_offset', 'start_time', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'start_time', 'day_offset'],
                name='unique_group_booking_slot_clock_time',
            ),
            models.UniqueConstraint(fields=['group', 'label'], name='unique_group_booking_slot_label'),
        ]

    def __str__(self):
        return self.label


class Car(models.Model):
    SIZE_CHOICES = [('small', 'صغيرة'), ('big', 'كبيرة')]
    CATEGORY_CHOICES = [
        ('sedan', 'سيدان'),
        ('small_suv', 'جيب صغير'),
        ('family_suv', 'جيب عائلي'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    category = models.CharField(
        max_length=20,
        default='sedan',
    )
    vehicle_name = models.CharField(max_length=100, default='مركبة')
    vehicle_type = models.CharField(max_length=100, default='سيارة')
    size = models.CharField(max_length=10, choices=SIZE_CHOICES, default='small')
    brand = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    color = models.CharField(max_length=50)
    plate_number = models.CharField(max_length=20)
    image_data = models.TextField(blank=True, default='')

    def __str__(self):
        return f"{self.vehicle_name} ({self.plate_number})"


class Location(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    address_text = models.CharField(max_length=255)
    latitude = models.FloatField()
    longitude = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.user.username}"


class Booking(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('on_the_way', 'On the way'),
        ('in_progress', 'In progress'),
        ('completed', 'Completed'),
        ('canceled', 'Canceled'),
    ]

    CAR_SIZE_CHOICES = [
        ('small', 'سيارة صغيرة'),
        ('big', 'سيارة كبيرة'),
    ]

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['service_group', 'date', 'time_slot'],
                condition=~models.Q(status='canceled'),
                name='unique_active_group_booking_slot',
            ),
        ]

    # العلاقات القديمة (نخليها عادي)
    customer = models.ForeignKey(User, on_delete=models.CASCADE)
    car = models.ForeignKey(
        Car,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    service_group = models.ForeignKey(ServiceGroup, on_delete=models.PROTECT, null=True)
    service = models.ForeignKey(Service, on_delete=models.PROTECT, null=True, blank=True)
    service_items = models.JSONField(default=list, blank=True)

    # معلومات العميل اللي يعبّيها من التطبيق
    customer_name = models.CharField(max_length=100, blank=True, null=True)
    customer_phone = models.CharField(max_length=20, blank=True, null=True)
    car_size = models.CharField(
        max_length=10,
        choices=CAR_SIZE_CHOICES,
        blank=True,
        null=True,
    )

    address_text = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    date = models.DateField()
    time_slot = models.CharField(max_length=50)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payment_method = models.CharField(
        max_length=50,
        default='cash',
    )
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    promo_code = models.CharField(max_length=40, blank=True, default='')
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    add_ons = models.JSONField(default=list, blank=True)
    customer_package = models.ForeignKey(CustomerPackage, on_delete=models.PROTECT, null=True, blank=True, related_name='bookings')
    package_wash_used = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Booking #{self.id} - {self.customer_name or self.customer.username}"

    def save(self, *args, **kwargs):
        if self.service_group_id is None and self.service_id:
            self.service_group_id = self.service.group_id
        return super().save(*args, **kwargs)

    def maps_url(self):
        if self.latitude is not None and self.longitude is not None:
            return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"
        return None


class PaymentTransaction(models.Model):
    STATUS_CHOICES = [
        ('pending', 'بانتظار الدفع'),
        ('paid', 'مدفوع'),
        ('failed', 'فشل الدفع'),
        ('expired', 'انتهت مهلة الدفع'),
    ]

    booking = models.OneToOneField(
        Booking,
        on_delete=models.CASCADE,
        related_name='payment',
    )
    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    provider = models.CharField(max_length=30, default='moyasar')
    provider_payment_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='SAR')
    expires_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    provider_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Payment #{self.pk} - Booking #{self.booking_id} - {self.status}'


class Invoice(models.Model):
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='invoice')
    number = models.CharField(max_length=30, unique=True, blank=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    notes = models.CharField(max_length=255, blank=True)
    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    line_items = models.JSONField(default=list, blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        if not self.number:
            super().save(*args, **kwargs)
            self.number = f'INV-{self.issued_at:%Y%m%d}-{self.pk:05d}'
            return super().save(update_fields=['number'])
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.number or f'Invoice {self.pk}'

    def ensure_snapshot(self):
        if self.line_items and self.total_amount:
            return
        add_ons = self.booking.add_ons or []
        add_on_total = sum(
            (Decimal(str(item.get('subtotal', 0))) for item in add_ons),
            Decimal('0'),
        )
        service_total = self.booking.total_price + self.booking.discount_amount - add_on_total
        if self.booking.service_items:
            self.line_items = [*self.booking.service_items, *add_ons]
        else:
            self.line_items = [{
                'name': (
                    f'{self.booking.service.name} (من الباقة)'
                    if self.booking.package_wash_used and self.booking.service
                    else self.booking.service.name
                    if self.booking.service
                    else self.booking.service_group.name
                ),
                'quantity': 1,
                'unit_price': str(service_total),
                'subtotal': str(service_total),
            }, *add_ons]
        if self.booking.discount_amount > 0:
            self.line_items.append({
                'name': f'خصم ({self.booking.promo_code})',
                'quantity': 1,
                'unit_price': str(-self.booking.discount_amount),
                'subtotal': str(-self.booking.discount_amount),
            })
        self.total_amount = self.booking.total_price
        self.save(update_fields=['line_items', 'total_amount'])


class Expense(models.Model):
    date = models.DateField()
    description = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True, default='مصروف عام')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=50, default='cash')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.description} - {self.amount}'


class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=255)
    status_code = models.PositiveSmallIntegerField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user_id or "anonymous"} {self.method} {self.path}'
