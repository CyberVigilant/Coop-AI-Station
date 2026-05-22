"""
Management command: resolve_flagged

Scores all flagged (pending) SubValidation records, keeps the 5 best as
showcase cases, and auto-decides the rest by confidence threshold (≥60 →
approve, <60 → reject). Rebuilds the leaderboard and clears the audit log.

Usage:
    python manage.py resolve_flagged
"""
import re
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count

from accounts.models import (
    AuditLog,
    Leaderboard,
    LeaderboardEntry,
    OpportunityStatus,
    Submission,
    SubmissionStatus,
    SubValidation,
    ValidationResult,
)

_APPROVE_THRESHOLD = 60

_KNOWN_SAUDI_COMPANIES = {
    "aramco", "saudi aramco", "stc", "saudi telecom", "sabic", "snb",
    "saudi national bank", "ncb", "alahli", "al rajhi", "alrajhi",
    "elm", "sdaia", "neom", "ma'aden", "maaden", "taqa", "sctv",
    "kpmg", "pwc", "deloitte", "ernst", "mckinsey", "accenture",
    "ibm", "microsoft", "oracle", "sidf", "sdb", "jadwa", "jarir",
    "mobily", "zain", "virgin", "ericsson", "bupa", "tawuniya",
    "tadawul", "samba", "riyadh bank", "alinma", "bakerhughes",
    "baker hughes", "schlumberger", "halliburton", "saudamco",
    "red sea", "qiddiya", "roshn", "sabco", "saudi re",
}

_GIBBERISH_TOKENS = {"test", "testing", "demo", "fake", "sample", "n/a", "tbd"}


def _parse_note(note: str):
    """Return (confidence_int_or_None, reason_str) from a SubValidation note."""
    if not note:
        return None, ""
    first_line = note.split("\n")[0]
    conf_match = re.search(r"Confidence:\s*(\d+)%", first_line)
    confidence = int(conf_match.group(1)) if conf_match else None
    reason_match = re.search(r"Confidence:\s*\d+%\.\s*(.+)$", first_line)
    reason = reason_match.group(1).strip() if reason_match else first_line
    return confidence, reason


def _showcase_score(title: str, company: str, reason: str, confidence: int) -> int:
    score = confidence or 0

    if len(title) > 20:
        score += 5

    company_lower = (company or "").lower()
    if any(known in company_lower for known in _KNOWN_SAUDI_COMPANIES):
        score += 5

    if len(reason) > 80:
        score += 3

    title_lower = title.lower()
    if any(token in title_lower for token in _GIBBERISH_TOKENS):
        score -= 20

    return score


def _pick_showcase(candidates: list, n: int = 5) -> list:
    """
    Greedy selection: pick n candidates maximising variety across
    company, category, and failed_step while preferring higher score.
    """
    sorted_candidates = sorted(candidates, key=lambda c: c["score"], reverse=True)

    picked = []
    used_companies = set()
    used_categories = set()
    used_steps = set()

    # First pass: strict variety
    for c in sorted_candidates:
        if len(picked) >= n:
            break
        company_key = (c["company"] or "").strip().lower()
        cat_key     = c["category"]
        step_key    = c["failed_step"]

        if (company_key and company_key in used_companies):
            continue
        if (cat_key and cat_key in used_categories):
            continue

        picked.append(c)
        if company_key:
            used_companies.add(company_key)
        if cat_key:
            used_categories.add(cat_key)
        if step_key:
            used_steps.add(step_key)

    # Second pass: fill remaining slots ignoring variety constraints
    if len(picked) < n:
        picked_ids = {c["sv_id"] for c in picked}
        for c in sorted_candidates:
            if len(picked) >= n:
                break
            if c["sv_id"] not in picked_ids:
                picked.append(c)
                picked_ids.add(c["sv_id"])

    return picked


class Command(BaseCommand):
    help = "Auto-resolve flagged submissions: keep 5 as showcase, decide the rest by confidence."

    def handle(self, *args, **options):
        flagged_svs = (
            SubValidation.objects
            .filter(result=ValidationResult.PENDING)
            .select_related("submission__opportunity__category", "submission__submitted_by_student")
        )

        total_flagged = flagged_svs.count()
        if total_flagged == 0:
            self.stdout.write(self.style.WARNING("No flagged submissions found. Nothing to do."))
            return

        self.stdout.write(f"Found {total_flagged} flagged submission(s). Scoring…\n")

        # ── Step 1: Score all candidates ────────────────────────────────────
        candidates = []
        for sv in flagged_svs:
            opp        = sv.submission.opportunity
            title      = opp.title or ""
            company    = opp.company or ""
            category   = opp.category_id
            confidence, reason = _parse_note(sv.note)

            candidates.append({
                "sv_id":      sv.pk,
                "sv":         sv,
                "title":      title,
                "company":    company,
                "category":   category,
                "failed_step": sv.failed_step,
                "confidence": confidence,
                "reason":     reason,
                "score":      _showcase_score(title, company, reason, confidence or 0),
            })

        # ── Step 2: Select best 5 showcase cases ────────────────────────────
        showcase = _pick_showcase(candidates, n=5)
        showcase_ids = {c["sv_id"] for c in showcase}

        self.stdout.write("=== KEPT AS FLAGGED (showcase cases) ===")
        for i, c in enumerate(showcase, start=1):
            self.stdout.write(
                f"{i}. {c['title']} — {c['company'] or '—'} — "
                f"confidence: {c['confidence']}% — "
                f"failed_step: {c['failed_step']} — "
                f"reason: {c['reason'][:100]}"
            )
        self.stdout.write("")

        # ── Step 3: Auto-decide the remaining ───────────────────────────────
        approved_count  = 0
        rejected_count  = 0

        for c in candidates:
            if c["sv_id"] in showcase_ids:
                continue

            sv         = c["sv"]
            submission = sv.submission
            opp        = submission.opportunity
            confidence = c["confidence"] or 0

            if confidence >= _APPROVE_THRESHOLD:
                submission.status = SubmissionStatus.APPROVED
                submission.save(update_fields=["status"])
                opp.status = OpportunityStatus.OPEN
                opp.save(update_fields=["status"])
                sv.result = ValidationResult.PASS
                sv.save(update_fields=["result"])
                approved_count += 1
            else:
                submission.status = SubmissionStatus.REJECTED
                submission.save(update_fields=["status"])
                sv.result = ValidationResult.FAIL
                sv.save(update_fields=["result"])
                rejected_count += 1

        # ── Step 4: Rebuild leaderboard ─────────────────────────────────────
        Leaderboard.objects.all().delete()
        lb = Leaderboard.objects.create()

        counts = (
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

        for rank, entry in enumerate(counts, start=1):
            LeaderboardEntry.objects.create(
                leaderboard=lb,
                student_id=entry["submitted_by_student"],
                rank=rank,
                score=Decimal(entry["total"]),
            )

        lb_count = lb.entries.count()

        # ── Step 5: Clear audit log ──────────────────────────────────────────
        AuditLog.objects.all().delete()

        # ── Step 6: Summary ──────────────────────────────────────────────────
        self.stdout.write("=== resolve_flagged complete ===")
        self.stdout.write(f"Total flagged processed   : {total_flagged}")
        self.stdout.write(f"Kept as flagged (showcase): {len(showcase)}")
        self.stdout.write(self.style.SUCCESS(f"Auto-approved             : {approved_count}"))
        self.stdout.write(self.style.ERROR(  f"Auto-rejected             : {rejected_count}"))
        self.stdout.write(f"Leaderboard rebuilt       : ✓  ({lb_count} entries)")
        self.stdout.write(f"Audit log cleared         : ✓")
        self.stdout.write("")
        self.stdout.write("AI Validations panel should now show:")
        self.stdout.write(f"  Flagged for Review: 5")
        self.stdout.write("================================")
