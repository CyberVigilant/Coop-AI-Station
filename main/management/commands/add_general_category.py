from django.core.management.base import BaseCommand
from accounts.models import OppCategory


class Command(BaseCommand):
    help = "Ensure the 'General' OppCategory exists."

    def handle(self, *args, **options):
        obj, created = OppCategory.objects.get_or_create(name="General")
        if created:
            self.stdout.write(self.style.SUCCESS("Created OppCategory: General"))
        else:
            self.stdout.write("OppCategory 'General' already exists.")
