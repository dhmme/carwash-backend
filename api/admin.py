from django.contrib import admin
from django.utils.html import format_html

from .models import AddOn, Service, Car, Booking


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'price')


@admin.register(AddOn)
class AddOnAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'unit', 'price', 'allows_quantity', 'is_active')
    list_editable = ('price', 'is_active')


@admin.register(Car)
class CarAdmin(admin.ModelAdmin):
    # نخليها بسيطة عشان ما نعتمد على حقول يمكن ما هي موجودة
    list_display = ('id',)   # لو حبيت نضيف حقول حقيقية لاحقاً نعدلها


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'customer',
        'car',
        'service',
        'date',
        'time_slot',
        'maps_link',   # عمود رابط الخريطة
    )

    def maps_link(self, obj):
        if obj.latitude is not None and obj.longitude is not None:
            url = f"https://www.google.com/maps?q={obj.latitude},{obj.longitude}"
            return format_html(
                '<a href="{}" target="_blank">عرض على الخريطة</a>',
                url,
            )
        return '-'

    maps_link.short_description = 'الموقع'
