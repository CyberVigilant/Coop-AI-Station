import json
import re
from collections import defaultdict
from functools import wraps
 
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Case, Count, Q, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
 
from .views import REGIONS_AND_CITIES, AI_AUTO_APPROVE_THRESHOLD
from .link_validator import validate_submission as link_validate_submission, build_validation_note
 
from .models import (
    Admin,
    AIDiscovery,
    AuditLog,
    FetchSchedule,
    FetchSession,
    FetchSessionStatus,
    Leaderboard,
    LeaderboardEntry,
    OppCategory,
    Opportunity,
    OpportunityStatus,
    Report,
    ReportStatus,
    ReportType,
    MonitorResult,
    MonitorSchedule,
    MonitorSession,
    Student,
    Submission,
    SubmissionStatus,
    SubmitterType,
    SubValidation,
    ValidationResult,
)
from .opportunity_observer import run_fetch_session
 
# ---------------------------------------------------------------------------
# Auth decorator
# ---------------------------------------------------------------------------
 
def admin_panel_login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get("admin_panel_admin_id"):
            return redirect("admin_login")
        return view_func(request, *args, **kwargs)
    return wrapper
 
 
# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------
 
def _get_current_admin(request):
    return Admin.objects.filter(id=request.session.get("admin_panel_admin_id")).first()
 
 
def _log(admin, action, target_type, target_id=None, target_label=None, note=None):
    AuditLog.objects.create(
        admin=admin,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        note=note,
    )
 
 
def _sidebar_counts():
    return {
        "pending_submissions_count": Submission.objects.filter(
            status=SubmissionStatus.PENDING
        ).count(),
        "pending_reports_count": Report.objects.filter(
            status=ReportStatus.PENDING
        ).count(),
    }
 
 
# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
 
def admin_login(request):
    if request.session.get("admin_panel_admin_id"):
        return redirect("admin_dashboard")
 
    error = None
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        pw = request.POST.get("password") or ""
        admin = Admin.objects.filter(email=email).first()
        if admin and check_password(pw, admin.password):
            request.session["admin_panel_admin_id"] = admin.id
            return redirect("admin_dashboard")
        error = "Invalid email or password."
 
    return render(request, "admin_panel/admin_login.html", {"error": error})
 
 
def admin_logout(request):
    request.session.pop("admin_panel_admin_id", None)
    return redirect("admin_login")
 
 
# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
 
@admin_panel_login_required
def admin_dashboard(request):
    ctx = {
        "admin": _get_current_admin(request),
        "total_opportunities": Opportunity.objects.count(),
        "total_students": Student.objects.count(),
        "pending_submissions": Submission.objects.filter(status=SubmissionStatus.PENDING).count(),
        "pending_reports": Report.objects.filter(status=ReportStatus.PENDING).count(),
        "recent_audit": AuditLog.objects.select_related("admin").order_by("-timestamp")[:5],
    }
    ctx.update(_sidebar_counts())
    return render(request, "admin_panel/admin_dashboard.html", ctx)
 
 
# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------
 
@admin_panel_login_required
@require_POST
def admin_approve_submission(request, sub_id):
    sub = get_object_or_404(Submission, id=sub_id)
    admin = _get_current_admin(request)

    sub.status = SubmissionStatus.APPROVED
    sub.decision_at = timezone.now()
    sub.save()

    sub.opportunity.status = OpportunityStatus.OPEN
    sub.opportunity.save(update_fields=["status"])

    # Update the existing SubValidation so the row leaves the pending queue
    SubValidation.objects.filter(submission=sub).update(
        result=ValidationResult.PASS,
        admin=admin,
        validated_at=timezone.now(),
    )

    if sub.submitted_by_student:
        student = sub.submitted_by_student
        approved_count = Submission.objects.filter(
            submitted_by_student=student, status=SubmissionStatus.APPROVED
        ).count()
        today = timezone.now().date()
        lb = Leaderboard.objects.filter(generated_at__date=today).first()
        if not lb:
            lb = Leaderboard.objects.create()
        entry, created = LeaderboardEntry.objects.get_or_create(
            leaderboard=lb,
            student=student,
            defaults={"score": approved_count, "rank": 1},
        )
        if not created:
            entry.score = approved_count
            entry.save(update_fields=["score"])

    _log(admin, "approved_submission", "submission", sub.id, sub.opportunity.title)
    next_url = request.POST.get("next", "")
    return redirect(next_url if next_url.startswith("/") else "admin_ai_validations")


@admin_panel_login_required
@require_POST
def admin_reject_submission(request, sub_id):
    sub = get_object_or_404(Submission, id=sub_id)
    admin = _get_current_admin(request)

    sub.status = SubmissionStatus.REJECTED
    sub.decision_at = timezone.now()
    sub.save()

    # Update the existing SubValidation so the row leaves the pending queue
    updated = SubValidation.objects.filter(submission=sub).update(
        result=ValidationResult.FAIL,
        admin=admin,
        validated_at=timezone.now(),
    )
    if not updated:
        SubValidation.objects.create(
            submission=sub, admin=admin, result=ValidationResult.FAIL
        )

    _log(admin, "rejected_submission", "submission", sub.id, sub.opportunity.title)
    next_url = request.POST.get("next", "")
    return redirect(next_url if next_url.startswith("/") else "admin_ai_validations")
 
 
# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------
 
@admin_panel_login_required
def admin_opportunities(request):
    q = (request.GET.get("q") or "").strip()
    category_filter = (request.GET.get("category") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()
 
    qs = (
        Opportunity.objects
        .exclude(submissions__status__in=[SubmissionStatus.PENDING, SubmissionStatus.REJECTED])
        .select_related("category")
        .order_by("-created_at")
    )
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(company__icontains=q))
    if category_filter:
        qs = qs.filter(category_id=category_filter)
    if status_filter in ("open", "closed"):
        qs = qs.filter(status=status_filter)
 
    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
 
    ctx = {
        "admin": _get_current_admin(request),
        "page_obj": page_obj,
        "categories": OppCategory.objects.all().order_by("name"),
        "q": q,
        "category_filter": category_filter,
        "status_filter": status_filter,
    }
    ctx.update(_sidebar_counts())
    return render(request, "admin_panel/admin_opportunities.html", ctx)
 
 
@admin_panel_login_required
def admin_add_opportunity(request):
    categories = OppCategory.objects.all().order_by("name")
    errors = {}
    selected_region = ""
    selected_city = ""
 
    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        company = (request.POST.get("company") or "").strip()
        company_domain = (request.POST.get("company_domain") or "").strip().lower()
        description = (request.POST.get("description") or "").strip()
        selected_region = (request.POST.get("region") or "").strip()
        selected_city = (request.POST.get("city") or "").strip()
        deadline_raw = (request.POST.get("deadline") or "").strip()
        source_link = (request.POST.get("source_link") or "").strip()
        category_id = (request.POST.get("category") or "").strip()

        if selected_region and selected_city:
            location = f"{selected_region},{selected_city}"
        elif selected_region:
            location = selected_region
        else:
            location = None

        if not title:
            errors["title"] = "Title is required."
        if not category_id:
            errors["category"] = "Category is required."

        if not errors:
            from datetime import date
            deadline = None
            if deadline_raw:
                try:
                    deadline = date.fromisoformat(deadline_raw)
                except ValueError:
                    errors["deadline"] = "Enter a valid date."

        if not errors:
            opp = Opportunity.objects.create(
                title=title,
                company=company or None,
                company_domain=company_domain,
                description=description or None,
                location=location,
                deadline=deadline,
                source_link=source_link or None,
                category_id=category_id,
                status=OpportunityStatus.OPEN,
            )
            admin = _get_current_admin(request)
            submission = Submission.objects.create(
                opportunity=opp,
                submitted_by_admin=admin,
                status=SubmissionStatus.PENDING,
                link=source_link or None,
            )

            link_result = link_validate_submission(
                url=source_link or "",
                title=title,
                company=company,
                description=description,
            )
            _note = build_validation_note(link_result, url=source_link or "", title=title, company=company)

            if link_result["final_status"] == "approved":
                submission.status = SubmissionStatus.APPROVED
                submission.decision_at = timezone.now()
                submission.save()
                opp.status = OpportunityStatus.OPEN
                opp.save(update_fields=["status"])
                SubValidation.objects.create(
                    submission=submission,
                    result=ValidationResult.PASS,
                    note=_note,
                )
                messages.success(request, "✅ Opportunity approved by AI and published.")

            elif link_result["final_status"] == "rejected":
                submission.status = SubmissionStatus.REJECTED
                submission.decision_at = timezone.now()
                submission.save()
                SubValidation.objects.create(
                    submission=submission,
                    result=ValidationResult.FAIL,
                    note=_note,
                )
                messages.error(request, f"❌ AI rejected this opportunity. See Raw output for details.")

            else:
                SubValidation.objects.create(
                    submission=submission,
                    result=ValidationResult.PENDING,
                    note=_note,
                )
                messages.info(request, "⏳ AI flagged this opportunity for manual review.")

            _log(admin, "added_opportunity", "opportunity", opp.id, opp.title)
            return redirect("admin_opportunities")
 
    initial = {k: (request.POST.get(k) or "") for k in
               ["title", "company", "company_domain", "description", "deadline", "source_link", "category", "status"]} \
        if request.method == "POST" else {"status": "open"}
 
    ctx = {
        "admin": _get_current_admin(request),
        "opp": None,
        "initial": initial,
        "categories": categories,
        "errors": errors,
        "form_title": "Add Opportunity",
        "submit_label": "Add Opportunity",
        "regions_and_cities_json": json.dumps(REGIONS_AND_CITIES),
        "regions": list(REGIONS_AND_CITIES.keys()),
        "selected_region": selected_region,
        "selected_city": selected_city,
    }
    ctx.update(_sidebar_counts())
    return render(request, "admin_panel/admin_add_edit_opportunity.html", ctx)
 
 
@admin_panel_login_required
def admin_edit_opportunity(request, opp_id):
    opp = get_object_or_404(Opportunity, id=opp_id)
    categories = OppCategory.objects.all().order_by("name")
    errors = {}
 
    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        company = (request.POST.get("company") or "").strip()
        company_domain = (request.POST.get("company_domain") or "").strip().lower()
        description = (request.POST.get("description") or "").strip()
        selected_region = (request.POST.get("region") or "").strip()
        selected_city = (request.POST.get("city") or "").strip()
        deadline_raw = (request.POST.get("deadline") or "").strip()
        source_link = (request.POST.get("source_link") or "").strip()
        category_id = (request.POST.get("category") or "").strip()
        status = (request.POST.get("status") or "").strip()
 
        if selected_region and selected_city:
            location = f"{selected_region},{selected_city}"
        elif selected_region:
            location = selected_region
        else:
            location = None
 
        if not title:
            errors["title"] = "Title is required."
        if not category_id:
            errors["category"] = "Category is required."
 
        deadline = opp.deadline
        if deadline_raw:
            from datetime import date
            try:
                deadline = date.fromisoformat(deadline_raw)
            except ValueError:
                errors["deadline"] = "Enter a valid date."
        elif not deadline_raw:
            deadline = None
 
        if not errors:
            opp.title = title
            opp.company = company or None
            opp.company_domain = company_domain
            opp.description = description or None
            opp.location = location
            opp.deadline = deadline
            opp.source_link = source_link or None
            opp.category_id = category_id
            if status in (OpportunityStatus.OPEN, OpportunityStatus.CLOSED):
                opp.status = status
            opp.save()
            _log(
                _get_current_admin(request),
                "edited_opportunity",
                "opportunity",
                opp.id,
                opp.title,
            )
            return redirect("admin_opportunities")
    else:
        selected_region = ""
        selected_city = ""
        if opp.location and "," in opp.location:
            parts = opp.location.split(",", 1)
            selected_region, selected_city = parts[0].strip(), parts[1].strip()
        elif opp.location:
            selected_region = opp.location.strip()
 
    if request.method == "POST":
        initial = {k: (request.POST.get(k) or "") for k in
                   ["title", "company", "company_domain", "description", "deadline", "source_link", "category", "status"]}
    else:
        initial = {
            "title":          opp.title or "",
            "company":        opp.company or "",
            "company_domain": opp.company_domain or "",
            "description":    opp.description or "",
            "deadline":       opp.deadline.isoformat() if opp.deadline else "",
            "source_link":    opp.source_link or "",
            "category":       str(opp.category_id),
            "status":         opp.status,
        }
 
    ctx = {
        "admin": _get_current_admin(request),
        "opp": opp,
        "initial": initial,
        "categories": categories,
        "errors": errors,
        "form_title": f"Edit: {opp.title}",
        "submit_label": "Save Changes",
        "regions_and_cities_json": json.dumps(REGIONS_AND_CITIES),
        "regions": list(REGIONS_AND_CITIES.keys()),
        "selected_region": selected_region,
        "selected_city": selected_city,
    }
    ctx.update(_sidebar_counts())
    return render(request, "admin_panel/admin_add_edit_opportunity.html", ctx)
 
 
@admin_panel_login_required
def admin_opp_detail_partial(request, opp_id):
    opp = get_object_or_404(Opportunity, id=opp_id)
    region = city = ""
    if opp.location and "," in opp.location:
        parts = opp.location.split(",", 1)
        region, city = parts[0].strip(), parts[1].strip()
    elif opp.location:
        region = opp.location.strip()
    return render(request, "admin_panel/_opp_detail_partial.html", {
        "opp": opp,
        "region": region,
        "city": city,
    })


@admin_panel_login_required
@require_POST
def admin_delete_opportunity(request, opp_id):
    opp = get_object_or_404(Opportunity, id=opp_id)
    title = opp.title
    opp.delete()
    _log(
        _get_current_admin(request),
        "deleted_opportunity",
        "opportunity",
        opp_id,
        title,
    )
    next_url = request.POST.get("next", "")
    return redirect(next_url if next_url.startswith("/") else "admin_opportunities")
 
 
# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
 
@admin_panel_login_required
def admin_reports(request):
    status_filter = (request.GET.get("status") or "").strip()
    type_filter = (request.GET.get("type") or "").strip()
 
    qs = Report.objects.select_related(
        "student__user", "opportunity"
    ).order_by(
        Case(When(status=ReportStatus.PENDING, then=0), default=1),
        "-created_at",
    )
    if status_filter in ("pending", "resolved"):
        qs = qs.filter(status=status_filter)
    if type_filter:
        qs = qs.filter(report_type=type_filter)
 
    ctx = {
        "admin": _get_current_admin(request),
        "reports": qs,
        "status_filter": status_filter,
        "type_filter": type_filter,
        "report_types": ReportType.choices,
    }
    ctx.update(_sidebar_counts())
    return render(request, "admin_panel/admin_reports.html", ctx)
 
 
@admin_panel_login_required
@require_POST
def admin_resolve_report(request, report_id):
    report = get_object_or_404(Report, id=report_id)
    report.status = ReportStatus.RESOLVED
    report.save(update_fields=["status"])
    _log(
        _get_current_admin(request),
        "resolved_report",
        "report",
        report.id,
        str(report.opportunity),
    )
    return redirect("admin_reports")
 
 
# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------
 
@admin_panel_login_required
def admin_audit_log(request):
    qs = AuditLog.objects.select_related("admin").order_by("-timestamp")
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
 
    ctx = {
        "admin": _get_current_admin(request),
        "page_obj": page_obj,
    }
    ctx.update(_sidebar_counts())
    return render(request, "admin_panel/admin_audit_log.html", ctx)
 
 
# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
 
@admin_panel_login_required
def admin_analytics(request):
    total_submissions = Submission.objects.count()
    validated_subs = SubValidation.objects.values("submission").distinct().count()

    total_opportunities = Opportunity.objects.count()
    observer_added = Opportunity.objects.filter(ai_checks__isnull=False).distinct().count()
    student_approved = Opportunity.objects.filter(
        submissions__submitted_by_type=SubmitterType.STUDENT,
        submissions__status=SubmissionStatus.APPROVED,
    ).distinct().count()

    open_opps   = Opportunity.objects.filter(status=OpportunityStatus.OPEN).count()
    closed_opps = Opportunity.objects.filter(status=OpportunityStatus.CLOSED).count()

    ctx = {
        "admin": _get_current_admin(request),
        "open_opportunities":   open_opps,
        "closed_opportunities": closed_opps,
        "opps_by_category": (
            Opportunity.objects
            .values("category__name")
            .annotate(count=Count("id"))
            .order_by("-count")
        ),
        "subs_pending": Submission.objects.filter(status=SubmissionStatus.PENDING).count(),
        "subs_approved": Submission.objects.filter(status=SubmissionStatus.APPROVED).count(),
        "subs_rejected": Submission.objects.filter(status=SubmissionStatus.REJECTED).count(),
        "reports_by_type": (
            Report.objects.values("report_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        ),
        "total_students": Student.objects.count(),
        "total_opportunities": total_opportunities,
        "total_submissions": total_submissions,
        "validated_subs": validated_subs,
        "unvalidated_subs": total_submissions - validated_subs,
        "observer_added": observer_added,
        "student_approved": student_approved,
        "admin_added": total_opportunities - observer_added - student_approved,
    }
    ctx.update(_sidebar_counts())
    return render(request, "admin_panel/admin_analytics.html", ctx)
 
 
# ---------------------------------------------------------------------------
# AI Validations
# ---------------------------------------------------------------------------
 
def _parse_ai_note(note):
    """Extract (confidence_pct_int, reason) from an AI-generated SubValidation note."""
    if not note:
        return None, ""
    # Only parse the first line — the rest is the raw step report
    first_line = note.split("\n")[0]
    conf_match = re.search(r"Confidence:\s*(\d+)%", first_line)
    confidence = int(conf_match.group(1)) if conf_match else None
    reason_match = re.search(r"Confidence:\s*\d+%\.\s*(.+)$", first_line)
    reason = reason_match.group(1).strip() if reason_match else first_line
    return confidence, reason


def _parse_steps_from_note(note):
    """Parse the 4 validation steps out of a link_validator note string.

    Handles both formats:
      New: "  STEP N · NAME          ✓ APPROVED  " + "  Summary  :  <detail>"
      Old: "STEP N · NAME — ✓ APPROVED"            + "Detail: <detail>"
    """
    if not note:
        return []
    step_defs = [
        ("STEP 1 · SAFETY CHECK",        "Step 1 · Safety Check"),
        ("STEP 2 · AUTHENTICITY CHECK",  "Step 2 · Authenticity Check"),
        ("STEP 3 · AVAILABILITY CHECK",  "Step 3 · Availability Check"),
        ("STEP 4 · RELEVANCE CHECK",     "Step 4 · Relevance Check"),
    ]
    result = []
    for header, label in step_defs:
        m = re.search(re.escape(header) + r"[^\n]*", note)
        if not m:
            result.append({"name": label, "status": "skipped", "detail": "Not reached"})
            continue
        line = m.group(0)
        if "SKIPPED" in line.upper() or "(skipped)" in line.lower():
            status = "skipped"
        elif "✓" in line or "APPROVED" in line.upper():
            status = "approved"
        elif "⚠" in line or "FLAGGED" in line.upper():
            status = "flagged"
        elif "✗" in line or "REJECTED" in line.upper():
            status = "rejected"
        else:
            status = "skipped"
        # Look for detail in the 600 chars following this line
        tail = note[m.end():m.end() + 600]
        detail_m = re.search(r"Summary\s*:\s*([^\n]+)", tail)
        if not detail_m:
            detail_m = re.search(r"Detail:\s*([^\n]+)", tail)
        detail = detail_m.group(1).strip() if detail_m else ""
        result.append({"name": label, "status": status, "detail": detail})
    return result
 
 
@admin_panel_login_required
def admin_ai_validations(request):
    result_filter = (request.GET.get("result") or "").strip()
 
    qs = (
        SubValidation.objects
        .filter(admin__isnull=True, note__startswith="AI")
        .select_related(
            "submission__opportunity__category",
            "submission__submitted_by_student__user",
            "submission__submitted_by_admin",
        )
        .order_by("-validated_at")
    )

    if result_filter in ("pass", "fail", "pending"):
        qs = qs.filter(result=result_filter)

    # Annotate each record with parsed confidence + reason
    rows = []
    for sv in qs:
        confidence, reason = _parse_ai_note(sv.note)
        sub = sv.submission
        stype = sub.submitted_by_type
        if stype == "ai":
            submitter_label = "AI Fetch"
        elif stype == "admin" and sub.submitted_by_admin:
            submitter_label = sub.submitted_by_admin.display_name
        elif stype == "student" and sub.submitted_by_student:
            submitter_label = sub.submitted_by_student.user.username
        else:
            submitter_label = "—"
        opp = sv.submission.opportunity
        rows.append({
            "sv": sv,
            "opp": opp,
            "student": sv.submission.submitted_by_student,
            "submitter_label": submitter_label,
            "submitted_at": sv.submission.submitted_at,
            "confidence": confidence,
            "reason": reason,
            "source_link": opp.source_link or "",
            "steps_json": json.dumps(_parse_steps_from_note(sv.note)),
        })
 
    total       = SubValidation.objects.filter(admin__isnull=True, note__startswith="AI").count()
    total_pass  = SubValidation.objects.filter(admin__isnull=True, note__startswith="AI", result="pass").count()
    total_fail  = SubValidation.objects.filter(admin__isnull=True, note__startswith="AI", result="fail").count()
    total_unsure = SubValidation.objects.filter(admin__isnull=True, note__startswith="AI", result="pending").count()
 
    ctx = {
        "admin": _get_current_admin(request),
        "rows": rows,
        "result_filter": result_filter,
        "total": total,
        "total_pass": total_pass,
        "total_fail": total_fail,
        "total_unsure": total_unsure,
        "monitor_sessions":    MonitorSession.objects.all(),
        "expand_session_id":   request.GET.get("expand"),
        "monitor_schedule":    MonitorSchedule.objects.first(),
        "day_names":           ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    }
    ctx.update(_sidebar_counts())
    return render(request, "admin_panel/admin_ai_validations.html", ctx)


# ---------------------------------------------------------------------------
# Opportunity Status Monitor
# ---------------------------------------------------------------------------

@require_POST
@admin_panel_login_required
def admin_monitor_opportunities(request):
    """
    Runs Step 3 (availability) on all OPEN opportunities.
    Updates cache with live progress so the polling endpoint can serve it.
    Saves results to MonitorSession / MonitorResult, then redirects with ?expand=.
    """
    current_admin = _get_current_admin(request)
    admin_id = current_admin.id if current_admin else "anon"
    cache_key = f"monitor_progress_{admin_id}"

    open_opps = list(
        Opportunity.objects
        .filter(status=OpportunityStatus.OPEN)
        .exclude(source_link__isnull=True)
        .exclude(source_link="")
    )
    total = len(open_opps)
    cache.set(cache_key, {"current": 0, "total": total, "running": True}, timeout=600)

    db_results = []

    for idx, opp in enumerate(open_opps, start=1):
        cache.set(cache_key, {"current": idx, "total": total, "running": True}, timeout=600)

        decision = "error"
        reason   = "Validation error — could not reach the validator."

        try:
            result = link_validate_submission(
                url=opp.source_link,
                title=opp.title or "",
                company=opp.company or "",
                description=opp.description or "",
                skip_to_step=3,
                stop_after_step=3,
            )
            fs     = result["final_status"]
            step3  = (result.get("steps") or {}).get("step3_availability") or {}
            reason = step3.get("detail") or ""

            if fs == "rejected":
                opp.status = OpportunityStatus.CLOSED
                opp.save(update_fields=["status"])
                AuditLog.objects.create(
                    admin=current_admin,
                    action="auto_closed",
                    target_type="Opportunity",
                    target_id=opp.pk,
                    target_label=opp.title,
                    note="Opportunity auto-closed by Status Monitor",
                )
                decision = "auto_closed"
                reason   = reason or "Opportunity appears closed."
            else:
                decision = "still_open"
                reason   = reason or "Opportunity still available."

        except Exception as exc:
            reason = f"Error: {exc}"

        db_results.append({"opp": opp, "decision": decision, "reason": reason})

    cache.delete(cache_key)

    closed  = sum(1 for r in db_results if r["decision"] == "auto_closed")
    errors  = sum(1 for r in db_results if r["decision"] == "error")

    session_obj = MonitorSession.objects.create(
        checked=total, auto_closed=closed, errors=errors
    )
    MonitorResult.objects.bulk_create([
        MonitorResult(
            session=session_obj,
            opportunity=r["opp"],
            ai_decision=r["decision"],
            reason=r["reason"],
        )
        for r in db_results
    ])

    response = redirect(reverse("admin_ai_validations") + f"?expand={session_obj.id}")
    return response


@admin_panel_login_required
def admin_monitor_progress(request):
    """Returns live progress JSON for the polling counter."""
    current_admin = _get_current_admin(request)
    admin_id = current_admin.id if current_admin else "anon"
    data = cache.get(f"monitor_progress_{admin_id}", {"current": 0, "total": 0, "running": False})
    return JsonResponse(data)


@admin_panel_login_required
def admin_monitor_session_results(request, session_id):
    """Returns the results table partial for a given MonitorSession."""
    session_obj = get_object_or_404(MonitorSession, pk=session_id)
    results = session_obj.results.select_related("opportunity")
    return render(request, "admin_panel/partials/_monitor_results.html", {
        "session": session_obj,
        "results": results,
    })


@admin_panel_login_required
def admin_save_monitor_schedule(request):
    if request.method != "POST":
        return redirect("admin_ai_validations")

    from .scheduler import reload_monitor_schedule, _update_monitor_next_run

    schedule = MonitorSchedule.objects.first()
    if not schedule:
        schedule = MonitorSchedule()

    schedule.enabled = request.POST.get("enabled") == "on"
    schedule.frequency = request.POST.get("frequency", "daily")

    raw_dow = request.POST.get("day_of_week")
    schedule.day_of_week = int(raw_dow) if raw_dow is not None and raw_dow != "" else 0

    raw_time = request.POST.get("run_at_time", "09:00")
    from datetime import time as _time
    try:
        parts = raw_time.split(":")
        schedule.run_at_time = _time(int(parts[0]), int(parts[1]))
    except Exception:
        schedule.run_at_time = _time(9, 0)

    schedule.save()

    if schedule.enabled:
        _update_monitor_next_run(schedule)
        schedule.save(update_fields=["next_run"])

    reload_monitor_schedule()

    messages.success(request, "Monitor schedule saved successfully.")
    return redirect("admin_ai_validations")


# ---------------------------------------------------------------------------
# Opportunity Observer — Fetch Sessions
# ---------------------------------------------------------------------------
 
@admin_panel_login_required
def admin_fetch_opportunities(request):
    sessions = FetchSession.objects.select_related("initiated_by").order_by("-started_at")
    schedule = FetchSchedule.objects.first()
    ctx = {
        "admin": _get_current_admin(request),
        "sessions": sessions,
        "serper_configured": bool(settings.SERPER_API_KEY),
        "schedule": schedule,
        "day_names": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    }
    ctx.update(_sidebar_counts())
    return render(request, "admin_panel/admin_fetch_opportunities.html", ctx)


@admin_panel_login_required
def admin_save_schedule(request):
    if request.method != "POST":
        return redirect("admin_fetch_opportunities")

    from .scheduler import reload_schedule

    schedule = FetchSchedule.objects.first()
    if not schedule:
        schedule = FetchSchedule()

    schedule.enabled = request.POST.get("enabled") == "on"
    schedule.frequency = request.POST.get("frequency", "daily")

    raw_dow = request.POST.get("day_of_week")
    schedule.day_of_week = int(raw_dow) if raw_dow is not None and raw_dow != "" else 0

    raw_time = request.POST.get("run_at_time", "09:00")
    from datetime import time as _time
    try:
        parts = raw_time.split(":")
        schedule.run_at_time = _time(int(parts[0]), int(parts[1]))
    except Exception:
        schedule.run_at_time = _time(9, 0)

    schedule.save()

    # Recalculate next_run and reload the live scheduler
    if schedule.enabled:
        from .scheduler import _update_next_run
        _update_next_run(schedule)
        schedule.save(update_fields=["next_run"])

    reload_schedule()

    messages.success(request, "Schedule saved successfully.")
    return redirect("admin_fetch_opportunities")
 
 
@admin_panel_login_required
def admin_run_fetch(request):
    if request.method != "POST":
        return redirect("admin_fetch_opportunities")
    admin = _get_current_admin(request)
    session = run_fetch_session(admin)
    messages.success(
        request,
        f"✅ Fetch Session #{session.id} completed. "
        f"Added {session.total_added} new opportunities.",
    )
    return redirect("admin_fetch_opportunities")
 
 
@admin_panel_login_required
def admin_scheduler_status(request):
    """Returns live scheduler state from the running server process."""
    from .scheduler import _scheduler, _started
    running = _scheduler.running
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id":           job.id,
            "next_run":     str(job.next_run_time) if job.next_run_time else None,
        })
    return JsonResponse({"started": _started, "running": running, "jobs": jobs})


@admin_panel_login_required
def admin_fetch_session_detail(request, session_id):
    session = get_object_or_404(FetchSession, id=session_id)
    discoveries = AIDiscovery.objects.filter(
        fetch_session=session
    ).order_by("fetched_at")
    grouped = defaultdict(list)
    for d in discoveries:
        grouped[d.query].append(d)
    ctx = {
        "admin": _get_current_admin(request),
        "session": session,
        "grouped": dict(grouped),
    }
    ctx.update(_sidebar_counts())
    return render(request, "admin_panel/admin_fetch_session_detail.html", ctx)