from datetime import date
from django.core.paginator import Paginator
from django.urls import reverse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout, update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Count, Avg, Min
from django.utils import timezone
from django.views.decorators.http import require_POST
from .link_validator import validate_submission as link_validate_submission, build_validation_note
from .forms import SignupForm, LoginForm, ReportForm, RatingForm, SubmitOpportunityForm
from .models import (
    Student, Opportunity, OppCategory, Bookmark,
    Submission, SubmissionStatus, SubmitterType, OpportunityStatus,
    SubValidation, ValidationResult,
    Leaderboard, LeaderboardEntry,
    Rating, Report, ReportType,
)

User = get_user_model()

AI_AUTO_APPROVE_THRESHOLD = 0.75

REGIONS_AND_CITIES = {
    "Riyadh": ["Riyadh", "Al Kharj", "Al Majma'ah", "Al Quway'iyah"],
    "Makkah": ["Jeddah", "Makkah", "Taif", "Rabigh"],
    "Eastern Province": ["Dammam", "Khobar", "Dhahran", "Jubail"],
    "Madinah": ["Madinah", "Yanbu", "Al Ula", "Badr"],
    "Qassim": ["Buraidah", "Unaizah", "Al Rass", "Bukayriyah"],
    "Asir": ["Abha", "Khamis Mushait", "Mahayel", "Bisha"],
    "Tabuk": ["Tabuk", "Duba", "Umluj", "Tayma"],
    "Hail": ["Hail", "Baqaa", "Ash Shinan", "Ghazalah"],
    "Jazan": ["Jazan", "Sabya", "Abu Arish", "Al Ardah"],
    "Najran": ["Najran", "Sharurah", "Hubuna"],
    "Al Bahah": ["Al Bahah", "Baljurashi", "Al Mandaq"],
    "Al Jawf": ["Sakaka", "Dumat Al Jandal", "Qurayyat"],
    "Northern Borders": ["Arar", "Rafha", "Turaif"],
}


def hello(request):
    student_profile = Student.objects.filter(user=request.user).first() if request.user.is_authenticated else None
    return render(request, "pages/homepage.html", {"student_profile": student_profile})


def register_view(request):
    signup_form = SignupForm()
    login_form = LoginForm()
    active_tab = "signup"

    if request.method == "POST":
        form_type = request.POST.get("form_type")

        if form_type == "signup":
            active_tab = "signup"
            signup_form = SignupForm(request.POST)
            if signup_form.is_valid():
                user = signup_form.save()
                Student.objects.get_or_create(
                    user=user,
                    defaults={
                        "full_name": getattr(user, "username", ""),
                        "major": "",
                    },
                )
                auth_login(request, user)
                return redirect("opportunities")
            else:
                messages.error(request, "Please fix the signup errors.")

        elif form_type == "login":
            active_tab = "login"
            login_form = LoginForm(request.POST)
            if login_form.is_valid():
                user = login_form.cleaned_data["user"] 
                auth_login(request, user)
                return redirect("opportunities")
            else:
                messages.error(request, "Please fix the login errors.")

    return render(
        request,
        "pages/register.html",
        {
            "signup_form": signup_form,
            "login_form": login_form,
            "active_tab": active_tab,
        },
    )


def about_us(request):
    return render(request, "pages/about-us.html")


def contact_us(request):
    return render(request, "pages/contact-us.html")


def terms_of_service(request):
    return render(request, "pages/terms-of-service.html")


def privacy_policy(request):
    return render(request, "pages/privacy-policy.html")


@login_required
def bookmarks(request):
    student = Student.objects.filter(user=request.user).first()
    bookmarks_qs = (
        Bookmark.objects.filter(student=student)
        .select_related("opportunity")
        .order_by("-created_at")
    )
    return render(
        request,
        "pages/bookmarks.html",
        {
            "student_profile": student,
            "bookmarks": bookmarks_qs,
            "bookmarks_count": bookmarks_qs.count(),
        },
    )


@login_required
@require_POST
def toggle_bookmark(request, opp_id):
    student = get_object_or_404(Student, user=request.user)
    opp = get_object_or_404(Opportunity, id=opp_id)
    obj, created = Bookmark.objects.get_or_create(student=student, opportunity=opp)
    if not created:
        obj.delete()
        is_bookmarked = False
    else:
        is_bookmarked = True

    if request.headers.get("HX-Request"):
        return render(request, "pages/partials/_bookmark_btn.html", {
            "opp": opp,
            "is_bookmarked": is_bookmarked,
        })
    return redirect(request.META.get("HTTP_REFERER", "/opportunities/"))


def opp_detail(request, opp_id):
    opp = get_object_or_404(Opportunity, id=opp_id)
    is_bookmarked = False
    existing_rating = None
    already_reported = False

    if request.user.is_authenticated:
        student = Student.objects.filter(user=request.user).first()
        if student:
            is_bookmarked = Bookmark.objects.filter(
                student=student, opportunity=opp).exists()
            existing_rating = Rating.objects.filter(
                student=student, opportunity=opp).first()
            already_reported = Report.objects.filter(
                student=student, opportunity=opp).exists()

    today = date.today()
    days_left = (opp.deadline - today).days if opp.deadline else None
    deadline_warning = days_left is not None and days_left <= 3
    deadline_passed = days_left is not None and days_left < 0

    return render(request, "pages/partials/_opp_detail.html", {
        "opp": opp,
        "is_bookmarked": is_bookmarked,
        "existing_rating": existing_rating,
        "already_reported": already_reported,
        "deadline_warning": deadline_warning,
        "deadline_passed": deadline_passed,
        "days_left": days_left,
        "star_values": [5, 4, 3, 2, 1],
    })


@login_required
@require_POST
def report_opportunity(request, opp_id):
    opp = get_object_or_404(Opportunity, id=opp_id)
    student = get_object_or_404(Student, user=request.user)

    if Report.objects.filter(student=student, opportunity=opp).exists():
        messages.warning(request, "You have already reported this opportunity.")
        return redirect("opportunities")

    form = ReportForm(request.POST)
    if form.is_valid():
        Report.objects.create(
            student=student,
            opportunity=opp,
            report_type=form.cleaned_data["report_type"],
            description=form.cleaned_data.get("description") or "",
        )
        messages.success(
            request,
            "Thank you, your report has been submitted and will be reviewed by our team.",
        )
    return redirect("opportunities")


@login_required
@require_POST
def rate_opportunity(request, opp_id):
    opp = get_object_or_404(Opportunity, id=opp_id)
    student = get_object_or_404(Student, user=request.user)

    form = RatingForm(request.POST)
    if form.is_valid():
        lv = int(form.cleaned_data["learning_value"])
        we = int(form.cleaned_data["work_env"])
        me = int(form.cleaned_data["mentorship"])
        ou = int(form.cleaned_data["outcome"])
        overall = round((lv + we + me + ou) / 4, 2)

        _, created = Rating.objects.update_or_create(
            student=student,
            opportunity=opp,
            defaults={
                "learning_value": lv,
                "work_env": we,
                "mentorship": me,
                "outcome": ou,
                "overall": overall,
            },
        )

        new_avg = Rating.objects.filter(opportunity=opp).aggregate(
            avg=Avg("overall"))["avg"]
        opp.avg_rating = round(new_avg, 2) if new_avg else None
        opp.save(update_fields=["avg_rating"])

        msg = "Rating updated." if not created else "Thank you for rating this opportunity!"
        messages.success(request, msg)

    return redirect("opportunities")


@login_required
def profile(request):
    from urllib.parse import urlparse

    student = Student.objects.filter(user=request.user).first()
    if not student:
        student = Student.objects.create(user=request.user)

    field_errors: dict[str, str] = {}
    form_data: dict[str, str] = {}

    if request.method == "POST":
        import re

        # --- Avatar removal (handled separately, no other fields needed) ---
        if request.POST.get("remove_avatar"):
            if student.avatar:
                student.avatar.delete(save=False)
                student.avatar = None
                student.save(update_fields=["avatar"])
            messages.success(request, "Avatar removed.")
            return redirect("profile")

        username = (request.POST.get("username") or "").strip()
        avatar = request.FILES.get("avatar")
        email = (request.POST.get("email") or "").strip()
        full_name = (request.POST.get("full_name") or "").strip()
        phone_num = (request.POST.get("phone_num") or "").strip()
        major = (request.POST.get("major") or "").strip()
        linkedin_url = (request.POST.get("linkedin_url") or "").strip()
        gender = (request.POST.get("gender") or "").strip()
        password1 = request.POST.get("password1") or ""
        password2 = request.POST.get("password2") or ""

        has_error = False

        # --- Username ---
        if username:
            if (
                username != request.user.username
                and User.objects.filter(username=username).exists()
            ):
                field_errors["username"] = (
                    "This username is already taken. Please choose another one."
                )
                has_error = True
            else:
                request.user.username = username

        # --- Email ---
        if email:
            if (
                email != request.user.email
                and User.objects.filter(email=email).exists()
            ):
                field_errors["email"] = "This email is already registered."
                has_error = True
            else:
                request.user.email = email

        # --- LinkedIn URL ---
        if linkedin_url:
            check_url = (
                linkedin_url if "://" in linkedin_url else "https://" + linkedin_url
            )
            try:
                netloc = urlparse(check_url).netloc.lower()
                # Allow linkedin.com and any subdomain (e.g. www.linkedin.com, ae.linkedin.com)
                if not (netloc == "linkedin.com" or netloc.endswith(".linkedin.com")):
                    field_errors["linkedin_url"] = (
                        "Please enter a valid LinkedIn URL (e.g. https://linkedin.com/in/yourname)."
                    )
                    has_error = True
                else:
                    student.linkedin_url = linkedin_url
            except Exception:
                field_errors["linkedin_url"] = "Please enter a valid LinkedIn URL."
                has_error = True
        else:
            # Empty field — clear the stored URL if user submitted an empty value
            # (only when explicitly submitted, not on GET)
            pass  # don't clear unless they explicitly want to remove it

        # --- Phone number ---
        if phone_num:
            digits_only = re.sub(r"[\s\-\(\)]", "", phone_num)
            # Allow optional leading +, then 7–15 digits (E.164 max is 15)
            if not re.fullmatch(r"\+?\d{7,15}", digits_only):
                field_errors["phone_num"] = (
                    "Enter a valid phone number (7–15 digits, e.g. +966 50 123 4567)."
                )
                has_error = True
            else:
                student.phone_num = phone_num

        # --- Password ---
        if password1 or password2:
            if password1 != password2:
                messages.error(request, "Passwords do not match.")
                has_error = True
            else:
                try:
                    validate_password(password1, request.user)
                    request.user.set_password(password1)
                except ValidationError as e:
                    for err in e.messages:
                        messages.error(request, err)
                    has_error = True

        # --- Other fields (no validation needed) ---
        if full_name:
            student.full_name = full_name
        if major:
            student.major = major
        if gender:
            student.gender = gender
        if avatar:
            student.avatar = avatar

        if not has_error:
            request.user.save()
            student.save()
            if password1:
                update_session_auth_hash(request, request.user)
            messages.success(request, "Profile updated successfully.")
            return redirect("profile")

        # Preserve typed values so the form re-opens with what the user entered
        form_data = {
            "username": username,
            "email": email,
            "full_name": full_name,
            "phone_num": phone_num,
            "linkedin_url": linkedin_url,
            "major": major,
        }

    majors = [
        "Computer Science & IT",
        "Cybersecurity",
        "Software Engineering",
        "Data & AI",
        "Information Systems",
        "Engineering",
        "Architecture & Planning",
        "Design (UI/UX & Graphic)",
        "Business & Management",
        "Finance",
        "Accounting",
        "Marketing",
        "Media & Communications",
        "Law",
        "Shariah & Islamic Studies",
        "Education",
        "Healthcare",
        "Pharmacy",
        "Agriculture & Environmental",
        "Arts & Humanities",
    ]

    return render(
        request,
        "pages/profile.html",
        {
            "student_profile": student,
            "student": student,
            "majors": majors,
            "field_errors": field_errors,
            "form_data": form_data,
        },
    )


@login_required
def leaderboard(request):
    student_profile = Student.objects.filter(user=request.user).first()

    all_ranked = list(
        Student.objects.select_related("user")
        .annotate(
            approved_count=Count(
                "submissions",
                filter=Q(submissions__status=SubmissionStatus.APPROVED),
                distinct=True,
            ),
            first_approved=Min(
                "submissions__submitted_at",
                filter=Q(submissions__status=SubmissionStatus.APPROVED),
            ),
        )
        .order_by("-approved_count", "first_approved", "id")
    )

    total_active = sum(1 for s in all_ranked if s.approved_count > 0)
    total_approved = sum(s.approved_count for s in all_ranked)

    my_rank = None
    my_score = 0
    for index, student in enumerate(all_ranked, start=1):
        if student_profile and student.id == student_profile.id:
            my_rank = index
            my_score = student.approved_count
            break

    leaderboard_entries = [s for s in all_ranked if s.approved_count > 0]

    return render(
        request,
        "pages/leaderboard.html",
        {
            "student_profile": student_profile,
            "leaderboard_entries": leaderboard_entries,
            "total_active": total_active,
            "total_approved": total_approved,
            "my_rank": my_rank,
            "my_score": my_score,
        },
    )


@login_required
def opportunities(request):
    student_profile = Student.objects.filter(user=request.user).first()

    # Ensure all expected categories exist in the DB
    from .models import seed_default_categories
    seed_default_categories()

    qs = (
        Opportunity.objects
        .exclude(submissions__status__in=[SubmissionStatus.PENDING, SubmissionStatus.REJECTED])
        .order_by("-created_at", "-id")
    )

    # -----------------------------
    # Category filter (Major) - MULTI SELECT + COUNTS
    # -----------------------------
    raw_category_ids = request.GET.getlist("category")
    selected_category_ids: list[int] = []
    for x in raw_category_ids:
        try:
            selected_category_ids.append(int(x))
        except (TypeError, ValueError):
            continue
    selected_category_ids = sorted(set(selected_category_ids))

    categories = OppCategory.objects.all().order_by("name")

    qs_for_major_counts = qs

    if selected_category_ids:
        qs = qs.filter(category_id__in=selected_category_ids)

    major_counts = dict(
        qs_for_major_counts.values("category_id")
        .annotate(c=models.Count("id"))
        .values_list("category_id", "c")
    )

    def build_toggle_major_url(cat_id: int) -> str:
        params = request.GET.copy()
        params.pop("page", None)

        current = set(selected_category_ids)
        if cat_id in current:
            current.remove(cat_id)
        else:
            current.add(cat_id)

        params.setlist("category", [str(i) for i in sorted(current)])
        qs_str = params.urlencode()
        return f"{reverse('opportunities')}{('?' + qs_str) if qs_str else ''}"

    def build_clear_majors_url() -> str:
        params = request.GET.copy()
        params.pop("page", None)
        params.pop("category", None)
        qs_str = params.urlencode()
        return f"{reverse('opportunities')}{('?' + qs_str) if qs_str else ''}"

    PREFERRED_MAJOR_ORDER = [
        "Engineering",
        "Architecture & Planning",
        "Project Management",
        "Business & Management",
        "Finance",
        "Accounting",
        "Data & AI",
        "Information Systems",
        "Computer Science & IT",
        "Telecommunications",
        "Marketing",
        "Design (UI/UX & Graphic)",
        "Cybersecurity",
    ]
    preferred_index = {name: i for i, name in enumerate(PREFERRED_MAJOR_ORDER)}

    categories_ui = []
    for c in categories:
        categories_ui.append(
            {
                "id": c.id,
                "name": c.name,
                "count": int(major_counts.get(c.id, 0) or 0),
                "selected": c.id in selected_category_ids,
                "toggle_url": build_toggle_major_url(c.id),
            }
        )

    # Preferred majors first (in defined order), remaining ones alphabetically at the end
    categories_ui.sort(
        key=lambda c: (preferred_index.get(c["name"], len(PREFERRED_MAJOR_ORDER)), c["name"])
    )

    selected_category = None
    if len(selected_category_ids) == 1:
        selected_category = OppCategory.objects.filter(id=selected_category_ids[0]).first()

    category_id = (
        str(selected_category_ids[0]) if len(selected_category_ids) == 1 else None
    )

    selected_category_names = [c["name"] for c in categories_ui if c.get("selected")]

    # -----------------------------
    # Location filter (Region/City)
    # -----------------------------
    selected_region = (request.GET.get("region") or "").strip() or None
    selected_city = (request.GET.get("city") or "").strip() or None

    if selected_region and selected_region not in REGIONS_AND_CITIES:
        selected_region = None
        selected_city = None
    if (
        selected_region
        and selected_city
        and selected_city not in REGIONS_AND_CITIES.get(selected_region, [])
    ):
        selected_city = None

    base_for_counts = qs

    if selected_region and selected_city:
        qs = qs.filter(
            Q(location__iexact=f"{selected_region},{selected_city}")
            | Q(location__iexact=f"{selected_region}, {selected_city}")
        )
    elif selected_region:
        qs = qs.filter(
            Q(location__startswith=f"{selected_region},")
            | Q(location__startswith=f"{selected_region}, ")
        )

    counts: dict[str, dict[str, int]] = {
        r: {c: 0 for c in cities} for r, cities in REGIONS_AND_CITIES.items()
    }
    for loc in base_for_counts.values_list("location", flat=True):
        if not loc:
            continue
        parts = [p.strip() for p in str(loc).split(",", 1)]
        if len(parts) != 2:
            continue
        r, c = parts[0], parts[1]
        if r in counts and c in counts[r]:
            counts[r][c] += 1

    location_menu = {
        r: [{"city": c, "count": counts[r][c]} for c in REGIONS_AND_CITIES[r]]
        for r in REGIONS_AND_CITIES
    }

    # -----------------------------
    # Search filter
    # -----------------------------
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(company__icontains=q) | Q(description__icontains=q)
        )

    # -----------------------------
    # Status filter
    # -----------------------------
    selected_status = (request.GET.get("status") or "").strip() or None
    if selected_status not in ("open", "closed"):
        selected_status = None
    if selected_status:
        qs = qs.filter(status=selected_status)

    def build_status_url(status_val):
        p = request.GET.copy()
        p.pop("page", None)
        if status_val:
            p["status"] = status_val
        else:
            p.pop("status", None)
        s = p.urlencode()
        return f"{reverse('opportunities')}{('?' + s) if s else ''}"

    status_open_url = build_status_url("open")
    status_closed_url = build_status_url("closed")
    clear_status_url = build_status_url(None)

    # -----------------------------
    # Pagination
    # -----------------------------
    paginator = Paginator(qs, 5)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    current = page_obj.number
    last = paginator.num_pages
    window = 2

    pages = []
    for p in range(1, last + 1):
        if p == 1 or p == last or (current - window <= p <= current + window):
            pages.append(p)

    page_range = []
    prev = None
    for p in pages:
        if prev is not None and p - prev > 1:
            page_range.append(None)
        page_range.append(p)
        prev = p

    params = request.GET.copy()
    params.pop("page", None)
    qs_str = params.urlencode()
    extra_qs = ("&" + qs_str) if qs_str else ""

    clear_all_url = reverse("opportunities")
    clear_majors_url = build_clear_majors_url()

    params_clear_loc = request.GET.copy()
    params_clear_loc.pop("page", None)
    params_clear_loc.pop("region", None)
    params_clear_loc.pop("city", None)
    clear_loc_qs = params_clear_loc.urlencode()
    clear_location_url = (
        f"{reverse('opportunities')}{('?' + clear_loc_qs) if clear_loc_qs else ''}"
    )

    # Build a query-string of all active filters except page/region/city
    # (used by the location JS to preserve other filters when building location URLs)
    params_other = request.GET.copy()
    params_other.pop("page", None)
    params_other.pop("region", None)
    params_other.pop("city", None)
    majors_qs_str = params_other.urlencode()
    majors_qs = ("&" + majors_qs_str) if majors_qs_str else ""

    bookmarked_ids = set()
    if student_profile:
        bookmarked_ids = set(
            Bookmark.objects.filter(student=student_profile).values_list(
                "opportunity_id", flat=True
            )
        )

    return render(
        request,
        "pages/opportunities.html",
        {
            "student_profile": student_profile,
            "opportunities": page_obj.object_list,
            "bookmarked_ids": bookmarked_ids,
            "page_obj": page_obj,
            "page_range": page_range,
            "categories": categories,
            "categories_ui": categories_ui,
            "selected_category": selected_category,
            "selected_category_ids": selected_category_ids,
            "category_id": category_id,
            "selected_category_names": selected_category_names,
            "selected_category_count": len(selected_category_ids),
            "clear_majors_url": clear_majors_url,
            "clear_location_url": clear_location_url,
            "majors_qs": majors_qs,
            "location_menu": location_menu,
            "selected_region": selected_region,
            "selected_city": selected_city,
            "extra_qs": extra_qs,
            "clear_all_url": clear_all_url,
            "selected_status": selected_status,
            "status_open_url": status_open_url,
            "status_closed_url": status_closed_url,
            "clear_status_url": clear_status_url,
            "q": q,
        },
    )


@login_required
def dashboard(request):
    student_profile, _ = Student.objects.get_or_create(
        user=request.user,
        defaults={"full_name": request.user.get_username(), "major": ""},
    )

    open_opportunities_qs = (
        Opportunity.objects
        .filter(status=OpportunityStatus.OPEN)
        .exclude(submissions__status__in=[SubmissionStatus.PENDING, SubmissionStatus.REJECTED])
        .order_by("-created_at", "-id")
    )

    bookmark_qs = (
        Bookmark.objects.filter(student=student_profile)
        .select_related("opportunity")
        .order_by("-created_at")
    )

    ranked_students = list(
        Student.objects.select_related("user")
        .annotate(
            approved_count=Count(
                "submissions",
                filter=Q(submissions__status=SubmissionStatus.APPROVED),
                distinct=True,
            )
        )
        .order_by("-approved_count", "user__username", "id")
    )

    my_rank = None
    my_score = 0
    for index, student in enumerate(ranked_students, start=1):
        if student.id == student_profile.id:
            my_rank = index
            my_score = student.approved_count
            break

    return render(
        request,
        "pages/dashboard.html",
        {
            "student_profile": student_profile,
            "latest_open_opportunities": open_opportunities_qs[:5],
            "open_opportunities_count": open_opportunities_qs.count(),
            "bookmarks_count": bookmark_qs.count(),
            "my_rank": my_rank,
            "my_score": my_score,
            "top_contributors": ranked_students[:5],
        },
    )


@login_required(login_url="register")
def submit_opportunity(request):
    form = SubmitOpportunityForm()
    location_error = None
    selected_region = None
    selected_city = None

    if request.method == "POST":
        form = SubmitOpportunityForm(request.POST)
        selected_region = (request.POST.get("region") or "").strip() or None
        selected_city = (request.POST.get("city") or "").strip() or None

        # Validate region + city against the known REGIONS_AND_CITIES map
        if not selected_region or selected_region not in REGIONS_AND_CITIES:
            location_error = "Please select a region."
        elif not selected_city or selected_city not in REGIONS_AND_CITIES.get(selected_region, []):
            location_error = "Please select a city for the chosen region."

        if form.is_valid() and not location_error:
            location = f"{selected_region},{selected_city}"
            opp = Opportunity.objects.create(
                title=form.cleaned_data["title"],
                company=form.cleaned_data["company"],
                category=form.cleaned_data["category"],
                location=location,
                deadline=form.cleaned_data.get("deadline") or None,
                source_link=form.cleaned_data["source_link"],
                description=form.cleaned_data.get("description") or "",
                status=OpportunityStatus.OPEN,
            )
            student = get_object_or_404(Student, user=request.user)
            submission = Submission.objects.create(
                opportunity=opp,
                submitted_by_student=student,
                submitted_by_type=SubmitterType.STUDENT,
                status=SubmissionStatus.PENDING,
                link=form.cleaned_data["source_link"],
            )

            # Link validation
            link_result = link_validate_submission(
                url=str(form.cleaned_data.get("source_link", "") or ""),
                title=form.cleaned_data["title"],
                company=form.cleaned_data["company"],
                description=form.cleaned_data.get("description", ""),
            )

            _note = build_validation_note(
                link_result,
                url=str(form.cleaned_data.get("source_link", "") or ""),
                title=form.cleaned_data["title"],
                company=form.cleaned_data["company"],
            )

            if link_result["final_status"] == "approved":
                submission.status = SubmissionStatus.APPROVED
                submission.decision_at = timezone.now()
                submission.save()
                submission.opportunity.status = OpportunityStatus.OPEN
                submission.opportunity.save(update_fields=["status"])
                SubValidation.objects.create(
                    submission=submission,
                    result=ValidationResult.PASS,
                    note=_note,
                )
                if submission.submitted_by_student:
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
                messages.success(
                    request,
                    "✅ Your submission was reviewed and approved automatically! "
                    "It is now live on the platform.",
                    extra_tags="submission",
                )

            elif link_result["final_status"] == "rejected":
                submission.status = SubmissionStatus.REJECTED
                submission.decision_at = timezone.now()
                submission.save()
                SubValidation.objects.create(
                    submission=submission,
                    result=ValidationResult.FAIL,
                    note=_note,
                    failed_step=link_result.get("step"),
                )
                messages.error(
                    request,
                    "❌ Your submission was not accepted. The link did not pass our validation checks.",
                    extra_tags="submission",
                )

            else:
                SubValidation.objects.create(
                    submission=submission,
                    result=ValidationResult.PENDING,
                    note=_note,
                )
                messages.info(
                    request,
                    "⏳ Your submission is under review and will be published "
                    "once our team verifies it.",
                    extra_tags="submission",
                )

            request.session["submission_ai_result"] = link_result["final_status"]
            if link_result["final_status"] == "rejected":
                request.session["submission_ai_failed_step"] = link_result.get("step")
            return redirect("submission_success")

    import json
    return render(request, "pages/submit-opportunity.html", {
        "form": form,
        "regions_and_cities_json": json.dumps(REGIONS_AND_CITIES),
        "regions": list(REGIONS_AND_CITIES.keys()),
        "location_error": location_error,
        "selected_region": selected_region,
        "selected_city": selected_city,
    })


def submission_success(request):
    ai_result = request.session.pop("submission_ai_result", None)
    failed_step = request.session.pop("submission_ai_failed_step", None)
    return render(request, "pages/submission_success.html", {
        "ai_result": ai_result,
        "failed_step": failed_step,
    })


def faq(request):
    return render(request, "pages/faq.html")


@login_required
@require_POST
def delete_account(request):
    user = request.user
    auth_logout(request)   # end session before deleting
    user.delete()          # cascades → Student → Bookmark / Rating / Report
    return redirect("home")
