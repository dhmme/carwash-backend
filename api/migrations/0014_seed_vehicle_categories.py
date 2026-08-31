from django.db import migrations


def seed_categories(apps, schema_editor):
    Category = apps.get_model('api', 'VehicleCategory')
    for key, name, adjustment in [
        ('sedan', 'سيدان', 0),
        ('small_suv', 'جيب صغير', 10),
        ('family_suv', 'جيب عائلي', 20),
    ]:
        Category.objects.get_or_create(
            key=key,
            defaults={'name': name, 'price_adjustment': adjustment, 'is_active': True},
        )


class Migration(migrations.Migration):
    dependencies = [('api', '0013_vehiclecategory_invoice')]
    operations = [migrations.RunPython(seed_categories, migrations.RunPython.noop)]
