from django.db import migrations, models


def seed_add_ons(apps, schema_editor):
    AddOn = apps.get_model('api', 'AddOn')
    values = [
        ('غسيل المراتب', 'مرتبة', 10, True),
        ('معطرات', 'حبة', 5, True),
        ('الكاوا', 'خدمة', 15, False),
    ]
    for name, unit, price, allows_quantity in values:
        AddOn.objects.get_or_create(name=name, defaults={
            'unit': unit, 'price': price,
            'allows_quantity': allows_quantity, 'is_active': True,
        })


class Migration(migrations.Migration):
    dependencies = [('api', '0010_car_category_car_image_data_car_vehicle_name')]
    operations = [
        migrations.CreateModel(
            name='AddOn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('unit', models.CharField(default='خدمة', max_length=30)),
                ('price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('allows_quantity', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
            ],
        ),
        migrations.AddField(
            model_name='booking', name='add_ons',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(seed_add_ons, migrations.RunPython.noop),
    ]
