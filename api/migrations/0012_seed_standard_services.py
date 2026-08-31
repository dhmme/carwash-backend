from django.db import migrations


def seed_services(apps, schema_editor):
    Service = apps.get_model('api', 'Service')
    services = [
        ('غسيل كامل', 'غسيل داخلي وخارجي للمركبة', 45),
        ('غسيل خارجي', 'غسيل خارجي للمركبة', 30),
        ('غسيل داخلي', 'تنظيف داخلي للمركبة', 25),
    ]
    for name, description, price in services:
        Service.objects.update_or_create(
            name=name,
            defaults={
                'description': description,
                'price': price,
                'is_active': True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [('api', '0011_addon_booking_add_ons')]
    operations = [
        migrations.RunPython(seed_services, migrations.RunPython.noop),
    ]
