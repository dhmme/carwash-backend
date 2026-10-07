from django.db import migrations, models
import django.db.models.deletion


def seed_packages(apps, schema_editor):
    Service = apps.get_model('api', 'Service')
    PackagePlan = apps.get_model('api', 'PackagePlan')
    service = Service.objects.filter(group__key='car_wash', name__icontains='كامل').first()
    if not service:
        service = Service.objects.filter(group__key='car_wash').first()
    if service:
        PackagePlan.objects.get_or_create(name='باقة 4 غسلات', defaults={'washes_count': 4, 'price': 77, 'included_service': service, 'validity_days': 60, 'ordering': 10})
        PackagePlan.objects.get_or_create(name='باقة 6 غسلات', defaults={'washes_count': 6, 'price': 99, 'included_service': service, 'validity_days': 60, 'ordering': 20})


class Migration(migrations.Migration):
    dependencies = [('api', '0024_promocode_booking_discount')]
    operations = [
        migrations.CreateModel(
            name='PackagePlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('washes_count', models.PositiveSmallIntegerField()),
                ('price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('validity_days', models.PositiveSmallIntegerField(default=60)),
                ('is_active', models.BooleanField(default=True)),
                ('ordering', models.PositiveSmallIntegerField(default=0)),
                ('included_service', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='package_plans', to='api.service')),
            ],
            options={'ordering': ['ordering', 'id']},
        ),
        migrations.CreateModel(
            name='CustomerPackage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('payment_method', models.CharField(default='bank_transfer', max_length=50)),
                ('status', models.CharField(choices=[('pending', 'بانتظار الاعتماد'), ('active', 'نشطة'), ('rejected', 'مرفوضة'), ('expired', 'منتهية')], default='pending', max_length=20)),
                ('remaining_washes', models.PositiveSmallIntegerField(default=0)),
                ('purchased_at', models.DateTimeField(auto_now_add=True)),
                ('activated_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='wash_packages', to='auth.user')),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchases', to='api.packageplan')),
            ],
            options={'ordering': ['-id']},
        ),
        migrations.AddField(model_name='booking', name='customer_package', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='bookings', to='api.customerpackage')),
        migrations.AddField(model_name='booking', name='package_wash_used', field=models.BooleanField(default=False)),
        migrations.RunPython(seed_packages, migrations.RunPython.noop),
    ]
