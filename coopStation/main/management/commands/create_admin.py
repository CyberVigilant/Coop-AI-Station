import getpass
from django.core.management.base import BaseCommand
from django.contrib.auth.hashers import make_password
from accounts.models import Admin


class Command(BaseCommand):
    help = "Create an admin panel user"

    def handle(self, *args, **options):
        email = input("Email: ").strip()
        if Admin.objects.filter(email=email).exists():
            self.stdout.write(self.style.ERROR(f"Admin with email '{email}' already exists."))
            return
        fname = input("First name: ").strip()
        lname = input("Last name: ").strip()
        pw = getpass.getpass("Password: ")
        pw2 = getpass.getpass("Confirm password: ")
        if pw != pw2:
            self.stdout.write(self.style.ERROR("Passwords do not match."))
            return
        Admin.objects.create(
            email=email,
            fname=fname,
            lname=lname,
            name=f"{fname} {lname}",
            password=make_password(pw),
        )
        self.stdout.write(self.style.SUCCESS(f"Admin '{email}' created successfully."))
