from django.core.management.base import BaseCommand
from accounts.models import Student


SUFFIXES = ("آل سعود", "لادن")


class Command(BaseCommand):
    help = "Delete students whose full name ends with 'آل سعود' or 'لادن', including their Django User accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be deleted without actually deleting.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        targets = [
            s for s in Student.objects.select_related("user").all()
            if any(s.full_name.strip().endswith(suffix) for suffix in SUFFIXES)
        ]

        if not targets:
            self.stdout.write("No matching students found.")
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN — would delete {len(targets)} student(s):"))
            for s in targets:
                self.stdout.write(f"  • {s.full_name} (username: {s.user.username})")
            return

        deleted_count = 0
        for s in targets:
            name = s.full_name
            username = s.user.username
            user = s.user
            s.delete()
            user.delete()
            deleted_count += 1
            self.stdout.write(self.style.SUCCESS(f"Deleted: {name} ({username})"))

        self.stdout.write(self.style.SUCCESS(f"\nDone. {deleted_count} student(s) deleted."))
