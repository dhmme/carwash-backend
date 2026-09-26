from django.db import migrations, models


def seed_payment_methods(apps, schema_editor):
    PaymentMethod = apps.get_model('api', 'PaymentMethod')
    methods = [
        ('cash', 'كاش', '', False, 10),
        ('card', 'شبكة عند الوصول', '', False, 20),
        ('bank_transfer', 'تحويل بنكي', '', False, 30),
        ('online', 'دفع إلكتروني', 'مدى، Visa أو Mastercard', True, 40),
    ]
    for code, name, instructions, requires_gateway, ordering in methods:
        PaymentMethod.objects.update_or_create(code=code, defaults={
            'name': name,
            'instructions': instructions,
            'requires_gateway': requires_gateway,
            'is_active': True,
            'ordering': ordering,
        })


class Migration(migrations.Migration):
    dependencies = [('api', '0022_furniture_services')]
    operations = [
        migrations.CreateModel(
            name='PaymentMethod',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(blank=True, max_length=50, unique=True)),
                ('name', models.CharField(max_length=100)),
                ('instructions', models.CharField(blank=True, max_length=255)),
                ('requires_gateway', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
                ('ordering', models.PositiveSmallIntegerField(default=0)),
            ],
            options={'ordering': ['ordering', 'id']},
        ),
        migrations.AlterField(
            model_name='booking', name='payment_method',
            field=models.CharField(default='cash', max_length=50),
        ),
        migrations.AlterField(
            model_name='expense', name='payment_method',
            field=models.CharField(default='cash', max_length=50),
        ),
        migrations.RunPython(seed_payment_methods, migrations.RunPython.noop),
    ]
