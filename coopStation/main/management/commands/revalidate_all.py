"""
Management command: revalidate_all

Wipes all platform data (except Students, OppCategory, Admin) then
re-validates every source link from scratch using the 4-step pipeline,
re-creates Opportunity + Submission + SubValidation records, and rebuilds
the Leaderboard.

Usage:
    python manage.py revalidate_all            # full run
    python manage.py revalidate_all --dry-run  # print-only, no DB writes
"""
import itertools
import random
import time
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.link_validator import build_validation_note, validate_submission
from accounts.models import (
    AIDiscovery,
    AuditLog,
    FetchSession,
    Leaderboard,
    LeaderboardEntry,
    Opportunity,
    OpportunityStatus,
    Report,
    Student,
    Submission,
    SubmissionStatus,
    SubValidation,
    ValidationResult,
)


class Command(BaseCommand):
    help = "Wipe all platform data and re-validate every source link from scratch."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would happen without writing to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no database writes.\n"))

        # ── Step 1: Snapshot ────────────────────────────────────────────────
        opportunities_snapshot = list(
            Opportunity.objects
            .exclude(source_link__isnull=True)
            .exclude(source_link="")
            .values("source_link", "category_id", "title", "company")
        )
        self.stdout.write(
            f"Snapshotted {len(opportunities_snapshot)} opportunities with source links."
        )

        # ── Step 2: Wipe ────────────────────────────────────────────────────
        if not dry_run:
            AuditLog.objects.all().delete()
            LeaderboardEntry.objects.all().delete()
            Leaderboard.objects.all().delete()
            SubValidation.objects.all().delete()
            Report.objects.all().delete()
            Submission.objects.all().delete()
            Opportunity.objects.all().delete()
            FetchSession.objects.all().delete()
            AIDiscovery.objects.all().delete()
            self.stdout.write(self.style.WARNING("All platform data wiped.\n"))

        # ── Step 3 & 4: Re-validate and distribute across students ──────────
        students = list(Student.objects.all())
        if not students:
            self.stdout.write(self.style.ERROR("No students found. Aborting."))
            return

        random.shuffle(students)
        student_cycle = itertools.cycle(students)

        total = len(opportunities_snapshot)
        counts = {"approved": 0, "flagged": 0, "rejected": 0, "error": 0}
        submissions_created = 0

        for idx, snap in enumerate(opportunities_snapshot, start=1):
            url        = snap["source_link"]
            title      = snap["title"] or ""
            company    = snap["company"] or ""
            category_id = snap["category_id"]

            label  = (title[:40] + "...") if len(title) > 40 else title
            prefix = f"[{idx}/{total}] {label:<43}"

            try:
                result = validate_submission(
                    url=url,
                    title=title,
                    company=company,
                    description="",
                )
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"{prefix} → ERROR: {exc}"))
                counts["error"] += 1
                time.sleep(2)
                continue

            fs = result["final_status"]
            counts[fs] = counts.get(fs, 0) + 1

            status_label = {
                "approved": self.style.SUCCESS("APPROVED"),
                "flagged":  self.style.WARNING("FLAGGED"),
                "rejected": self.style.ERROR("REJECTED"),
            }.get(fs, fs.upper())
            self.stdout.write(f"{prefix} → {status_label}")

            if dry_run:
                time.sleep(2)
                continue

            student      = next(student_cycle)
            submitted_at = timezone.now() - timedelta(days=random.randint(1, 90))

            vr_map = {
                "approved": ValidationResult.PASS,
                "flagged":  ValidationResult.PENDING,
                "rejected": ValidationResult.FAIL,
            }
            vr   = vr_map[fs]
            note = build_validation_note(result, url=url, title=title, company=company)

            if fs == "approved":
                opp = Opportunity.objects.create(
                    title=title,
                    company=company or None,
                    source_link=url,
                    category_id=category_id,
                    status=OpportunityStatus.OPEN,
                )
                sub = Submission.objects.create(
                    opportunity=opp,
                    submitted_by_student=student,
                    submitted_at=submitted_at,
                    status=SubmissionStatus.APPROVED,
                    decision_at=submitted_at + timedelta(hours=random.randint(1, 48)),
                    link=url,
                )
                SubValidation.objects.create(
                    submission=sub,
                    result=vr,
                    note=note,
                )
            else:
                # Opportunity must exist for the FK — mark it closed so it
                # never appears in the public listing.
                opp = Opportunity.objects.create(
                    title=title,
                    company=company or None,
                    source_link=url,
                    category_id=category_id,
                    status=OpportunityStatus.CLOSED,
                )
                sub = Submission.objects.create(
                    opportunity=opp,
                    submitted_by_student=student,
                    submitted_at=submitted_at,
                    status=SubmissionStatus.REJECTED,
                    decision_at=submitted_at + timedelta(hours=random.randint(1, 12)),
                    link=url,
                )
                SubValidation.objects.create(
                    submission=sub,
                    result=vr,
                    note=note,
                    failed_step=result.get("step"),
                )

            submissions_created += 1
            time.sleep(2)

        if dry_run:
            self._print_summary(total, counts, submissions_created, 0, dry_run=True)
            return

        # ── Step 5: Rebuild leaderboard ─────────────────────────────────────
        from django.db.models import Count

        Leaderboard.objects.all().delete()
        lb = Leaderboard.objects.create()

        submission_counts = (
            Submission.objects
            .filter(
                status=SubmissionStatus.APPROVED,
                submitted_by_type="student",
                submitted_by_student__isnull=False,
            )
            .values("submitted_by_student")
            .annotate(total=Count("id"))
            .order_by("-total")
        )

        for rank, entry in enumerate(submission_counts, start=1):
            LeaderboardEntry.objects.create(
                leaderboard=lb,
                student_id=entry["submitted_by_student"],
                rank=rank,
                score=Decimal(entry["total"]),
            )

        lb_count = lb.entries.count()

        # ── Step 6: Clear audit log ──────────────────────────────────────────
        AuditLog.objects.all().delete()

        # ── Step 7: Summary ──────────────────────────────────────────────────
        self._print_summary(total, counts, submissions_created, lb_count, dry_run=False)

    def _print_summary(self, total, counts, submissions_created, lb_count, *, dry_run):
        approved  = counts.get("approved", 0)
        failed    = counts.get("rejected", 0) + counts.get("flagged", 0)
        errors    = counts.get("error", 0)

        self.stdout.write("")
        self.stdout.write("=" * 48)
        suffix = " (DRY RUN)" if dry_run else ""
        self.stdout.write(f"Re-validation Complete{suffix}")
        self.stdout.write("=" * 48)
        self.stdout.write(f"Source links processed : {total}")
        self.stdout.write(f"Passed validation      : {approved}")
        self.stdout.write(f"Failed / flagged       : {failed}")
        if errors:
            self.stdout.write(self.style.ERROR(f"Errors                 : {errors}"))
        if not dry_run:
            self.stdout.write(f"Opportunities published: {approved}")
            self.stdout.write(f"Submissions created    : {submissions_created}")
            self.stdout.write(f"Leaderboard entries    : {lb_count}")
            self.stdout.write(f"Audit log cleared      : ✓")
        self.stdout.write("=" * 48)
        self.stdout.write("")
