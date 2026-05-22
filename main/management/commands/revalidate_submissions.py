import time

from django.core.management.base import BaseCommand

from accounts.models import Submission, SubValidation, ValidationResult
from accounts.link_validator import validate_submission, build_validation_note


class Command(BaseCommand):
    help = "Re-run link_validator on all existing submissions and replace SubValidation records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would happen without writing to the database.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Only process the first N submissions (useful for testing).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit   = options["limit"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no database writes.\n"))

        qs = (
            Submission.objects
            .select_related("opportunity")
            .filter(opportunity__source_link__isnull=False)
            .exclude(opportunity__source_link="")
            .order_by("id")
        )
        if limit:
            qs = qs[:limit]

        all_submissions = list(qs)
        total_with_url  = len(all_submissions)

        # Skipped = submissions whose opportunity has no source_link
        total_all = Submission.objects.count()
        skipped   = total_all - Submission.objects.filter(
            opportunity__source_link__isnull=False
        ).exclude(opportunity__source_link="").count()

        counts = {"approved": 0, "flagged": 0, "rejected": 0, "error": 0}

        _result_map = {
            "approved": ValidationResult.PASS,
            "rejected": ValidationResult.FAIL,
            "flagged":  ValidationResult.PENDING,
        }
        _status_style = {
            "approved": lambda s: self.style.SUCCESS(s),
            "flagged":  lambda s: self.style.WARNING(s),
            "rejected": lambda s: self.style.ERROR(s),
        }

        for idx, submission in enumerate(all_submissions, start=1):
            opp         = submission.opportunity
            url         = str(opp.source_link)
            title       = opp.title or ""
            company     = opp.company or ""
            description = opp.description or ""

            short = (title[:40] + "...") if len(title) > 40 else title
            prefix = f"[{idx}/{total_with_url}] {short:<43}"

            try:
                result = validate_submission(
                    url=url,
                    title=title,
                    company=company,
                    description=description,
                )
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"{prefix} → ERROR: {exc}"))
                counts["error"] += 1
                time.sleep(2)
                continue

            fs    = result["final_status"]
            counts[fs] = counts.get(fs, 0) + 1
            stylefn     = _status_style.get(fs, lambda s: s)
            self.stdout.write(f"{prefix} → {stylefn(fs.upper())}")

            if not dry_run:
                note = build_validation_note(result, url=url, title=title, company=company)
                SubValidation.objects.filter(submission=submission).delete()
                SubValidation.objects.create(
                    submission=submission,
                    result=_result_map[fs],
                    note=note,
                )

            time.sleep(2)

        self.stdout.write("")
        suffix = " (dry run — nothing written)" if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"Done{suffix}. "
            f"{total_with_url} processed, {skipped} skipped (no URL). "
            f"{counts['approved']} approved, {counts['flagged']} flagged, "
            f"{counts['rejected']} rejected, {counts['error']} errors."
        ))
