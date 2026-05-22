"""
Management command: cleanup_and_trim

1. Delete junk Opportunity records (title ≤ 2 chars — e.g. 'sa', 'aa').
   Cascade deletes their Submissions, SubValidations, and Reports.
2. Trim seeded approved student submissions down to ~28 total.
   Re-generate the leaderboard from remaining approved submissions.
3. Fix analytics: total_opportunities query is already correct in the view.
4. (Caller is responsible for deleting AuditLog after this command.)
"""
import random
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from accounts.models import (
    AuditLog,
    Leaderboard,
    LeaderboardEntry,
    Opportunity,
    Student,
    Submission,
    SubmissionStatus,
    SubmitterType,
)

TARGET_APPROVED_STUDENT_SUBS = 28


class Command(BaseCommand):
    help = "Delete junk test data, trim excess seed submissions, rebuild leaderboard, clear audit log."

    def handle(self, *args, **options):
        self._delete_junk_opportunities()
        self._trim_approved_submissions()
        self._rebuild_leaderboard()
        self._clear_audit_log()

    def _delete_junk_opportunities(self):
        junk_qs = Opportunity.objects.filter(title__regex=r'^.{1,2}$')
        count = junk_qs.count()
        junk_qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Deleted {count} junk opportunity/ies (title ≤ 2 chars) and their linked records."
        ))

    def _trim_approved_submissions(self):
        approved_student = list(
            Submission.objects
            .filter(status=SubmissionStatus.APPROVED, submitted_by_type=SubmitterType.STUDENT)
            .order_by('id')
        )
        current = len(approved_student)
        if current <= TARGET_APPROVED_STUDENT_SUBS:
            self.stdout.write(f"Approved student submissions already at {current} — no trim needed.")
            return

        random.shuffle(approved_student)
        to_keep = approved_student[:TARGET_APPROVED_STUDENT_SUBS]
        to_delete = approved_student[TARGET_APPROVED_STUDENT_SUBS:]
        keep_ids = {s.pk for s in to_keep}
        delete_ids = [s.pk for s in to_delete]
        Submission.objects.filter(pk__in=delete_ids).delete()
        remaining = Submission.objects.filter(
            status=SubmissionStatus.APPROVED, submitted_by_type=SubmitterType.STUDENT
        ).count()
        self.stdout.write(self.style.SUCCESS(
            f"Trimmed approved student submissions: {current} → {remaining}."
        ))

    def _rebuild_leaderboard(self):
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
        entry_count = lb.entries.count()
        top = lb.entries.order_by("rank").first()
        top_name = top.student.display_name if top else "—"
        top_score = int(top.score) if top else 0
        self.stdout.write(self.style.SUCCESS(
            f"Leaderboard rebuilt: {entry_count} entries. Top: {top_name} ({top_score} submissions)."
        ))

    def _clear_audit_log(self):
        count, _ = AuditLog.objects.all().delete()
        self.stdout.write(self.style.SUCCESS(f"Cleared {count} audit log entries."))
