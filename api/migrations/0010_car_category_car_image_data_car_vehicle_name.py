from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('api', '0009_add_internal_wash_service')]
    operations = [
        migrations.AddField(
            model_name='car', name='category',
            field=models.CharField(
                choices=[('sedan', 'سيدان'), ('small_suv', 'جيب صغير'), ('family_suv', 'جيب عائلي')],
                default='sedan', max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='car', name='vehicle_name',
            field=models.CharField(default='مركبة', max_length=100),
        ),
        migrations.AddField(
            model_name='car', name='image_data',
            field=models.TextField(blank=True, default=''),
        ),
    ]
