from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('api', '0025_packages')]

    operations = [
        migrations.AlterField(
            model_name='customerpackage',
            name='status',
            field=models.CharField(max_length=20, default='pending', choices=[
                ('pending', 'بانتظار الاعتماد'), ('active', 'نشطة'),
                ('rejected', 'مرفوضة'), ('expired', 'منتهية'),
                ('revoked', 'ملغاة من الإدارة'),
            ]),
        ),
        migrations.AddField(
            model_name='customerpackage',
            name='grant_reference',
            field=models.UUIDField(null=True, blank=True, unique=True, editable=False),
        ),
    ]
