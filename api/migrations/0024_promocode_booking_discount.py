from django.db import migrations, models


def clear_category_adjustments(apps, schema_editor):
    apps.get_model('api', 'VehicleCategory').objects.update(price_adjustment=0)


class Migration(migrations.Migration):
    dependencies = [('api', '0023_paymentmethod')]
    operations = [
        migrations.CreateModel(
            name='PromoCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=40, unique=True)),
                ('discount_amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddField(model_name='booking', name='promo_code', field=models.CharField(blank=True, default='', max_length=40)),
        migrations.AddField(model_name='booking', name='discount_amount', field=models.DecimalField(decimal_places=2, default=0, max_digits=10)),
        migrations.RunPython(clear_category_adjustments, migrations.RunPython.noop),
    ]
