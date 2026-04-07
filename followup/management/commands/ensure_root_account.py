import secrets

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from followup.models import UserProfile


class Command(BaseCommand):
    help = "Ensure that a usable root account exists."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="root")
        parser.add_argument("--password", default="")

    def handle(self, *args, **options):
        username = options["username"]
        password = (options["password"] or "").strip()

        user, created = User.objects.get_or_create(username=username, defaults={"is_active": True})
        user.is_active = True

        generated_password = ""
        if password:
            user.set_password(password)
        elif created:
            generated_password = secrets.token_urlsafe(14)
            user.set_password(generated_password)

        user.save()

        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": UserProfile.ROLE_ROOT},
        )

        if created and generated_password:
            self.stdout.write(self.style.SUCCESS(f"Created root account {username}."))
            self.stdout.write(self.style.WARNING(f"Generated password: {generated_password}"))
        elif created:
            self.stdout.write(self.style.SUCCESS(f"Created root account {username}."))
        elif password:
            self.stdout.write(self.style.SUCCESS(f"Updated password for root account {username}."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Verified root account {username}; password unchanged."))
