from datetime import date

from django.core.management.base import BaseCommand

from accounts.models import Opportunity, OppCategory


class Command(BaseCommand):
    help = "Insert a small curated set of opportunities (safe: only uses existing Opportunity fields)."

    def handle(self, *args, **options):
        # Make sure categories exist
        categories = [
            "Computer Science & IT",
            "Business & Management",
            "Finance",
            "Engineering",
            "Cybersecurity",
        ]

        cat_objs = {}
        for name in categories:
            obj, _ = OppCategory.objects.get_or_create(name=name)
            cat_objs[name] = obj

        opportunities_data = [
            {
                "title": "IT Co-op Trainee",
                "company": "AON",
                "location": "Riyadh,Riyadh",
                "category": "Computer Science & IT",
                "major": "Computer Science, IT, Information Systems",
            },
            {
                "title": "Consulting Intern - Jusoor Program",
                "company": "Deloitte",
                "location": "Eastern Province,Al Khobar",
                "category": "Business & Management",
                "major": "Business, Finance, MIS, Engineering",
            },
            {
                "title": "Technology & Events Co-op",
                "company": "Webook",
                "location": "Riyadh,Riyadh",
                "category": "Computer Science & IT",
                "major": "Technology, Marketing, Finance",
            },
            {
                "title": "Digital Transformation Co-op",
                "company": "Digital Government Authority",
                "location": "Riyadh,Riyadh",
                "category": "Computer Science & IT",
                "major": "IT, Computer Science, Business, Law",
            },
            {
                "title": "Co-op Training Program",
                "company": "GOSI",
                "location": "Riyadh,Riyadh",
                "category": "Finance",
                "major": "Finance, Accounting, Business",
            },
        ]

        # Only concrete DB fields (avoid reverse relations that appear in get_fields())
        existing_fields = {f.name for f in Opportunity._meta.concrete_fields}
        self.stdout.write(f"Opportunity concrete fields: {sorted(existing_fields)}")

        created = 0
        skipped = 0

        for data in opportunities_data:
            create_kwargs = {
                "title": data["title"],
                "company": data["company"],
                "location": data["location"],
                "category": cat_objs[data["category"]],
                "major": data["major"],
                "status": "open",
                "deadline": date(2026, 12, 31),
            }

            # Store description only if the Opportunity model has a `description` field
            desc = "Curated opportunity inserted manually for system initialization."
            if "description" in existing_fields:
                create_kwargs["description"] = desc

            # Avoid duplicates by company+title
            exists = Opportunity.objects.filter(company=data["company"], title=data["title"]).exists()
            if exists:
                skipped += 1
                continue

            Opportunity.objects.create(**create_kwargs)
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Inserted {created} opportunities."))
        if skipped:
            self.stdout.write(self.style.WARNING(f"Skipped {skipped} (already existed)."))