from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0006_booking_customer_name_booking_customer_phone_and_more'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='booking',
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name='booking',
            name='car',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to='api.car',
            ),
        ),
        migrations.AlterField(
            model_name='booking',
            name='payment_method',
            field=models.CharField(
                choices=[
                    ('cash', 'كاش'),
                    ('card', 'شبكة'),
                    ('bank_transfer', 'تحويل بنكي'),
                ],
                default='cash',
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name='booking',
            constraint=models.UniqueConstraint(
                condition=models.Q(('status', 'canceled'), _negated=True),
                fields=('date', 'time_slot'),
                name='unique_active_booking_slot',
            ),
        ),
    ]
