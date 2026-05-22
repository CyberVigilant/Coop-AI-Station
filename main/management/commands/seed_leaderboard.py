import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from accounts.models import (
    Leaderboard,
    LeaderboardEntry,
    Opportunity,
    Student,
    Submission,
    SubmissionStatus,
    SubmitterType,
)

# (min_submissions, max_submissions, student_count)
TIERS = [
    (0,  0,  8),   # badge: Contribute Now
    (1,  1,  8),   # badge: Contributor
    (2,  4,  8),   # badge: Active
    (5,  9,  5),   # badge: Expert
    (10, 14, 3),   # badge: Elite
    (15, 20, 1),   # badge: Top Contributor
]


class Command(BaseCommand):
    help = "Seed realistic submission data and regenerate the leaderboard."

    def handle(self, *args, **options):
        students = list(Student.objects.all())
        opportunities = list(Opportunity.objects.all())

        if not students:
            self.stdout.write(self.style.ERROR("No students found."))
            return
        if not opportunities:
            self.stdout.write(self.style.ERROR("No opportunities found."))
            return

        random.shuffle(students)

        # Assign tier targets — cycle through students
        tier_assignments = []
        for min_s, max_s, count in TIERS:
            for _ in range(count):
                target = random.randint(min_s, max_s) if min_s != max_s else min_s
                tier_assignments.append(target)

        # Pad or trim to match actual student count
        while len(tier_assignments) < len(students):
            tier_assignments.append(random.randint(1, 3))
        tier_assignments = tier_assignments[: len(students)]

        total_created = 0

        for student, target_count in zip(students, tier_assignments):
            if target_count == 0:
                continue

            # Track which opps this student already has submissions for (existing + new)
            already_used = set(
                Submission.objects
                .filter(submitted_by_student=student)
                .values_list("opportunity_id", flat=True)
            )

            created_for_student = 0
            available_opps = [o for o in opportunities if o.pk not in already_used]
            random.shuffle(available_opps)

            for opp in available_opps:
                if created_for_student >= target_count:
                    break
                submitted_at = timezone.now() - timedelta(days=random.randint(1, 90))
                Submission.objects.create(
                    opportunity=opp,
                    submitted_by_student=student,
                    submitted_by_type=SubmitterType.STUDENT,
                    status=SubmissionStatus.APPROVED,
                    submitted_at=submitted_at,
                    decision_at=submitted_at + timedelta(hours=random.randint(1, 48)),
                    link=opp.source_link or "",
                )
                already_used.add(opp.pk)
                created_for_student += 1
                total_created += 1

        self.stdout.write(f"Created {total_created} submission(s) across {len(students)} student(s).")

        # Regenerate leaderboard
        Leaderboard.objects.all().delete()
        lb = Leaderboard.objects.create()

        submission_counts = (
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

        for rank, entry in enumerate(submission_counts, start=1):
            student = Student.objects.get(pk=entry["submitted_by_student"])
            LeaderboardEntry.objects.create(
                leaderboard=lb,
                student=student,
                rank=rank,
                score=Decimal(entry["total"]),
            )

        entry_count = lb.entries.count()
        top = lb.entries.order_by("rank").first()
        top_name = top.student.display_name if top else "—"
        top_score = int(top.score) if top else 0

        self.stdout.write(self.style.SUCCESS(
            f"Leaderboard regenerated: {entry_count} entries.\n"
            f"Top contributor: {top_name} with {top_score} submissions."
        ))
