import os
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create or update the manager account from environment variables.'

    def handle(self, *args, **options):
        phone = os.getenv('MANAGER_PHONE', '').strip()
        password = os.getenv('MANAGER_PASSWORD', '')
        name = os.getenv('MANAGER_NAME', 'مدير الفرع').strip()
        if not phone or not password:
            self.stdout.write('Manager account skipped: environment variables are not set.')
            return
        user, created = User.objects.get_or_create(username=phone)
        user.first_name = name
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f'Manager account {"created" if created else "updated"}.'))
