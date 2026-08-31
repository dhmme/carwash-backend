import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create or update the worker account from Render environment variables.'

    def handle(self, *args, **options):
        phone = os.getenv('WORKER_PHONE', '').strip()
        password = os.getenv('WORKER_PASSWORD', '')
        name = os.getenv('WORKER_NAME', 'عامل الغسيل').strip()

        if not phone or not password:
            self.stdout.write('Worker account skipped: environment variables are not set.')
            return

        user, created = User.objects.get_or_create(username=phone)
        user.first_name = name
        user.is_staff = True
        user.is_active = True
        user.set_password(password)
        user.save(update_fields=['first_name', 'is_staff', 'is_active', 'password'])

        action = 'created' if created else 'updated'
        self.stdout.write(self.style.SUCCESS(f'Worker account {action}.'))
