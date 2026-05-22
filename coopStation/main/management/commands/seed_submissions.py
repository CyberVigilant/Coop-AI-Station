from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction, connection
from django.utils import timezone
from faker import Faker

from accounts.models import (
    Student,
    Opportunity,
    OppCategory,
    Submission,
    SubmissionStatus,
    SubValidation,
    ValidationResult,
    Report,
    ReportType,
    ReportStatus,
    Rating,
    Leaderboard,
    LeaderboardEntry,
)

User = get_user_model()
fake = Faker("en_US")

COMPANIES = [
    ("Saudi Aramco", "https://www.aramco.com/en/careers"),
    ("stc", "https://www.stc.com.sa/en/careers"),
    ("SABIC", "https://www.sabic.com/en/careers"),
    ("Mobily", "https://www.mobily.com.sa/careers"),
    ("STC Pay", "https://www.stcpay.com.sa/careers"),
    ("Elm", "https://www.elm.sa/careers"),
    ("Saudi Telecom Company", "https://www.stc.com.sa/careers"),
    ("Riyadh Air", "https://www.riyadhair.com/careers"),
    ("NEOM", "https://www.neom.com/en-us/careers"),
    ("Vision Realisation Program", "https://www.vrp.gov.sa/careers"),
    ("Saudi National Bank", "https://www.snb.com/en/careers"),
    ("Al Rajhi Bank", "https://www.alrajhibank.com.sa/careers"),
    ("Riyad Bank", "https://www.riyadbank.com/careers"),
    ("Saudi Exports", "https://www.saudi-exports.com/careers"),
    ("Thiqah Business Services", "https://www.thiqah.sa/careers"),
]

TITLES = [
    "Software Engineering Co-op",
    "Data Science Internship",
    "Cybersecurity Co-op Trainee",
    "IT Infrastructure Co-op",
    "Business Analysis Co-op",
    "Cloud Engineering Co-op",
    "Mobile Development Internship",
    "AI & Machine Learning Co-op",
    "Network Engineering Co-op",
    "DevOps Co-op Trainee",
    "UX/UI Design Co-op",
    "Digital Marketing Co-op",
    "Finance & Accounting Co-op",
    "Project Management Trainee",
    "Information Systems Co-op",
]

REGIONS_AND_CITIES = {
    "Riyadh": ["Riyadh", "Diriyah", "Al Kharj"],
    "Makkah": ["Jeddah", "Makkah", "Taif"],
    "Eastern Province": ["Dammam", "Al Khobar", "Dhahran"],
    "Madinah": ["Madinah", "Yanbu"],
}

PASS_NOTES = [
    "Looks like a real co-op opportunity with a valid source link and recognizable company.",
    "Legitimate Saudi internship listing with verifiable company and a clear deadline.",
    "Source link is accessible and the company is a known entity in the Saudi market.",
    "All required fields are present, company is real, and no duplicates detected.",
    "Valid co-op submission from a well-known Saudi organization with working link.",
    "Opportunity appears genuine; company name matches known Saudi employer.",
]

FAIL_NOTES = [
    "Submission appears to be a duplicate of an existing listing on the platform.",
    "Missing critical information — no source link and company name could not be verified.",
    "Title and company combination already exists in the system; likely duplicate.",
    "No company name provided and the description contains no verifiable information.",
    "Source link is broken and the listed company does not appear to exist.",
]

UNSURE_NOTES = [
    "Company name could not be verified through standard sources.",
    "Description is vague and source link was not provided.",
    "Unusual company name; could be real but requires manual verification.",
    "Co-op opportunity looks plausible but the deadline seems incorrect.",
    "Company exists but the role title is atypical for a co-op programme.",
]


def _random_past_dt(days: int = 30):
    dt = fake.date_time_between(start_date=f"-{days}d", end_date="now")
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _pick_region_city():
    region = random.choice(list(REGIONS_AND_CITIES.keys()))
    city = random.choice(REGIONS_AND_CITIES[region])
    return region, city


class Command(BaseCommand):
    help = (
        "Seed realistic submissions, SubValidations, Reports, and Ratings "
        "that follow the correct AI validation flow."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help=(
                "Clear Submission, SubValidation, Report, Rating, "
                "LeaderboardEntry, and Leaderboard before seeding."
            ),
        )

    def _truncate(self, model):
        table = connection.ops.quote_name(model._meta.db_table)
        with connection.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;")

    def handle(self, *args, **options):
        if options["reset"]:
            self.stdout.write(self.style.WARNING("Resetting submission-related tables…"))
            for model in [LeaderboardEntry, Leaderboard, Rating, Report, SubValidation, Submission]:
                self._truncate(model)
            self.stdout.write(self.style.SUCCESS("Tables cleared."))

        students = list(Student.objects.all().order_by("?")[:15])
        if len(students) < 15:
            self.stdout.write(self.style.ERROR(
                f"Need at least 15 students, found {len(students)}. "
                "Run seed_all --students 52 first."
            ))
            return

        approved_opps = list(Opportunity.objects.filter(status="open").order_by("?")[:30])
        if not approved_opps:
            self.stdout.write(self.style.ERROR(
                "No open opportunities found. Run import_curated_opps first."
            ))
            return

        cat = OppCategory.objects.order_by("?").first()

        # 6 approved, 5 rejected, 4 pending
        statuses = (
            [SubmissionStatus.APPROVED] * 6
            + [SubmissionStatus.REJECTED] * 5
            + [SubmissionStatus.PENDING] * 4
        )
        random.shuffle(statuses)

        created_subs = 0
        created_validations = 0
        leaderboard_scores: dict[int, int] = {}  # student_id -> approved count

        today = timezone.now().date()
        lb = Leaderboard.objects.filter(generated_at__date=today).first()
        if not lb:
            lb = Leaderboard.objects.create()

        for student, target_status in zip(students, statuses):
            company, source_link = random.choice(COMPANIES)
            title = random.choice(TITLES)
            region, city = _pick_region_city()

            with transaction.atomic():
                opp = Opportunity.objects.create(
                    title=title,
                    company=company,
                    location=f"{region},{city}",
                    deadline=date.today() + timedelta(days=random.randint(30, 120)),
                    source_link=source_link,
                    status="open",
                    category=cat,
                )

                decision_dt = _random_past_dt(30) if target_status != SubmissionStatus.PENDING else None

                sub = Submission.objects.create(
                    opportunity=opp,
                    submitted_by_student=student,
                    status=target_status,
                    decision_at=decision_dt,
                )
                created_subs += 1

                if target_status == SubmissionStatus.APPROVED:
                    confidence = random.randint(80, 95)
                    note = f"AI auto-approved. Confidence: {confidence}%. {random.choice(PASS_NOTES)}"
                    val_result = ValidationResult.PASS
                    opp.status = "open"

                    score = leaderboard_scores.get(student.id, 0) + 1
                    leaderboard_scores[student.id] = score

                elif target_status == SubmissionStatus.REJECTED:
                    confidence = random.randint(85, 95)
                    note = f"AI auto-rejected. Confidence: {confidence}%. {random.choice(FAIL_NOTES)}"
                    val_result = ValidationResult.FAIL
                    opp.status = "closed"

                else:
                    confidence = random.randint(30, 55)
                    note = (
                        f"AI flagged for review. Result: unsure. "
                        f"Confidence: {confidence}%. {random.choice(UNSURE_NOTES)}"
                    )
                    val_result = ValidationResult.PENDING

                opp.save(update_fields=["status"])

                SubValidation.objects.create(
                    submission=sub,
                    result=val_result,
                    note=note,
                    validated_at=decision_dt or timezone.now(),
                )
                created_validations += 1

        # Build leaderboard entries with correct ranks in one pass
        sorted_scores = sorted(leaderboard_scores.items(), key=lambda x: x[1], reverse=True)
        for rank, (student_id, score) in enumerate(sorted_scores, start=1):
            student = Student.objects.get(id=student_id)
            LeaderboardEntry.objects.create(
                leaderboard=lb,
                student=student,
                score=score,
                rank=rank,
            )

        # --- Reports (30) ---
        approved_submission_opps = list(
            Opportunity.objects.filter(
                submissions__status=SubmissionStatus.APPROVED
            ).distinct()
        )
        all_students = list(Student.objects.all())
        report_types = [
            ReportType.BROKEN_LINK,
            ReportType.WRONG_DEADLINE,
            ReportType.DUPLICATE,
        ]
        created_reports = 0
        used_report_pairs: set[tuple] = set()

        attempts = 0
        while created_reports < 30 and attempts < 500:
            attempts += 1
            student = random.choice(all_students)
            opp = random.choice(approved_submission_opps)
            pair = (student.id, opp.id)
            if pair in used_report_pairs:
                continue
            used_report_pairs.add(pair)

            status = ReportStatus.PENDING if created_reports < 20 else ReportStatus.RESOLVED
            try:
                with transaction.atomic():
                    Report.objects.create(
                        student=student,
                        opportunity=opp,
                        report_type=random.choice(report_types),
                        status=status,
                        description="Auto-seeded report for testing purposes.",
                    )
                created_reports += 1
            except Exception:
                continue

        # --- Ratings (50) ---
        created_ratings = 0
        used_rating_pairs: set[tuple] = set()
        attempts = 0

        while created_ratings < 50 and attempts < 500:
            attempts += 1
            student = random.choice(all_students)
            opp = random.choice(approved_submission_opps)
            pair = (student.id, opp.id)
            if pair in used_rating_pairs:
                continue
            used_rating_pairs.add(pair)

            lv = random.randint(3, 5)
            we = random.randint(3, 5)
            ms = random.randint(3, 5)
            oc = random.randint(3, 5)
            overall = round((lv + we + ms + oc) / 4.0, 2)

            try:
                with transaction.atomic():
                    Rating.objects.create(
                        student=student,
                        opportunity=opp,
                        learning_value=lv,
                        work_env=we,
                        mentorship=ms,
                        outcome=oc,
                        overall=overall,
                    )
                created_ratings += 1
            except Exception:
                continue

        # Update avg_rating on opportunities
        for opp in approved_submission_opps:
            ratings = opp.ratings.all()
            if ratings.exists():
                avg = sum(r.overall for r in ratings if r.overall) / ratings.count()
                opp.avg_rating = round(avg, 2)
                opp.save(update_fields=["avg_rating"])

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"✅ {Student.objects.count()} students (existing)"))
        self.stdout.write(self.style.SUCCESS(f"✅ {Opportunity.objects.count()} opportunities total"))
        self.stdout.write(self.style.SUCCESS(
            f"✅ {created_subs} submissions created "
            f"(6 approved, 5 rejected, 4 pending)"
        ))
        self.stdout.write(self.style.SUCCESS(f"✅ {created_validations} SubValidation records created"))
        self.stdout.write(self.style.SUCCESS(f"✅ Leaderboard updated ({len(leaderboard_scores)} entries)"))
        self.stdout.write(self.style.SUCCESS(f"✅ {created_reports} reports seeded"))
        self.stdout.write(self.style.SUCCESS(f"✅ {created_ratings} ratings seeded"))
