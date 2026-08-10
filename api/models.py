from django.db import models
from django.contrib.auth.models import User

class Service(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Car(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    brand = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    color = models.CharField(max_length=50)
    plate_number = models.CharField(max_length=20)

    def __str__(self):
        return f"{self.brand} {self.model} ({self.plate_number})"


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

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Booking #{self.id} - {self.customer_name or self.customer.username}"

    def maps_url(self):
        if self.latitude is not None and self.longitude is not None:
            return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"
        return None
