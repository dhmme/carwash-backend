from django.db import migrations


def add_internal_service(apps, schema_editor):
    Service = apps.get_model('api', 'Service')
    Service.objects.get_or_create(
        name='غسيل داخلي',
        defaults={'description': 'تنظيف داخلي للمركبة', 'price': 25, 'is_active': True},
    )


class Migration(migrations.Migration):
    dependencies = [('api', '0008_car_size_car_vehicle_type_location')]
    operations = [migrations.RunPython(add_internal_service, migrations.RunPython.noop)]
