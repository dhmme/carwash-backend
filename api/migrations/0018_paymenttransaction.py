import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0017_invoice_print_snapshot'),
    ]

    operations = [
        migrations.AlterField(
            model_name='booking',
            name='payment_method',
            field=models.CharField(
                choices=[
                    ('cash', 'كاش'),
                    ('card', 'شبكة'),
                    ('bank_transfer', 'تحويل بنكي'),
                    ('online', 'دفع إلكتروني'),
                ],
                default='cash',
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='PaymentTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('provider', models.CharField(default='moyasar', max_length=30)),
                ('provider_payment_id', models.CharField(blank=True, max_length=100, null=True, unique=True)),
                ('status', models.CharField(choices=[('pending', 'بانتظار الدفع'), ('paid', 'مدفوع'), ('failed', 'فشل الدفع'), ('expired', 'انتهت مهلة الدفع')], default='pending', max_length=20)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('currency', models.CharField(default='SAR', max_length=3)),
                ('expires_at', models.DateTimeField()),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('provider_response', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('booking', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='payment', to='api.booking')),
            ],
        ),
    ]
