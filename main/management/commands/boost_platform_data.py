"""
Management command: boost_platform_data
- Creates ~65 new Saudi student accounts (raises total to ~115-120)
- Reduces pending submissions to 5-8
- Reduces pending reports to 4-6
- Regenerates the leaderboard from scratch
"""
import random
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from accounts.models import (
    GenderChoices,
    Leaderboard,
    LeaderboardEntry,
    Report,
    ReportStatus,
    Student,
    Submission,
    SubmissionStatus,
    SubmitterType,
)

# ---------- Name pools ----------
MALE_NAMES = [
    ("عبدالله الغامدي", "abdullah"),
    ("محمد العتيبي", "mohammed"),
    ("فيصل الدوسري", "faisal"),
    ("خالد الزهراني", "khalid"),
    ("سعد القحطاني", "saad"),
    ("يوسف الشهري", "yousef"),
    ("تركي المطيري", "turki"),
    ("أحمد السبيعي", "ahmed"),
    ("عمر الحربي", "omar"),
    ("راشد العنزي", "rashed"),
    ("بندر الرشيدي", "bandar"),
    ("ماجد الصاعدي", "majed"),
    ("نواف الجهني", "nawaf"),
    ("وليد الشمري", "waleed"),
    ("حمد البقمي", "hamad"),
    ("منصور الرويلي", "mansour"),
    ("ناصر الثبيتي", "nasser"),
    ("طلال البلوي", "talal"),
    ("عادل السلمي", "adel"),
    ("جابر الخالدي", "jaber"),
]

FEMALE_NAMES = [
    ("نورة العمري", "nora"),
    ("ريم الأحمدي", "reem"),
    ("لمى الحسيني", "lama"),
    ("سارة المالكي", "sara"),
    ("هند الغامدي", "hind"),
    ("دانة السعدي", "dana"),
    ("شيماء الشريف", "shaimaa"),
    ("أروى الزهراني", "arwa"),
    ("مريم القرني", "mariam"),
    ("وفاء الحربي", "wafaa"),
    ("أسماء العنزي", "asmaa"),
    ("رنا الدوسري", "rana"),
    ("بسمة الشهري", "basma"),
    ("منى العتيبي", "mona"),
    ("غادة السبيعي", "ghada"),
]

MAJORS = [
    "Marketing", "Economics", "Law", "Civil Engineering",
    "Software Engineering", "Information Technology",
    "Data Science & Analytics", "Business Administration",
    "Accounting", "Artificial Intelligence", "Industrial Engineering",
    "Shariah", "Mechanical Engineering", "Finance",
    "Information Systems (MIS)", "Management",
    "Human Resources", "Cybersecurity",
]

TARGET_NEW_STUDENTS = 65
TARGET_PENDING_SUBMISSIONS = 6
TARGET_PENDING_REPORTS = 5


def _regen_leaderboard():
    Leaderboard.objects.all().delete()
    lb = Leaderboard.objects.create()
    counts = (
        Submission.objects
        .filter(
            status=SubmissionStatus.APPROVED,
            submitted_by_type=SubmitterType.STUDENT,
            submitted_by_student__isnull=False,
        )
        .values("submitted_by_student")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    for rank, row in enumerate(counts, start=1):
        student = Student.objects.get(pk=row["submitted_by_student"])
        LeaderboardEntry.objects.create(
            leaderboard=lb,
            student=student,
            rank=rank,
            score=Decimal(row["total"]),
        )
    return lb.entries.count()


class Command(BaseCommand):
    help = "Boost platform data: more students, fewer pending items, fresh leaderboard."

    def handle(self, *args, **options):
        self._create_students()
        self._trim_pending_submissions()
        self._trim_pending_reports()
        entry_count = _regen_leaderboard()
        self.stdout.write(self.style.SUCCESS(
            f"Leaderboard regenerated with {entry_count} entries."
        ))

    # ------------------------------------------------------------------
    def _create_students(self):
        # Find the highest existing counter in student emails to avoid collisions
        existing_emails = set(User.objects.values_list("email", flat=True))
        counter = 101
        while f"s{counter}@student.ksu.edu.sa" in existing_emails:
            counter += 1

        # Build a shuffled name pool: roughly 55% male, 45% female
        pool = []
        for _ in range(4):          # repeat each pool a few times so we have plenty
            pool.extend(MALE_NAMES)
            pool.extend(FEMALE_NAMES)
        random.shuffle(pool)

        created = 0
        for arabic_name, latin_slug in pool:
            if created >= TARGET_NEW_STUDENTS:
                break

            username = f"student_{latin_slug}_{counter}"
            email = f"s{counter}@student.ksu.edu.sa"

            # Skip if username already taken
            if User.objects.filter(username=username).exists():
                counter += 1
                continue

            gender = (
                GenderChoices.MALE
                if (arabic_name, latin_slug) in MALE_NAMES
                else GenderChoices.FEMALE
            )

            user = User.objects.create_user(
                username=username,
                email=email,
                password="Test1234!",
            )
            Student.objects.create(
                user=user,
                full_name=arabic_name,
                major=random.choice(MAJORS),
                gender=gender,
            )
            counter += 1
            created += 1

        total = Student.objects.count()
        self.stdout.write(
            self.style.SUCCESS(f"Created {created} new students. Total students: {total}.")
        )

    # ------------------------------------------------------------------
    def _trim_pending_submissions(self):
        pending = list(Submission.objects.filter(status=SubmissionStatus.PENDING))
        current_count = len(pending)
        if current_count <= TARGET_PENDING_SUBMISSIONS:
            self.stdout.write(
                f"Pending submissions already at {current_count} — no trim needed."
            )
            return

        random.shuffle(pending)
        to_keep = pending[:TARGET_PENDING_SUBMISSIONS]
        to_resolve = pending[TARGET_PENDING_SUBMISSIONS:]
        keep_ids = {s.pk for s in to_keep}

        approved_count = 0
        rejected_count = 0
        for sub in to_resolve:
            if random.random() < 0.60:
                sub.status = SubmissionStatus.APPROVED
                sub.decision_at = timezone.now()
                approved_count += 1
            else:
                sub.status = SubmissionStatus.REJECTED
                sub.decision_at = timezone.now()
                rejected_count += 1
            sub.save(update_fields=["status", "decision_at"])

        remaining = Submission.objects.filter(status=SubmissionStatus.PENDING).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Submissions: approved {approved_count}, rejected {rejected_count}. "
                f"Pending remaining: {remaining}."
            )
        )

    # ------------------------------------------------------------------
    def _trim_pending_reports(self):
        pending = list(Report.objects.filter(status=ReportStatus.PENDING))
        current_count = len(pending)
        if current_count <= TARGET_PENDING_REPORTS:
            self.stdout.write(
                f"Pending reports already at {current_count} — no trim needed."
            )
            return

        random.shuffle(pending)
        to_resolve = pending[TARGET_PENDING_REPORTS:]

        ids_to_resolve = [r.pk for r in to_resolve]
        Report.objects.filter(pk__in=ids_to_resolve).update(status=ReportStatus.RESOLVED)

        remaining = Report.objects.filter(status=ReportStatus.PENDING).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Resolved {len(to_resolve)} report(s). Pending remaining: {remaining}."
            )
        )
