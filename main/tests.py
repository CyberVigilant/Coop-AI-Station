"""
Tests for the accounts app — models, forms, and views.
Run with:  python manage.py test accounts
"""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    Admin,
    Bookmark,
    Leaderboard,
    LeaderboardEntry,
    OppCategory,
    Opportunity,
    OpportunityStatus,
    Rating,
    Report,
    ReportType,
    Student,
    Submission,
    SubmissionStatus,
    SubmitterType,
    SubValidation,
    ValidationResult,
)
from .forms import (
    LoginForm,
    RatingForm,
    ReportForm,
    SignupForm,
    SubmitOpportunityForm,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(username="testuser", email="test@example.com", password="StrongPass123!"):
    user = User.objects.create_user(username=username, email=email, password=password)
    Student.objects.get_or_create(user=user, defaults={"full_name": "Test User"})
    return user


def make_category(name="Computer Science & IT"):
    cat, _ = OppCategory.objects.get_or_create(name=name)
    return cat


def make_opportunity(title="Test Opp", status=OpportunityStatus.OPEN, **kwargs):
    cat = make_category()
    return Opportunity.objects.create(
        title=title,
        company="Test Corp",
        category=cat,
        status=status,
        source_link="https://example.com/jobs/1",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Model Tests
# ---------------------------------------------------------------------------

class StudentModelTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="pass123"
        )
        self.student = Student.objects.create(user=self.user, full_name="Alice Smith")

    def test_display_name_uses_full_name_first(self):
        self.assertEqual(self.student.display_name, "Alice Smith")

    def test_display_name_falls_back_to_django_name(self):
        self.student.full_name = ""
        self.student.save()
        self.user.first_name = "Alice"
        self.user.last_name = "Smith"
        self.user.save()
        self.assertEqual(self.student.display_name, "Alice Smith")

    def test_display_name_falls_back_to_username(self):
        self.student.full_name = ""
        self.student.save()
        self.assertEqual(self.student.display_name, "alice")

    def test_email_property_reads_from_user(self):
        self.assertEqual(self.student.email, "alice@example.com")

    def test_str_returns_username(self):
        self.assertEqual(str(self.student), "alice")

    def test_student_created_at_auto_set(self):
        self.assertIsNotNone(self.student.created_at)


class AdminModelTest(TestCase):

    def test_display_name_full(self):
        admin = Admin(fname="John", lname="Doe", email="j@d.com", password="x")
        self.assertEqual(admin.display_name, "John Doe")

    def test_display_name_falls_back_to_email(self):
        admin = Admin(fname="", lname="", email="j@d.com", password="x")
        self.assertEqual(admin.display_name, "j@d.com")

    def test_str_returns_email(self):
        admin = Admin(fname="A", lname="B", email="a@b.com", password="x")
        self.assertEqual(str(admin), "a@b.com")

    def test_email_unique(self):
        Admin.objects.create(fname="A", lname="B", email="same@x.com", password="x")
        with self.assertRaises(Exception):
            Admin.objects.create(fname="C", lname="D", email="same@x.com", password="y")


class OppCategoryModelTest(TestCase):

    def test_str_returns_name(self):
        cat = OppCategory(name="Engineering")
        self.assertEqual(str(cat), "Engineering")

    def test_name_unique(self):
        OppCategory.objects.create(name="Unique Cat")
        with self.assertRaises(Exception):
            OppCategory.objects.create(name="Unique Cat")


class OpportunityModelTest(TestCase):

    def setUp(self):
        self.cat = make_category()

    def test_str_returns_title(self):
        opp = Opportunity(title="My Opp", category=self.cat)
        self.assertEqual(str(opp), "My Opp")

    def test_get_region_city_normal(self):
        opp = Opportunity(title="x", category=self.cat, location="Riyadh,Riyadh")
        self.assertEqual(opp.get_region_city(), ("Riyadh", "Riyadh"))

    def test_get_region_city_with_spaces(self):
        opp = Opportunity(title="x", category=self.cat, location="Makkah, Jeddah")
        self.assertEqual(opp.get_region_city(), ("Makkah", "Jeddah"))

    def test_get_region_city_no_location(self):
        opp = Opportunity(title="x", category=self.cat, location=None)
        self.assertEqual(opp.get_region_city(), (None, None))

    def test_get_region_city_only_region(self):
        opp = Opportunity(title="x", category=self.cat, location="Riyadh")
        self.assertEqual(opp.get_region_city(), ("Riyadh", None))

    def test_region_city_properties(self):
        opp = Opportunity(title="x", category=self.cat, location="Eastern Province,Dammam")
        self.assertEqual(opp.region, "Eastern Province")
        self.assertEqual(opp.city, "Dammam")

    def test_default_status_is_open(self):
        opp = make_opportunity()
        self.assertEqual(opp.status, OpportunityStatus.OPEN)


class BookmarkModelTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.student = Student.objects.get(user=self.user)
        self.opp = make_opportunity()

    def test_create_bookmark(self):
        b = Bookmark.objects.create(student=self.student, opportunity=self.opp)
        self.assertEqual(b.student, self.student)

    def test_bookmark_unique_constraint(self):
        Bookmark.objects.create(student=self.student, opportunity=self.opp)
        with self.assertRaises(Exception):
            Bookmark.objects.create(student=self.student, opportunity=self.opp)


class RatingModelTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.student = Student.objects.get(user=self.user)
        self.opp = make_opportunity()

    def test_create_rating(self):
        r = Rating.objects.create(
            student=self.student, opportunity=self.opp,
            learning_value=4, work_env=5, mentorship=3, outcome=4, overall=4.00
        )
        self.assertEqual(r.overall, 4.00)

    def test_rating_unique_per_student_opp(self):
        Rating.objects.create(student=self.student, opportunity=self.opp, overall=3)
        with self.assertRaises(Exception):
            Rating.objects.create(student=self.student, opportunity=self.opp, overall=4)


class ReportModelTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.student = Student.objects.get(user=self.user)
        self.opp = make_opportunity()

    def test_create_report(self):
        r = Report.objects.create(
            student=self.student, opportunity=self.opp,
            report_type=ReportType.BROKEN_LINK
        )
        self.assertEqual(r.report_type, ReportType.BROKEN_LINK)

    def test_report_unique_per_student_opp(self):
        Report.objects.create(
            student=self.student, opportunity=self.opp,
            report_type=ReportType.BROKEN_LINK
        )
        with self.assertRaises(Exception):
            Report.objects.create(
                student=self.student, opportunity=self.opp,
                report_type=ReportType.SCAM
            )


class SubmissionModelTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.student = Student.objects.get(user=self.user)
        self.opp = make_opportunity()

    def test_save_auto_sets_type_student(self):
        sub = Submission.objects.create(
            opportunity=self.opp, submitted_by_student=self.student
        )
        self.assertEqual(sub.submitted_by_type, SubmitterType.STUDENT)

    def test_save_auto_sets_type_admin(self):
        admin = Admin.objects.create(
            fname="A", lname="B", email="admin@test.com", password="x"
        )
        sub = Submission.objects.create(
            opportunity=self.opp, submitted_by_admin=admin
        )
        self.assertEqual(sub.submitted_by_type, SubmitterType.ADMIN)

    def test_clean_rejects_both_submitters(self):
        admin = Admin.objects.create(
            fname="A", lname="B", email="admin2@test.com", password="x"
        )
        sub = Submission(
            opportunity=self.opp,
            submitted_by_student=self.student,
            submitted_by_admin=admin,
        )
        with self.assertRaises(ValidationError):
            sub.clean()

    def test_clean_rejects_no_submitter(self):
        sub = Submission(opportunity=self.opp)
        with self.assertRaises(ValidationError):
            sub.clean()

    def test_default_status_is_pending(self):
        sub = Submission.objects.create(
            opportunity=self.opp, submitted_by_student=self.student
        )
        self.assertEqual(sub.status, SubmissionStatus.PENDING)


# ---------------------------------------------------------------------------
# Form Tests
# ---------------------------------------------------------------------------

class SignupFormTest(TestCase):

    def _valid_data(self, **overrides):
        data = {
            "full_name": "Jane Doe",
            "username": "janedoe",
            "email": "jane@example.com",
            "password1": "SuperSecure99!",
            "password2": "SuperSecure99!",
        }
        data.update(overrides)
        return data

    def test_valid_form(self):
        form = SignupForm(self._valid_data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_duplicate_username(self):
        User.objects.create_user(username="janedoe", email="other@x.com", password="x")
        form = SignupForm(self._valid_data())
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_duplicate_email(self):
        User.objects.create_user(username="other", email="jane@example.com", password="x")
        form = SignupForm(self._valid_data())
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_password_mismatch(self):
        form = SignupForm(self._valid_data(password2="Different99!"))
        self.assertFalse(form.is_valid())
        self.assertIn("password2", form.errors)

    def test_password_too_short(self):
        form = SignupForm(self._valid_data(password1="short", password2="short"))
        self.assertFalse(form.is_valid())

    def test_save_creates_user_and_student(self):
        form = SignupForm(self._valid_data())
        self.assertTrue(form.is_valid())
        user = form.save()
        self.assertIsNotNone(user.pk)
        self.assertTrue(Student.objects.filter(user=user).exists())

    def test_email_stored_lowercase(self):
        form = SignupForm(self._valid_data(email="Jane@EXAMPLE.COM"))
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["email"], "jane@example.com")

    def test_missing_required_field(self):
        data = self._valid_data()
        del data["username"]
        form = SignupForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)


class LoginFormTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="bob", email="bob@example.com", password="TestPass99!"
        )

    def test_login_by_username(self):
        form = LoginForm({"identifier": "bob", "password": "TestPass99!"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["user"], self.user)

    def test_login_by_email(self):
        form = LoginForm({"identifier": "bob@example.com", "password": "TestPass99!"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["user"], self.user)

    def test_wrong_password(self):
        form = LoginForm({"identifier": "bob", "password": "WrongPass!"})
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)

    def test_nonexistent_user(self):
        form = LoginForm({"identifier": "ghost", "password": "any"})
        self.assertFalse(form.is_valid())

    def test_email_case_insensitive(self):
        form = LoginForm({"identifier": "BOB@EXAMPLE.COM", "password": "TestPass99!"})
        self.assertTrue(form.is_valid(), form.errors)


class ReportFormTest(TestCase):

    def test_valid_with_description(self):
        form = ReportForm({"report_type": "broken_link", "description": "Link is dead"})
        self.assertTrue(form.is_valid())

    def test_valid_without_description(self):
        form = ReportForm({"report_type": "scam", "description": ""})
        self.assertTrue(form.is_valid())

    def test_invalid_report_type(self):
        form = ReportForm({"report_type": "not_a_type"})
        self.assertFalse(form.is_valid())

    def test_missing_report_type(self):
        form = ReportForm({"description": "Something is wrong"})
        self.assertFalse(form.is_valid())


class RatingFormTest(TestCase):

    def test_valid_all_fields(self):
        form = RatingForm({
            "learning_value": "5",
            "work_env": "4",
            "mentorship": "3",
            "outcome": "2",
        })
        self.assertTrue(form.is_valid())

    def test_invalid_out_of_range(self):
        form = RatingForm({
            "learning_value": "6",
            "work_env": "4",
            "mentorship": "3",
            "outcome": "2",
        })
        self.assertFalse(form.is_valid())

    def test_missing_field(self):
        form = RatingForm({
            "learning_value": "4",
            "work_env": "4",
            "mentorship": "3",
        })
        self.assertFalse(form.is_valid())


class SubmitOpportunityFormTest(TestCase):

    def setUp(self):
        self.cat = make_category("Software Engineering")

    def _valid_data(self, **overrides):
        data = {
            "title": "Backend Intern",
            "company": "Aramco",
            "category": self.cat.pk,
            "source_link": "https://aramco.com/jobs/1",
            "deadline": str(date.today() + timedelta(days=30)),
            "description": "A great opportunity.",
        }
        data.update(overrides)
        return data

    def test_valid_form(self):
        form = SubmitOpportunityForm(self._valid_data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_past_deadline_rejected(self):
        form = SubmitOpportunityForm(
            self._valid_data(deadline=str(date.today() - timedelta(days=1)))
        )
        self.assertFalse(form.is_valid())
        self.assertIn("deadline", form.errors)

    def test_missing_deadline_is_ok(self):
        form = SubmitOpportunityForm(self._valid_data(deadline=""))
        self.assertTrue(form.is_valid(), form.errors)

    def test_missing_required_title(self):
        data = self._valid_data()
        del data["title"]
        form = SubmitOpportunityForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)

    def test_invalid_source_link(self):
        form = SubmitOpportunityForm(self._valid_data(source_link="not-a-url"))
        self.assertFalse(form.is_valid())
        self.assertIn("source_link", form.errors)

    def test_description_optional(self):
        form = SubmitOpportunityForm(self._valid_data(description=""))
        self.assertTrue(form.is_valid(), form.errors)

    def test_today_deadline_is_valid(self):
        form = SubmitOpportunityForm(self._valid_data(deadline=str(date.today())))
        self.assertTrue(form.is_valid(), form.errors)


# ---------------------------------------------------------------------------
# View Tests
# ---------------------------------------------------------------------------

class PublicPageViewTests(TestCase):

    def test_homepage(self):
        r = self.client.get(reverse("home"))
        self.assertEqual(r.status_code, 200)

    def test_about_us(self):
        r = self.client.get(reverse("about_us"))
        self.assertEqual(r.status_code, 200)

    def test_contact_us(self):
        r = self.client.get(reverse("contact_us"))
        self.assertEqual(r.status_code, 200)

    def test_faq(self):
        r = self.client.get(reverse("faq"))
        self.assertEqual(r.status_code, 200)

    def test_terms_of_service(self):
        r = self.client.get(reverse("terms_of_service"))
        self.assertEqual(r.status_code, 200)

    def test_privacy_policy(self):
        r = self.client.get(reverse("privacy_policy"))
        self.assertEqual(r.status_code, 200)


class RegisterViewTest(TestCase):

    def test_get_returns_200(self):
        r = self.client.get(reverse("register"))
        self.assertEqual(r.status_code, 200)

    def test_signup_valid_creates_user_and_logs_in(self):
        r = self.client.post(reverse("register"), {
            "form_type": "signup",
            "full_name": "New User",
            "username": "newuser",
            "email": "new@example.com",
            "password1": "SuperSecure99!",
            "password2": "SuperSecure99!",
        })
        self.assertRedirects(r, reverse("opportunities"))
        self.assertTrue(User.objects.filter(username="newuser").exists())
        # Should be logged in after signup
        r2 = self.client.get(reverse("dashboard"))
        self.assertEqual(r2.status_code, 200)

    def test_signup_duplicate_username_shows_error(self):
        User.objects.create_user(username="taken", email="t@x.com", password="x")
        r = self.client.post(reverse("register"), {
            "form_type": "signup",
            "full_name": "Someone",
            "username": "taken",
            "email": "other@x.com",
            "password1": "SuperSecure99!",
            "password2": "SuperSecure99!",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.wsgi_request.user.is_authenticated)

    def test_signup_password_mismatch_shows_error(self):
        r = self.client.post(reverse("register"), {
            "form_type": "signup",
            "full_name": "X",
            "username": "xuser",
            "email": "x@example.com",
            "password1": "SuperSecure99!",
            "password2": "DifferentPass1!",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.wsgi_request.user.is_authenticated)

    def test_login_valid_by_username(self):
        User.objects.create_user(username="logme", email="lm@x.com", password="TestPass99!")
        r = self.client.post(reverse("register"), {
            "form_type": "login",
            "identifier": "logme",
            "password": "TestPass99!",
        })
        self.assertRedirects(r, reverse("opportunities"))

    def test_login_valid_by_email(self):
        User.objects.create_user(username="logme2", email="lm2@x.com", password="TestPass99!")
        r = self.client.post(reverse("register"), {
            "form_type": "login",
            "identifier": "lm2@x.com",
            "password": "TestPass99!",
        })
        self.assertRedirects(r, reverse("opportunities"))

    def test_login_wrong_password_stays_on_page(self):
        User.objects.create_user(username="logme3", email="lm3@x.com", password="Correct99!")
        r = self.client.post(reverse("register"), {
            "form_type": "login",
            "identifier": "logme3",
            "password": "Wrong99!",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.wsgi_request.user.is_authenticated)


class LoginRequiredViewsTest(TestCase):
    """Views that require login redirect to register when anonymous."""

    PROTECTED_URLS = [
        "dashboard",
        "bookmarks",
        "opportunities",
        "leaderboard",
        "profile",
        "submit_opportunity",
    ]

    def test_redirect_when_anonymous(self):
        for name in self.PROTECTED_URLS:
            with self.subTest(view=name):
                r = self.client.get(reverse(name))
                self.assertIn(r.status_code, [302, 301], msg=f"{name} should redirect")


class DashboardViewTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_get_returns_200(self):
        r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 200)

    def test_context_contains_student_profile(self):
        r = self.client.get(reverse("dashboard"))
        self.assertIn("student_profile", r.context)

    def test_context_contains_top_contributors(self):
        r = self.client.get(reverse("dashboard"))
        self.assertIn("top_contributors", r.context)

    def test_shows_open_opportunities(self):
        make_opportunity("Open Opp")
        r = self.client.get(reverse("dashboard"))
        self.assertIn("latest_open_opportunities", r.context)


class OpportunitiesViewTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.cat = make_category()

    def test_get_returns_200(self):
        r = self.client.get(reverse("opportunities"))
        self.assertEqual(r.status_code, 200)

    def test_search_filter(self):
        make_opportunity("Python Dev Role")
        make_opportunity("Marketing Intern")
        r = self.client.get(reverse("opportunities") + "?q=Python")
        titles = [o.title for o in r.context["opportunities"]]
        self.assertIn("Python Dev Role", titles)
        self.assertNotIn("Marketing Intern", titles)

    def test_status_filter_open(self):
        make_opportunity("Open Opp", status=OpportunityStatus.OPEN)
        make_opportunity("Closed Opp", status=OpportunityStatus.CLOSED)
        r = self.client.get(reverse("opportunities") + "?status=open")
        titles = [o.title for o in r.context["opportunities"]]
        self.assertIn("Open Opp", titles)
        self.assertNotIn("Closed Opp", titles)

    def test_category_filter(self):
        cat2 = OppCategory.objects.create(name="Finance")
        opp1 = make_opportunity("CS Opp")
        opp2 = Opportunity.objects.create(
            title="Finance Opp", company="Bank", category=cat2,
            source_link="https://bank.com/jobs/1"
        )
        r = self.client.get(reverse("opportunities") + f"?category={cat2.pk}")
        titles = [o.title for o in r.context["opportunities"]]
        self.assertIn("Finance Opp", titles)

    def test_pending_submissions_excluded(self):
        student = Student.objects.get(user=self.user)
        opp = make_opportunity("Pending Opp")
        Submission.objects.create(
            opportunity=opp,
            submitted_by_student=student,
            status=SubmissionStatus.PENDING,
        )
        r = self.client.get(reverse("opportunities"))
        titles = [o.title for o in r.context["opportunities"]]
        self.assertNotIn("Pending Opp", titles)

    def test_invalid_status_filter_ignored(self):
        r = self.client.get(reverse("opportunities") + "?status=invalid")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context["selected_status"])


class BookmarksViewTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.student = Student.objects.get(user=self.user)

    def test_get_returns_200(self):
        r = self.client.get(reverse("bookmarks"))
        self.assertEqual(r.status_code, 200)

    def test_shows_bookmarked_opportunities(self):
        opp = make_opportunity("Bookmarked Opp")
        Bookmark.objects.create(student=self.student, opportunity=opp)
        r = self.client.get(reverse("bookmarks"))
        self.assertEqual(r.context["bookmarks_count"], 1)


class ToggleBookmarkViewTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.student = Student.objects.get(user=self.user)
        self.opp = make_opportunity()

    def test_post_adds_bookmark(self):
        self.client.post(reverse("toggle_bookmark", args=[self.opp.pk]))
        self.assertTrue(Bookmark.objects.filter(student=self.student, opportunity=self.opp).exists())

    def test_post_removes_existing_bookmark(self):
        Bookmark.objects.create(student=self.student, opportunity=self.opp)
        self.client.post(reverse("toggle_bookmark", args=[self.opp.pk]))
        self.assertFalse(Bookmark.objects.filter(student=self.student, opportunity=self.opp).exists())

    def test_get_not_allowed(self):
        r = self.client.get(reverse("toggle_bookmark", args=[self.opp.pk]))
        self.assertEqual(r.status_code, 405)

    def test_anonymous_redirects(self):
        self.client.logout()
        r = self.client.post(reverse("toggle_bookmark", args=[self.opp.pk]))
        self.assertEqual(r.status_code, 302)


class OppDetailViewTest(TestCase):

    def setUp(self):
        self.opp = make_opportunity()

    def test_get_returns_200_anonymous(self):
        r = self.client.get(reverse("opp_detail", args=[self.opp.pk]))
        self.assertEqual(r.status_code, 200)

    def test_get_returns_200_logged_in(self):
        user = make_user()
        self.client.force_login(user)
        r = self.client.get(reverse("opp_detail", args=[self.opp.pk]))
        self.assertEqual(r.status_code, 200)

    def test_404_for_nonexistent_opp(self):
        r = self.client.get(reverse("opp_detail", args=[99999]))
        self.assertEqual(r.status_code, 404)

    def test_deadline_warning_flag_within_3_days(self):
        self.opp.deadline = date.today() + timedelta(days=2)
        self.opp.save()
        r = self.client.get(reverse("opp_detail", args=[self.opp.pk]))
        self.assertTrue(r.context["deadline_warning"])

    def test_deadline_passed_flag(self):
        self.opp.deadline = date.today() - timedelta(days=1)
        self.opp.save()
        r = self.client.get(reverse("opp_detail", args=[self.opp.pk]))
        self.assertTrue(r.context["deadline_passed"])

    def test_no_deadline_flags_when_none(self):
        r = self.client.get(reverse("opp_detail", args=[self.opp.pk]))
        self.assertIsNone(r.context["days_left"])
        self.assertFalse(r.context["deadline_warning"])
        self.assertFalse(r.context["deadline_passed"])

    def test_is_bookmarked_false_for_anonymous(self):
        r = self.client.get(reverse("opp_detail", args=[self.opp.pk]))
        self.assertFalse(r.context["is_bookmarked"])


class ReportOpportunityViewTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.student = Student.objects.get(user=self.user)
        self.opp = make_opportunity()

    def test_post_creates_report(self):
        self.client.post(
            reverse("report_opportunity", args=[self.opp.pk]),
            {"report_type": "broken_link", "description": "Link is dead"},
        )
        self.assertTrue(
            Report.objects.filter(student=self.student, opportunity=self.opp).exists()
        )

    def test_duplicate_report_blocked(self):
        Report.objects.create(
            student=self.student, opportunity=self.opp, report_type=ReportType.SCAM
        )
        self.client.post(
            reverse("report_opportunity", args=[self.opp.pk]),
            {"report_type": "broken_link"},
        )
        # Still only one report
        self.assertEqual(
            Report.objects.filter(student=self.student, opportunity=self.opp).count(), 1
        )

    def test_get_not_allowed(self):
        r = self.client.get(reverse("report_opportunity", args=[self.opp.pk]))
        self.assertEqual(r.status_code, 405)

    def test_anonymous_redirects(self):
        self.client.logout()
        r = self.client.post(
            reverse("report_opportunity", args=[self.opp.pk]),
            {"report_type": "scam"},
        )
        self.assertEqual(r.status_code, 302)


class RateOpportunityViewTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.student = Student.objects.get(user=self.user)
        self.opp = make_opportunity()

    def _post_rating(self, lv=4, we=4, me=4, ou=4):
        return self.client.post(
            reverse("rate_opportunity", args=[self.opp.pk]),
            {
                "learning_value": str(lv),
                "work_env": str(we),
                "mentorship": str(me),
                "outcome": str(ou),
            },
        )

    def test_post_creates_rating(self):
        self._post_rating(4, 4, 4, 4)
        self.assertTrue(
            Rating.objects.filter(student=self.student, opportunity=self.opp).exists()
        )

    def test_rating_overall_calculated_correctly(self):
        self._post_rating(4, 2, 4, 2)
        rating = Rating.objects.get(student=self.student, opportunity=self.opp)
        self.assertEqual(float(rating.overall), 3.0)

    def test_rating_updates_opp_avg(self):
        self._post_rating(4, 4, 4, 4)
        self.opp.refresh_from_db()
        self.assertEqual(float(self.opp.avg_rating), 4.0)

    def test_second_rating_updates_existing(self):
        self._post_rating(4, 4, 4, 4)
        self._post_rating(2, 2, 2, 2)
        self.assertEqual(Rating.objects.filter(student=self.student, opportunity=self.opp).count(), 1)
        rating = Rating.objects.get(student=self.student, opportunity=self.opp)
        self.assertEqual(float(rating.overall), 2.0)

    def test_get_not_allowed(self):
        r = self.client.get(reverse("rate_opportunity", args=[self.opp.pk]))
        self.assertEqual(r.status_code, 405)


class LeaderboardViewTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_get_returns_200(self):
        r = self.client.get(reverse("leaderboard"))
        self.assertEqual(r.status_code, 200)

    def test_context_keys_present(self):
        r = self.client.get(reverse("leaderboard"))
        for key in ("leaderboard_entries", "total_active", "total_approved", "my_rank", "my_score"):
            self.assertIn(key, r.context, msg=f"Missing context key: {key}")

    def test_student_with_approvals_appears_on_board(self):
        student = Student.objects.get(user=self.user)
        opp = make_opportunity()
        Submission.objects.create(
            opportunity=opp,
            submitted_by_student=student,
            status=SubmissionStatus.APPROVED,
        )
        r = self.client.get(reverse("leaderboard"))
        entries = r.context["leaderboard_entries"]
        self.assertTrue(any(e.id == student.id for e in entries))

    def test_student_with_no_approvals_not_on_board(self):
        r = self.client.get(reverse("leaderboard"))
        self.assertEqual(r.context["leaderboard_entries"], [])


class ProfileViewTest(TestCase):

    def setUp(self):
        self.user = make_user(username="profileuser", email="profile@example.com")
        self.client.force_login(self.user)

    def test_get_returns_200(self):
        r = self.client.get(reverse("profile"))
        self.assertEqual(r.status_code, 200)

    def test_post_updates_full_name(self):
        self.client.post(reverse("profile"), {"full_name": "Updated Name"})
        student = Student.objects.get(user=self.user)
        self.assertEqual(student.full_name, "Updated Name")

    def test_post_updates_username(self):
        r = self.client.post(reverse("profile"), {"username": "newusername"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "newusername")

    def test_post_duplicate_username_shows_error(self):
        User.objects.create_user(username="taken_name", email="t2@x.com", password="x")
        r = self.client.post(reverse("profile"), {"username": "taken_name"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("username", r.context["field_errors"])

    def test_post_invalid_linkedin_url(self):
        r = self.client.post(reverse("profile"), {"linkedin_url": "https://notlinkedin.com/in/x"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("linkedin_url", r.context["field_errors"])

    def test_post_valid_linkedin_url(self):
        r = self.client.post(reverse("profile"), {"linkedin_url": "https://linkedin.com/in/myprofile"})
        self.assertRedirects(r, reverse("profile"))
        student = Student.objects.get(user=self.user)
        self.assertEqual(student.linkedin_url, "https://linkedin.com/in/myprofile")

    def test_post_valid_phone_number(self):
        r = self.client.post(reverse("profile"), {"phone_num": "+966501234567"})
        self.assertRedirects(r, reverse("profile"))
        student = Student.objects.get(user=self.user)
        self.assertEqual(student.phone_num, "+966501234567")

    def test_post_invalid_phone_number(self):
        r = self.client.post(reverse("profile"), {"phone_num": "abc"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("phone_num", r.context["field_errors"])

    def test_post_password_mismatch_shows_error(self):
        r = self.client.post(reverse("profile"), {
            "password1": "NewPass99!",
            "password2": "DifferentPass99!",
        })
        self.assertEqual(r.status_code, 200)

    def test_post_same_username_no_error(self):
        r = self.client.post(reverse("profile"), {"username": "profileuser"})
        self.assertRedirects(r, reverse("profile"))


class DeleteAccountViewTest(TestCase):

    def test_post_deletes_user_and_redirects(self):
        user = make_user(username="todelete", email="del@x.com")
        self.client.force_login(user)
        r = self.client.post(reverse("delete_account"))
        self.assertRedirects(r, reverse("home"))
        self.assertFalse(User.objects.filter(username="todelete").exists())

    def test_post_cascades_to_student(self):
        user = make_user(username="todelete2", email="del2@x.com")
        self.client.force_login(user)
        self.client.post(reverse("delete_account"))
        self.assertFalse(Student.objects.filter(user=user).exists())

    def test_get_not_allowed(self):
        user = make_user(username="nodelete", email="nd@x.com")
        self.client.force_login(user)
        r = self.client.get(reverse("delete_account"))
        self.assertEqual(r.status_code, 405)

    def test_anonymous_redirects(self):
        r = self.client.post(reverse("delete_account"))
        self.assertEqual(r.status_code, 302)


class SubmitOpportunityViewTest(TestCase):
    """
    The submit view calls link_validate_submission() which hits external APIs.
    We mock it for all tests here.
    """

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.cat = make_category("Software Engineering")

    def _post_valid(self, final_status="approved"):
        with patch("accounts.views.link_validate_submission") as mock_validate, \
             patch("accounts.views.build_validation_note", return_value="note"):
            mock_validate.return_value = {
                "final_status": final_status,
                "steps": {},
            }
            return self.client.post(reverse("submit_opportunity"), {
                "title": "Backend Intern",
                "company": "TestCo",
                "category": self.cat.pk,
                "source_link": "https://testco.com/jobs/1",
                "deadline": str(date.today() + timedelta(days=30)),
                "description": "A great role.",
                "region": "Riyadh",
                "city": "Riyadh",
            })

    def test_get_returns_200(self):
        r = self.client.get(reverse("submit_opportunity"))
        self.assertEqual(r.status_code, 200)

    def test_approved_submission_creates_opp_and_redirects(self):
        r = self._post_valid(final_status="approved")
        self.assertRedirects(r, reverse("opportunities"))
        self.assertTrue(Opportunity.objects.filter(title="Backend Intern").exists())

    def test_approved_submission_status_is_approved(self):
        self._post_valid(final_status="approved")
        sub = Submission.objects.get(opportunity__title="Backend Intern")
        self.assertEqual(sub.status, SubmissionStatus.APPROVED)

    def test_flagged_submission_status_is_pending(self):
        self._post_valid(final_status="flagged")
        sub = Submission.objects.get(opportunity__title="Backend Intern")
        self.assertEqual(sub.status, SubmissionStatus.PENDING)

    def test_rejected_submission_status_is_rejected(self):
        with patch("accounts.views.link_validate_submission") as mock_validate, \
             patch("accounts.views.build_validation_note", return_value="note"):
            mock_validate.return_value = {"final_status": "rejected", "steps": {}}
            self.client.post(reverse("submit_opportunity"), {
                "title": "Bad Link Opp",
                "company": "ScamCo",
                "category": self.cat.pk,
                "source_link": "https://scam.com/jobs/1",
                "region": "Riyadh",
                "city": "Riyadh",
            })
        sub = Submission.objects.filter(opportunity__title="Bad Link Opp").first()
        if sub:
            self.assertEqual(sub.status, SubmissionStatus.REJECTED)

    def test_missing_region_shows_error(self):
        with patch("accounts.views.link_validate_submission"), \
             patch("accounts.views.build_validation_note", return_value=""):
            r = self.client.post(reverse("submit_opportunity"), {
                "title": "No Region Opp",
                "company": "Co",
                "category": self.cat.pk,
                "source_link": "https://co.com/jobs/1",
                "city": "Riyadh",
            })
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.context["location_error"])

    def test_approved_submission_leaderboard_entry_created(self):
        self._post_valid(final_status="approved")
        student = Student.objects.get(user=self.user)
        self.assertTrue(
            LeaderboardEntry.objects.filter(student=student).exists()
        )

    def test_anonymous_redirects(self):
        self.client.logout()
        r = self.client.get(reverse("submit_opportunity"))
        self.assertEqual(r.status_code, 302)
