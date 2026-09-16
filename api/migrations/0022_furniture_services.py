from datetime import time
from django.db import migrations, models
import django.db.models.deletion


def seed_groups_and_furniture(apps, schema_editor):
    Group = apps.get_model('api', 'ServiceGroup')
    Service = apps.get_model('api', 'Service')
    Slot = apps.get_model('api', 'BookingTimeSlot')
    Booking = apps.get_model('api', 'Booking')

    cars, _ = Group.objects.get_or_create(key='car_wash', defaults={
        'name': 'غسيل السيارات', 'description': 'غسيل المركبات في موقع العميل',
        'is_active': True, 'ordering': 1,
    })
    furniture, _ = Group.objects.get_or_create(key='furniture_wash', defaults={
        'name': 'غسيل الأثاث', 'description': 'غسيل الأثاث في موقع العميل',
        'is_active': True, 'ordering': 2,
    })
    Group.objects.get_or_create(key='yard_wash', defaults={
        'name': 'غسيل الأحواش', 'description': 'غسيل وتنظيف الأحواش',
        'is_active': False, 'ordering': 3,
    })
    Service.objects.filter(group__isnull=True).update(group=cars)
    Slot.objects.filter(group__isnull=True).update(group=cars)
    Booking.objects.filter(service_group__isnull=True).update(service_group=cars)

    for name, price, unit in [
        ('كنب', 50, 'شخص'), ('سجاد', 10, 'متر'), ('جلسة عربية', 40, 'متر'),
    ]:
        Service.objects.get_or_create(group=furniture, name=name, defaults={
            'description': f'{price} ر.س لكل {unit}', 'price': price,
            'unit': unit, 'allows_quantity': True, 'is_active': True,
        })

    for label, start, offset in [
        ('9 صباحاً', time(9), 0), ('10 صباحاً', time(10), 0),
        ('11 صباحاً', time(11), 0), ('4 مساءً', time(16), 0),
        ('5 مساءً', time(17), 0), ('6 مساءً', time(18), 0),
        ('7 مساءً', time(19), 0), ('8 مساءً', time(20), 0),
    ]:
        Slot.objects.get_or_create(group=furniture, label=label, defaults={
            'start_time': start, 'day_offset': offset, 'is_active': True,
        })


class Migration(migrations.Migration):
    dependencies = [('api', '0021_auditlog')]
    operations = [
        migrations.CreateModel(
            name='ServiceGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=40, unique=True)),
                ('name', models.CharField(max_length=100)),
                ('description', models.CharField(blank=True, max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('ordering', models.PositiveSmallIntegerField(default=0)),
            ],
            options={'ordering': ['ordering', 'id']},
        ),
        migrations.RemoveConstraint(model_name='booking', name='unique_active_booking_slot'),
        migrations.RemoveConstraint(model_name='bookingtimeslot', name='unique_booking_slot_clock_time'),
        migrations.AlterField(model_name='bookingtimeslot', name='label', field=models.CharField(max_length=50)),
        migrations.AddField(model_name='service', name='allows_quantity', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='service', name='unit', field=models.CharField(default='خدمة', max_length=30)),
        migrations.AddField(model_name='service', name='group', field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='services', to='api.servicegroup')),
        migrations.AddField(model_name='bookingtimeslot', name='group', field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='time_slots', to='api.servicegroup')),
        migrations.AddField(model_name='booking', name='service_group', field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to='api.servicegroup')),
        migrations.AddField(model_name='booking', name='service_items', field=models.JSONField(blank=True, default=list)),
        migrations.AlterField(model_name='booking', name='service', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='api.service')),
        migrations.RunPython(seed_groups_and_furniture, migrations.RunPython.noop),
        migrations.AddConstraint(model_name='bookingtimeslot', constraint=models.UniqueConstraint(fields=('group', 'start_time', 'day_offset'), name='unique_group_booking_slot_clock_time')),
        migrations.AddConstraint(model_name='bookingtimeslot', constraint=models.UniqueConstraint(fields=('group', 'label'), name='unique_group_booking_slot_label')),
        migrations.AddConstraint(model_name='booking', constraint=models.UniqueConstraint(condition=models.Q(('status', 'canceled'), _negated=True), fields=('service_group', 'date', 'time_slot'), name='unique_active_group_booking_slot')),
    ]
