from django.db import models
from django.contrib.auth.models import User
import uuid
from decimal import Decimal

class Service(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
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

    PAYMENT_METHOD_CHOICES = [
        ('cash', 'كاش'),
        ('card', 'شبكة'),
        ('bank_transfer', 'تحويل بنكي'),
    ]

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['date', 'time_slot'],
                condition=~models.Q(status='canceled'),
                name='unique_active_booking_slot',
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
    service = models.ForeignKey(Service, on_delete=models.CASCADE)

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
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default='cash',
    )
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    add_ons = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Booking #{self.id} - {self.customer_name or self.customer.username}"

    def maps_url(self):
        if self.latitude is not None and self.longitude is not None:
            return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"
        return None


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
        service_total = self.booking.total_price - add_on_total
        self.line_items = [{
            'name': self.booking.service.name,
            'quantity': 1,
            'unit_price': str(service_total),
            'subtotal': str(service_total),
        }, *add_ons]
        self.total_amount = self.booking.total_price
        self.save(update_fields=['line_items', 'total_amount'])


class Expense(models.Model):
    PAYMENT_METHOD_CHOICES = Booking.PAYMENT_METHOD_CHOICES
    date = models.DateField()
    description = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True, default='مصروف عام')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='cash')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.description} - {self.amount}'
