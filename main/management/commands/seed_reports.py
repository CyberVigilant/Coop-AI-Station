import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Opportunity, Report, ReportStatus, ReportType, Student

REPORT_DESCRIPTIONS = {
    "broken_link": [
        "The 'Apply Now' button redirects to a 404 page. Tried multiple times over two days.",
        "The link opens the company's homepage instead of the actual opportunity page.",
        "Clicking the application link gives a connection timeout error.",
    ],
    "wrong_deadline": [
        "The deadline shown is May 2025, but the opportunity appears to still be open on the company website.",
        "The application closed last month. The listing still shows it as open.",
        "The company's official career page shows a different deadline than what's listed here.",
    ],
    "wrong_location": [
        "The listing says Riyadh, but the company's website clearly states this is a remote position.",
        "The location shown is Jeddah, but when I called HR they said it's in Dammam.",
        "The city listed doesn't match the office location shown on the company's LinkedIn page.",
    ],
    "duplicate": [
        "This exact opportunity is already listed under a different title on this platform.",
        "I submitted this same opportunity last week and it was approved. This looks like a duplicate.",
        "This appears to be the same position as the one listed by the same company two posts above.",
    ],
    "scam": [
        "The company name exists, but this specific posting asks for personal documents before the interview. Suspicious.",
        "The contact email uses a Gmail address instead of a company domain. Looks unofficial.",
        "The opportunity description was copied word-for-word from a legitimate posting but points to a different link.",
    ],
    "other": [
        "The description mentions IT roles but this is listed under Finance category.",
        "The opportunity requires graduation, but it is listed as a co-op position.",
        "The salary field shows numbers that seem way too high for a co-op role.",
    ],
}

# (report_type, count)
DISTRIBUTION = [
    ("broken_link", 5),
    ("wrong_deadline", 5),
    ("wrong_location", 4),
    ("duplicate", 4),
    ("scam", 4),
    ("other", 3),
]


class Command(BaseCommand):
    help = "Delete all existing reports and seed 25 realistic ones."

    def handle(self, *args, **options):
        deleted, _ = Report.objects.all().delete()
        self.stdout.write(f"Deleted {deleted} existing report(s).")

        students = list(Student.objects.all())
        opportunities = list(Opportunity.objects.all())

        if not students:
            self.stdout.write(self.style.ERROR("No students found — run seed_all first."))
            return
        if not opportunities:
            self.stdout.write(self.style.ERROR("No opportunities found."))
            return

        created_count = 0
        # Track (student_id, opp_id) pairs to respect unique constraint
        used_pairs = set()

        for report_type, count in DISTRIBUTION:
            for _ in range(count):
                # Try up to 50 times to find an unused (student, opp) pair
                for attempt in range(50):
                    student = random.choice(students)
                    opp = random.choice(opportunities)
                    pair = (student.pk, opp.pk)
                    if pair not in used_pairs:
                        used_pairs.add(pair)
                        break
                else:
                    self.stdout.write(self.style.WARNING(
                        f"Could not find unique pair for {report_type}, skipping one."
                    ))
                    continue

                status = (
                    ReportStatus.PENDING
                    if random.random() < 0.6
                    else ReportStatus.RESOLVED
                )
                created_at = timezone.now() - timedelta(days=random.randint(1, 60))

                Report.objects.create(
                    student=student,
                    opportunity=opp,
                    report_type=report_type,
                    description=random.choice(REPORT_DESCRIPTIONS[report_type]),
                    status=status,
                    created_at=created_at,
                )
                created_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. Created {created_count} report(s)."
        ))
