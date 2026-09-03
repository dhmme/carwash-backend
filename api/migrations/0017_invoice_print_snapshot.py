import uuid

from django.db import migrations, models


def populate_public_tokens(apps, schema_editor):
    Invoice = apps.get_model('api', 'Invoice')
    for invoice in Invoice.objects.filter(public_token__isnull=True).iterator():
        invoice.public_token = uuid.uuid4()
        invoice.save(update_fields=['public_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0016_expense'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='line_items',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='invoice',
            name='total_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='invoice',
            name='public_token',
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_public_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='invoice',
            name='public_token',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
