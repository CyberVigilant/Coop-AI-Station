from __future__ import annotations

import random
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management import CommandError
from django.db import transaction, connection, models
from django.utils import timezone
from faker import Faker

from accounts.models import Student

# Optional models (your schema may or may not include them)
try:
    from accounts.models import Opportunity, OppCategory  # type: ignore
except Exception:  # pragma: no cover
    Opportunity = None  # type: ignore
    OppCategory = None  # type: ignore

try:
    from accounts.models import Admin  # type: ignore
except Exception:  # pragma: no cover
    Admin = None  # type: ignore

try:
    from accounts.models import OpportunityObserverAI  # type: ignore
except Exception:  # pragma: no cover
    OpportunityObserverAI = None  # type: ignore

# Optional: Submission model
try:
    from accounts.models import Submission  # type: ignore
except Exception:  # pragma: no cover
    Submission = None  # type: ignore

# Optional: Rating model
try:
    from accounts.models import Rating  # type: ignore
except Exception:  # pragma: no cover
    Rating = None  # type: ignore

# Optional: Report model
try:
    from accounts.models import Report  # type: ignore
except Exception:  # pragma: no cover
    Report = None  # type: ignore


User = get_user_model()

fake = Faker("ar_SA")

DEFAULT_MAJORS = [
    "Computer Science",
    "Information Technology",
    "Information Systems (MIS)",
    "Software Engineering",
    "Cybersecurity",
    "Data Science & Analytics",
    "Artificial Intelligence",
    "Business Administration",
    "Management",
    "Human Resources",
    "Marketing",
    "Finance",
    "Accounting",
    "Economics",
    "Law",
    "Shariah",
    "Electrical Engineering",
    "Mechanical Engineering",
    "Civil Engineering",
    "Industrial Engineering",
]


DEFAULT_OPP_CATEGORIES = [
    "Computer Science & IT",
    "Software Engineering",
    "Cybersecurity",
    "Data & AI",
    "Information Systems",
    "Business & Management",
    "Finance",
    "Accounting",
    "Marketing",
    "Engineering",
    "Design (UI/UX & Graphic)",
    "Architecture & Planning",
    "Law",
    "Shariah & Islamic Studies",
    "Healthcare",
    "Pharmacy",
    "Agriculture & Environmental",
    "Education",
    "Arts & Media",
    "Other",
]


REGIONS_AND_CITIES = {
    "Riyadh": ["Riyadh", "Diriyah", "Al Kharj"],
    "Makkah": ["Jeddah", "Makkah", "Taif"],
    "Eastern Province": ["Dammam", "Al Khobar", "Dhahran"],
    "Madinah": ["Madinah", "Yanbu"],
    "Qassim": ["Buraidah", "Unaizah"],
    "Asir": ["Abha", "Khamis Mushait"],
    "Tabuk": ["Tabuk"],
    "Hail": ["Hail"],
    "Jazan": ["Jazan"],
    "Najran": ["Najran"],
    "Al Bahah": ["Al Bahah"],
    "Al Jawf": ["Sakakah"],
    "Northern Borders": ["Arar"],
}


def make_saudi_mobile() -> str:
    # Saudi mobile format: +9665XXXXXXXX
    return "+9665" + "".join(str(random.randint(0, 9)) for _ in range(8))


def ensure_categories() -> dict[str, object]:
    """Ensure OppCategory rows exist, return mapping name->category_obj."""
    cat_map: dict[str, object] = {}
    if OppCategory is None:
        return cat_map

    for name in DEFAULT_OPP_CATEGORIES:
        obj, _ = OppCategory.objects.get_or_create(name=name)
        cat_map[name] = obj
    return cat_map



def pick_region_city() -> tuple[str, str]:
    region = random.choice(list(REGIONS_AND_CITIES.keys()))
    city = random.choice(REGIONS_AND_CITIES[region])
    return region, city


# Helper: Truncate model table (even if Python model is out of sync with DB)
def truncate_model_table(model) -> int:
    """TRUNCATE the model's table safely even if the Python model is out of sync with DB columns.

    Returns the number of rows *before* truncation (best-effort).
    """
    table = model._meta.db_table
    quoted = connection.ops.quote_name(table)

    # Best-effort count (does not touch ORM / model fields)
    before = 0
    try:
        with connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {quoted};")
            before = int(cur.fetchone()[0])
    except Exception:
        before = 0

    # TRUNCATE avoids ORM SELECT of non-existent columns (e.g. description_html)
    with connection.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE;")

    return before



def db_column_names_for_model(model) -> set[str]:
    """Return actual DB column names for this model's table (Postgres introspection)."""
    try:
        table = model._meta.db_table
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, table)
        return {col.name for col in description}
    except Exception:
        # If introspection fails, fall back to model fields only.
        return set()


def model_field_names(model) -> set[str]:
    """Return concrete model field names (excludes @property) AND present DB columns.

    This prevents seeding from failing when the Python model has fields that are not
    yet migrated to the database (e.g., description_html).
    """
    try:
        db_cols = db_column_names_for_model(model)
        names: set[str] = set()
        for f in model._meta.get_fields():
            if not getattr(f, "concrete", False):
                continue
            # f.name is the Python attribute, f.column is the actual DB column
            col = getattr(f, "column", None)
            if db_cols and col and col not in db_cols:
                continue
            names.add(f.name)
        return names
    except Exception:
        return set()


def has_model_field(model, field_name: str) -> bool:
    return field_name in model_field_names(model)



def set_if_field_exists(obj, field_name: str, value) -> bool:
    """Set obj.field_name=value only if it's a real Django model field (not @property)."""
    model = obj.__class__
    if not has_model_field(model, field_name):
        return False
    try:
        setattr(obj, field_name, value)
        return True
    except Exception:
        return False


# Helper to find ForeignKey field dynamically
def find_fk_field(model, target_model) -> tuple[str, str] | tuple[None, None]:
    """Return (field_name, attname) for FK from `model` to `target_model`.

    - field_name: Django attribute name (e.g. 'student')
    - attname: DB id attribute (e.g. 'student_id')
    """
    try:
        for f in model._meta.get_fields():
            if not getattr(f, "concrete", False):
                continue
            if isinstance(f, models.ForeignKey) and getattr(f.remote_field, "model", None) == target_model:
                return f.name, f.attname
    except Exception:
        pass
    return None, None


class Command(BaseCommand):
    help = "Seed DB with fake Students and/or Opportunities (choose what to create via CLI args)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing rows before inserting. By default deletes Students only. Use --reset-opps to delete Opportunities too.",
        )
        parser.add_argument(
            "--reset-opps",
            action="store_true",
            help="DANGER: When used with --reset, also DELETE/TRUNCATE Opportunities (and cascaded related rows).",
        )
        parser.add_argument(
            "--reset-subs",
            action="store_true",
            help="When used with --reset, also delete Submissions (TRUNCATE to avoid ORM schema mismatches).",
        )
        parser.add_argument(
            "--reset-ratings",
            action="store_true",
            help="When used with --reset, also delete Ratings (TRUNCATE to avoid ORM schema mismatches).",
        )
        parser.add_argument(
            "--reset-reports",
            action="store_true",
            help="When used with --reset, also delete Reports (TRUNCATE to avoid ORM schema mismatches).",
        )
        parser.add_argument(
            "--reset-students",
            action="store_true",
            help="When used with --reset, also delete Students (and cascaded related rows).",
        )
        parser.add_argument(
            "--students",
            type=int,
            default=20,
            help="How many students to create (default: 20). Use 0 to skip students.",
        )
        parser.add_argument(
            "--opps",
            type=int,
            default=0,
            help="How many opportunities to create (default: 0). Use 0 to skip opportunities.",
        )
        parser.add_argument(
            "--subs",
            type=int,
            default=0,
            help="How many submissions to create (default: 0). Use 0 to skip submissions.",
        )
        parser.add_argument(
            "--ratings",
            type=int,
            default=0,
            help="How many ratings to create (default: 0). Use 0 to skip ratings.",
        )
        parser.add_argument(
            "--reports",
            type=int,
            default=0,
            help="How many reports to create (default: 0). Use 0 to skip reports.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=None,
            help="Random seed for reproducible output",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("RUNNING accounts/management/commands/seed_all.py"))

        seed_value = options.get("seed")
        if seed_value is not None:
            Faker.seed(seed_value)
            random.seed(seed_value)

        # Debug: confirm which DB + table we write to
        self.stdout.write(
            self.style.NOTICE(
                f"DB NAME: {connection.settings_dict.get('NAME')}, HOST: {connection.settings_dict.get('HOST')}, PORT: {connection.settings_dict.get('PORT')}"
            )
        )
        self.stdout.write(self.style.NOTICE(f"Student table: {Student._meta.db_table}"))
        if Opportunity is not None:
            self.stdout.write(self.style.NOTICE(f"Opportunity table: {Opportunity._meta.db_table}"))
            # Show DB columns to quickly spot schema mismatches
            try:
                db_cols = sorted(db_column_names_for_model(Opportunity))
                self.stdout.write(self.style.NOTICE(f"Opportunity DB columns: {', '.join(db_cols)}"))
            except Exception:
                pass
        if Submission is not None:
            self.stdout.write(self.style.NOTICE(f"Submission table: {Submission._meta.db_table}"))
        if Rating is not None:
            self.stdout.write(self.style.NOTICE(f"Rating table: {Rating._meta.db_table}"))
        if Report is not None:
            self.stdout.write(self.style.NOTICE(f"Report table: {Report._meta.db_table}"))

        reset = bool(options.get("reset"))
        reset_students = bool(options.get("reset_students"))
        reset_opps = bool(options.get("reset_opps"))
        reset_subs = bool(options.get("reset_subs"))
        reset_ratings = bool(options.get("reset_ratings"))
        reset_reports = bool(options.get("reset_reports"))

        # Targets
        target_students = int(options.get("students") or 0)
        target_opps = int(options.get("opps") or 0)
        target_subs = int(options.get("subs") or 0)
        target_ratings = int(options.get("ratings") or 0)
        target_reports = int(options.get("reports") or 0)

        # Safety guard: never touch existing opportunities unless user is explicitly creating opportunities
        if reset and reset_opps and target_opps == 0:
            raise CommandError(
                "You used --reset-opps but --opps is 0. This would delete existing opportunities. "
                "Run without --reset-opps, or set --opps > 0 if you truly want to reset opportunities."
            )

        if reset:
            # Only reset the tables the user asked for.
            if reset_students or target_students > 0:
                deleted_students, _ = Student.objects.all().delete()
                self.stdout.write(self.style.WARNING(f"Deleted {deleted_students} Student-related objects."))
            else:
                self.stdout.write(self.style.NOTICE("Not resetting students (use --reset-students or seed students > 0)."))

            if reset_opps and Opportunity is not None:
                try:
                    before = truncate_model_table(Opportunity)
                    self.stdout.write(self.style.WARNING(f"TRUNCATED {before} opportunities (and cascaded related rows)."))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"FAILED resetting opportunities: {type(e).__name__}: {e}"))
                    raise

            if reset_subs and Submission is not None:
                try:
                    before = truncate_model_table(Submission)
                    self.stdout.write(self.style.WARNING(f"TRUNCATED {before} submissions (and cascaded related rows)."))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"FAILED resetting submissions: {type(e).__name__}: {e}"))
                    raise

            if reset_ratings and Rating is not None:
                try:
                    before = truncate_model_table(Rating)
                    self.stdout.write(self.style.WARNING(f"TRUNCATED {before} ratings (and cascaded related rows)."))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"FAILED resetting ratings: {type(e).__name__}: {e}"))
                    raise

            if reset_reports and Report is not None:
                try:
                    before = truncate_model_table(Report)
                    self.stdout.write(self.style.WARNING(f"TRUNCATED {before} reports (and cascaded related rows)."))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"FAILED resetting reports: {type(e).__name__}: {e}"))
                    raise

        # --------------------
        # 1) Create Students
        # --------------------
        created_students = 0

        if target_students > 0:
            for _ in range(target_students):
                username = f"{fake.user_name()}{random.randint(1000, 9999)}"
                email = fake.unique.email()

                try:
                    with transaction.atomic():
                        user = User.objects.create_user(
                            username=username,
                            email=email,
                            password="Test12345",
                        )

                        student_kwargs = {
                            "user": user,
                            "full_name": fake.name(),
                            "major": random.choice(DEFAULT_MAJORS),
                        }

                        # Optional student fields (DB-safe)
                        if has_model_field(Student, "phone_num"):
                            student_kwargs["phone_num"] = make_saudi_mobile()
                        elif has_model_field(Student, "phone_number"):
                            student_kwargs["phone_number"] = make_saudi_mobile()
                        elif has_model_field(Student, "phone"):
                            student_kwargs["phone"] = make_saudi_mobile()

                        Student.objects.create(**student_kwargs)

                    created_students += 1

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"FAILED creating a student: {type(e).__name__}: {e}"))
                    raise

            self.stdout.write(self.style.SUCCESS(f"Created {created_students} students."))
        else:
            self.stdout.write(self.style.NOTICE("Skipping students (students=0)."))

        # --------------------
        # 2) Create Opportunities
        # --------------------
        created_opps = 0

        if target_opps > 0:
            if Opportunity is None:
                self.stdout.write(self.style.ERROR("Cannot create opportunities because Opportunity model is not importable."))
                return

            cat_map = ensure_categories()

            # Pick a superuser as 'admin creator' if possible
            superuser = User.objects.filter(is_superuser=True).order_by("id").first()
            admin_profile = None
            if superuser is not None and Admin is not None:
                try:
                    admin_profile, _ = Admin.objects.get_or_create(user=superuser)
                except Exception:
                    admin_profile = None

            # Create (or reuse) a single AI observer row if the schema has it
            ai_row = None
            if OpportunityObserverAI is not None:
                try:
                    ai_row = OpportunityObserverAI.objects.order_by("id").first()
                    if ai_row is None:
                        ai_row = OpportunityObserverAI.objects.create()
                except Exception:
                    ai_row = None

            existing_students = list(Student.objects.all().values_list("id", flat=True))

            companies = [
                "AON",
                "Deloitte",
                "SAB",
                "Saudi Tadawul Group",
                "Webook",
                "Digital Government Authority",
                "GOSI",
                "Riyadh Air",
                "JASARA PMC",
                "TAWAL",
            ]

            titles = [
                "Cooperative Training Program (Co-op)",
                "IT Co-op Trainee",
                "Project Management Co-op",
                "Finance Co-op Trainee",
                "Marketing Co-op",
                "Data & Analytics Co-op",
                "Cybersecurity Co-op",
                "Business Development Co-op",
                "HR Co-op",
                "Engineering Co-op",
            ]

            statuses = ["open", "open", "open", "closed"]

            for _ in range(target_opps):
                company = random.choice(companies)
                title = random.choice(titles)

                region, city = pick_region_city()
                location_combined = f"{region},{city}"

                deadline = date.today() + timedelta(days=random.randint(7, 120))
                status = random.choice(statuses)

                # Choose a category (if categories exist)
                cat_obj = None
                if cat_map:
                    cat_name = random.choice(list(cat_map.keys()))
                    cat_obj = cat_map[cat_name]

                # Decide source type: AI / Admin / Student
                source_type = random.choice(["ai", "admin", "student"])

                # Build kwargs defensively based on your Opportunity model fields.
                # IMPORTANT: use _meta field checks (not hasattr) to avoid @property fields.
                opp_fields = model_field_names(Opportunity)

                create_kwargs = {}

                # Company/org
                if "org" in opp_fields:
                    create_kwargs["org"] = company
                elif "company" in opp_fields:
                    create_kwargs["company"] = company

                # Title
                if "opp_title" in opp_fields:
                    create_kwargs["opp_title"] = title
                elif "title" in opp_fields:
                    create_kwargs["title"] = title

                # Location
                if "location" in opp_fields:
                    create_kwargs["location"] = location_combined

                # Region/City (only if they are real DB fields)
                if "region" in opp_fields:
                    create_kwargs["region"] = region
                if "city" in opp_fields:
                    create_kwargs["city"] = city

                # Deadline / Status
                if "deadline" in opp_fields:
                    create_kwargs["deadline"] = deadline
                if "status" in opp_fields:
                    create_kwargs["status"] = status

                # Category FK
                if cat_obj is not None and "category" in opp_fields:
                    create_kwargs["category"] = cat_obj

                # Optional metadata
                if "sourcelink" in opp_fields:
                    create_kwargs["sourcelink"] = None
                elif "source_link" in opp_fields:
                    create_kwargs["source_link"] = None

                # Try to avoid duplicates (basic)
                qs = Opportunity.objects.all()
                if "org" in create_kwargs and "opp_title" in create_kwargs:
                    qs = qs.filter(org=create_kwargs["org"], opp_title=create_kwargs["opp_title"])
                elif "company" in create_kwargs and "title" in create_kwargs:
                    qs = qs.filter(company=create_kwargs["company"], title=create_kwargs["title"])
                if qs.exists():
                    continue

                try:
                    with transaction.atomic():
                        opp = Opportunity.objects.create(**create_kwargs)

                        # Attach a creator/source if your schema supports it
                        # Admin source
                        if source_type == "admin" and admin_profile is not None:
                            set_if_field_exists(opp, "created_by_admin", admin_profile)
                            set_if_field_exists(opp, "admin", admin_profile)

                        # Student source (use real IDs)
                        if source_type == "student" and existing_students:
                            sid = random.choice(existing_students)
                            try:
                                student_obj = Student.objects.get(id=sid)
                                set_if_field_exists(opp, "created_by_student", student_obj)
                                set_if_field_exists(opp, "student", student_obj)
                            except Exception:
                                pass

                        # AI source
                        if source_type == "ai" and ai_row is not None:
                            set_if_field_exists(opp, "created_by_ai", ai_row)
                            set_if_field_exists(opp, "observer", ai_row)

                        # If you have a source/type text field
                        set_if_field_exists(opp, "source", source_type)
                        set_if_field_exists(opp, "source_type", source_type)

                        try:
                            opp.save()
                        except Exception:
                            pass

                    created_opps += 1

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"FAILED creating an opportunity: {type(e).__name__}: {e}"))
                    raise

            self.stdout.write(self.style.SUCCESS(f"Created {created_opps} opportunities."))
        else:
            self.stdout.write(self.style.NOTICE("Skipping opportunities (opps=0)."))

        # --------------------
        # 3) Create Submissions
        # --------------------
        created_subs = 0

        if target_subs > 0:
            if Submission is None:
                self.stdout.write(self.style.ERROR("Cannot create submissions because Submission model is not importable."))
                return
            if Opportunity is None:
                self.stdout.write(self.style.ERROR("Cannot create submissions because Opportunity model is not importable."))
                return

            student_ids = list(Student.objects.all().values_list("id", flat=True))
            opp_ids = list(Opportunity.objects.all().values_list("id", flat=True))

            if not student_ids:
                self.stdout.write(self.style.ERROR("No students found. Create students first, then run with --subs."))
                return
            if not opp_ids:
                self.stdout.write(self.style.ERROR("No opportunities found. Create opportunities first, then run with --subs."))
                return

            sub_fields = model_field_names(Submission)

            # FK to Opportunity is required
            opp_fk_name, opp_fk_attname = find_fk_field(Submission, Opportunity)
            if not opp_fk_attname:
                self.stdout.write(self.style.ERROR("Submission model is missing a ForeignKey to Opportunity."))
                return

            # Prefer explicit student/admin creator fields if your updated model has them
            # - submitted_by_student (FK -> Student)
            # - submitted_by_admin (FK -> Admin) [optional]
            student_fk_attname = None
            if "submitted_by_student" in sub_fields:
                try:
                    f = Submission._meta.get_field("submitted_by_student")
                    student_fk_attname = getattr(f, "attname", None)
                except Exception:
                    student_fk_attname = None

            admin_fk_attname = None
            if Admin is not None and "submitted_by_admin" in sub_fields:
                try:
                    f = Submission._meta.get_field("submitted_by_admin")
                    admin_fk_attname = getattr(f, "attname", None)
                except Exception:
                    admin_fk_attname = None

            # Backwards-compatible fallback: any FK to Student
            if student_fk_attname is None:
                _student_fk_name, _student_fk_attname = find_fk_field(Submission, Student)
                student_fk_attname = _student_fk_attname

            if not student_fk_attname and not admin_fk_attname:
                # Print detected FKs to help you fix the model if needed
                try:
                    fk_debug = []
                    for f in Submission._meta.get_fields():
                        if not getattr(f, "concrete", False):
                            continue
                        if isinstance(f, models.ForeignKey):
                            fk_debug.append(f"{f.name} -> {getattr(f.remote_field, 'model', None)}")
                    fk_debug_str = ", ".join(fk_debug) if fk_debug else "(no concrete ForeignKey fields found)"
                except Exception:
                    fk_debug_str = "(could not introspect Submission fields)"

                self.stdout.write(
                    self.style.ERROR(
                        "Submission model is missing the required creator FK (Student/Admin). "
                        f"Detected FKs: {fk_debug_str}"
                    )
                )
                return

            # If you support admin submissions, collect admin ids
            admin_ids = []
            if Admin is not None and admin_fk_attname:
                try:
                    admin_ids = list(Admin.objects.all().values_list("id", flat=True))
                except Exception:
                    admin_ids = []

            possible_statuses = [
                "pending",
                "approved",
                "rejected",
            ]

            tz = timezone.get_current_timezone()

            # Build a pool of unique (student, opportunity) pairs for student-submitted rows
            all_pairs = [(sid, oid) for sid in student_ids for oid in opp_ids]
            random.shuffle(all_pairs)

            # We will try to create exactly `target_subs` rows with a capped number of attempts.
            # This avoids getting stuck if uniqueness constraints exist.
            attempts = 0
            max_attempts = max(200, target_subs * 20)

            pair_idx = 0

            while created_subs < target_subs and attempts < max_attempts:
                attempts += 1

                # Decide who submitted it: student (default) vs admin (if available)
                submitter_type = "student"
                if admin_ids and random.random() < 0.25:
                    submitter_type = "admin"

                oid = random.choice(opp_ids)
                create_kwargs = {}
                create_kwargs[opp_fk_attname] = oid

                if submitter_type == "admin" and admin_fk_attname and admin_ids:
                    create_kwargs[admin_fk_attname] = random.choice(admin_ids)
                    # Ensure student FK is null if present and nullable
                    if student_fk_attname and student_fk_attname in sub_fields:
                        create_kwargs[student_fk_attname] = None
                    if "submitted_by_type" in sub_fields:
                        create_kwargs["submitted_by_type"] = "admin"
                else:
                    # Student submission
                    if pair_idx < len(all_pairs):
                        sid, oid2 = all_pairs[pair_idx]
                        pair_idx += 1
                        oid = oid2
                        create_kwargs[opp_fk_attname] = oid
                    else:
                        sid = random.choice(student_ids)

                    if student_fk_attname:
                        create_kwargs[student_fk_attname] = sid
                    if "submitted_by_type" in sub_fields:
                        create_kwargs["submitted_by_type"] = "student"

                # Optional fields
                if "status" in sub_fields:
                    create_kwargs["status"] = random.choice(possible_statuses)

                if "notes" in sub_fields:
                    create_kwargs["notes"] = "Auto-seeded submission"
                if "comment" in sub_fields:
                    create_kwargs["comment"] = "Auto-seeded submission"

                if "cv_link" in sub_fields:
                    create_kwargs["cv_link"] = None
                if "resume_link" in sub_fields:
                    create_kwargs["resume_link"] = None

                if "submitted_at" in sub_fields:
                    dt = fake.date_time_between(start_date="-30d", end_date="now")
                    if timezone.is_naive(dt):
                        dt = timezone.make_aware(dt, tz)
                    create_kwargs["submitted_at"] = dt

                # Avoid duplicates if your schema enforces uniqueness on (student, opportunity)
                # Do this only for student submissions.
                try:
                    if submitter_type == "student" and student_fk_attname:
                        qs = Submission.objects.all().filter(**{student_fk_attname: create_kwargs.get(student_fk_attname), opp_fk_attname: oid})
                        if qs.exists():
                            continue
                except Exception:
                    pass

                try:
                    with transaction.atomic():
                        Submission.objects.create(**create_kwargs)
                    created_subs += 1
                except Exception as e:
                    # If it fails due to a uniqueness constraint or required field, we keep trying
                    # until we hit max_attempts.
                    err = f"{type(e).__name__}: {e}"
                    self.stdout.write(self.style.WARNING(f"Skipped one submission due to error: {err}"))
                    continue

            if created_subs < target_subs:
                self.stdout.write(
                    self.style.WARNING(
                        f"Created {created_subs}/{target_subs} submissions (stopped after {attempts} attempts; likely uniqueness/required-field constraints)."
                    )
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"Created {created_subs} submissions."))
        else:
            self.stdout.write(self.style.NOTICE("Skipping submissions (subs=0)."))

        # --------------------
        # 4) Create Ratings
        # --------------------
        created_ratings = 0

        if target_ratings > 0:
            if Rating is None:
                self.stdout.write(self.style.ERROR("Cannot create ratings because Rating model is not importable."))
                return
            if Opportunity is None:
                self.stdout.write(self.style.ERROR("Cannot create ratings because Opportunity model is not importable."))
                return

            student_ids = list(Student.objects.all().values_list("id", flat=True))
            opp_ids = list(Opportunity.objects.all().values_list("id", flat=True))

            if not student_ids:
                self.stdout.write(self.style.ERROR("No students found. Create students first, then run with --ratings."))
                return
            if not opp_ids:
                self.stdout.write(self.style.ERROR("No opportunities found. Create opportunities first, then run with --ratings."))
                return

            rating_fields = model_field_names(Rating)

            # Find required FKs
            opp_fk_name, opp_fk_attname = find_fk_field(Rating, Opportunity)
            stu_fk_name, stu_fk_attname = find_fk_field(Rating, Student)

            if not opp_fk_attname:
                self.stdout.write(self.style.ERROR("Rating model is missing a ForeignKey to Opportunity."))
                return
            if not stu_fk_attname:
                self.stdout.write(self.style.ERROR("Rating model is missing a ForeignKey to Student."))
                return

            # Build unique (student, opportunity) pairs to avoid duplicates
            pairs = [(sid, oid) for sid in student_ids for oid in opp_ids]
            random.shuffle(pairs)

            attempts = 0
            max_attempts = max(300, target_ratings * 30)
            pair_idx = 0

            # Detect rating schema
            has_dimensions = all(f in rating_fields for f in ["learning_value", "work_env", "mentorship", "outcome"])
            has_overall = "overall" in rating_fields

            # Fallback single-score field (older schema)
            score_field = None
            if not (has_dimensions and has_overall):
                for cand in ["score", "rating", "value", "stars"]:
                    if cand in rating_fields:
                        score_field = cand
                        break

            if not (has_dimensions and has_overall) and score_field is None:
                self.stdout.write(
                    self.style.ERROR(
                        "Rating model schema not recognized. Expected either dimension fields (learning_value/work_env/mentorship/outcome + overall) "
                        "or a single numeric field (score/rating/value/stars)."
                    )
                )
                return

            while created_ratings < target_ratings and attempts < max_attempts:
                attempts += 1

                if pair_idx < len(pairs):
                    sid, oid = pairs[pair_idx]
                    pair_idx += 1
                else:
                    sid = random.choice(student_ids)
                    oid = random.choice(opp_ids)

                create_kwargs = {
                    stu_fk_attname: sid,
                    opp_fk_attname: oid,
                }

                # New schema: 4 dimensions + overall
                if has_dimensions and has_overall:
                    lv = random.randint(1, 5)
                    we = random.randint(1, 5)
                    ms = random.randint(1, 5)
                    oc = random.randint(1, 5)

                    create_kwargs["learning_value"] = lv
                    create_kwargs["work_env"] = we
                    create_kwargs["mentorship"] = ms
                    create_kwargs["outcome"] = oc

                    # Store overall as numeric(4,2) average
                    overall = round((lv + we + ms + oc) / 4.0, 2)
                    create_kwargs["overall"] = overall

                # Old schema: single numeric score
                else:
                    # Score: 1..5 (int) by default
                    create_kwargs[score_field] = random.randint(1, 5)

                # Optional timestamps
                if "created_at" in rating_fields and "created_at" not in create_kwargs:
                    dt = fake.date_time_between(start_date="-30d", end_date="now")
                    if timezone.is_naive(dt):
                        dt = timezone.make_aware(dt, timezone.get_current_timezone())
                    create_kwargs["created_at"] = dt

                # Avoid duplicates best-effort
                try:
                    qs = Rating.objects.all().filter(**{stu_fk_attname: sid, opp_fk_attname: oid})
                    if qs.exists():
                        continue
                except Exception:
                    pass

                try:
                    with transaction.atomic():
                        Rating.objects.create(**create_kwargs)
                    created_ratings += 1
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    self.stdout.write(self.style.WARNING(f"Skipped one rating due to error: {err}"))
                    continue

            if created_ratings < target_ratings:
                self.stdout.write(
                    self.style.WARNING(
                        f"Created {created_ratings}/{target_ratings} ratings (stopped after {attempts} attempts; likely uniqueness/required-field constraints)."
                    )
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"Created {created_ratings} ratings."))

            # Optional: update avg_rating on opportunities if the column exists
            # Prefer using `overall` if present, otherwise fall back to detected single score field.
            if Opportunity is not None and has_model_field(Opportunity, "avg_rating"):
                try:
                    opp_table = connection.ops.quote_name(Opportunity._meta.db_table)
                    rat_table = connection.ops.quote_name(Rating._meta.db_table)

                    opp_id_col = opp_fk_attname  # e.g., opportunity_id

                    if has_overall:
                        score_col = "overall"
                    else:
                        # Map score_field to actual DB column name
                        try:
                            f = Rating._meta.get_field(score_field)
                            score_col = f.column
                        except Exception:
                            score_col = score_field

                    with connection.cursor() as cur:
                        cur.execute(
                            f"""
                            UPDATE {opp_table} o
                            SET avg_rating = sub.avg_score
                            FROM (
                                SELECT {opp_id_col} AS oid, AVG({score_col})::numeric(4,2) AS avg_score
                                FROM {rat_table}
                                GROUP BY {opp_id_col}
                            ) sub
                            WHERE o.id = sub.oid;
                            """
                        )
                except Exception:
                    pass

        else:
            self.stdout.write(self.style.NOTICE("Skipping ratings (ratings=0)."))


        # --------------------
        # 5) Create Reports
        # --------------------
        created_reports = 0

        if True:
            target_reports = 10
            if Report is None:
                self.stdout.write(self.style.ERROR("Cannot create reports because Report model is not importable."))
                return
            if Opportunity is None:
                self.stdout.write(self.style.ERROR("Cannot create reports because Opportunity model is not importable."))
                return

            student_ids = list(Student.objects.all().values_list("id", flat=True))
            opp_ids = list(
                Opportunity.objects.exclude(
                    submissions__status__in=["pending", "rejected"]
                ).values_list("id", flat=True)
            )

            if not student_ids:
                self.stdout.write(self.style.ERROR("No students found. Create students first."))
                return
            if not opp_ids:
                self.stdout.write(self.style.ERROR("No approved opportunities found. Create opportunities first."))
                return

            report_fields = model_field_names(Report)

            # Required FKs
            opp_fk_name, opp_fk_attname = find_fk_field(Report, Opportunity)
            stu_fk_name, stu_fk_attname = find_fk_field(Report, Student)

            if not opp_fk_attname:
                self.stdout.write(self.style.ERROR("Report model is missing a ForeignKey to Opportunity."))
                return
            if not stu_fk_attname:
                self.stdout.write(self.style.ERROR("Report model is missing a ForeignKey to Student."))
                return

            report_types = [
                "broken_link",
                "wrong_deadline",
                "wrong_location",
                "duplicate",
                "scam",
                "other",
            ]
            statuses = ["pending", "resolved"]

            # Create unique (student, opportunity) pairs to avoid duplicates if you later add a constraint
            pairs = [(sid, oid) for sid in student_ids for oid in opp_ids]
            random.shuffle(pairs)

            attempts = 0
            max_attempts = max(300, target_reports * 30)
            pair_idx = 0

            tz = timezone.get_current_timezone()

            while created_reports < target_reports and attempts < max_attempts:
                attempts += 1

                if pair_idx < len(pairs):
                    sid, oid = pairs[pair_idx]
                    pair_idx += 1
                else:
                    sid = random.choice(student_ids)
                    oid = random.choice(opp_ids)

                create_kwargs = {
                    stu_fk_attname: sid,
                    opp_fk_attname: oid,
                }

                if "report_type" in report_fields:
                    create_kwargs["report_type"] = random.choice(report_types)

                if "status" in report_fields:
                    create_kwargs["status"] = random.choice(statuses)

                if "description" in report_fields:
                    _report_descriptions = {
                        "scam": [
                            "This looks like a fake opportunity. The company has no online presence and the link redirects to a suspicious site.",
                            "I applied and they asked me for my bank details. Pretty sure this is a scam.",
                            "The posting asks for a registration fee which is a red flag. No legitimate co-op requires payment.",
                            "I searched the company name and nothing came up. The contact email is also a random Gmail address.",
                            "They offered me the position immediately without any interview. Something feels very off about this.",
                        ],
                        "spam": [
                            "This exact same opportunity has been posted multiple times this week under different titles.",
                            "The description is copied word for word from another listing already on the platform.",
                            "This doesn't seem like a real co-op. It looks like an ad disguised as a job posting.",
                            "I've seen this posted 3 times already. It's cluttering the feed.",
                            "The company keeps reposting this with slightly different titles. Please remove the duplicates.",
                        ],
                        "duplicate": [
                            "This is a duplicate of an existing listing. I saw the same opportunity posted last week.",
                            "Already reported this one before. It keeps getting reposted under a different title.",
                            "Same company, same description, same link — just a different posting date.",
                            "This is the third time I've seen this exact opportunity. Please merge or remove.",
                            "Duplicate of another active listing. The source link is identical.",
                        ],
                        "wrong_info": [
                            "The deadline listed has already passed by several months. The info needs to be updated.",
                            "The location says Riyadh but the actual position is remote. Misleading.",
                            "I checked the source link and the company name is different from what's listed here.",
                            "The major listed doesn't match what the company is actually looking for according to their website.",
                            "The salary and duration mentioned in the description don't match the original posting.",
                        ],
                        "broken_link": [
                            "The source link leads to a 404 page. Can't apply.",
                            "Clicked the apply button and it goes nowhere. The link is dead.",
                            "The URL redirects to the company homepage with no mention of this opportunity.",
                            "Link has been broken for at least a week. The application portal seems to be down.",
                            "The source link requires a login to a system I don't have access to. Can't verify or apply.",
                        ],
                        "other": [
                            "Not sure this qualifies as a co-op. It reads more like a full-time job listing.",
                            "The opportunity is only open to specific universities which isn't mentioned in the title.",
                            "The description is entirely in English but the company only operates in Arabic-speaking regions.",
                            "This position requires 2+ years of experience which disqualifies most students.",
                            "The listing has no contact information and no way to verify if it's still available.",
                        ],
                    }
                    rtype = create_kwargs.get("report_type", "other")
                    descs = _report_descriptions.get(rtype, _report_descriptions["other"])
                    create_kwargs["description"] = random.choice(descs)

                if "created_at" in report_fields and "created_at" not in create_kwargs:
                    dt = fake.date_time_between(start_date="-30d", end_date="now")
                    if timezone.is_naive(dt):
                        dt = timezone.make_aware(dt, tz)
                    create_kwargs["created_at"] = dt



                try:
                    with transaction.atomic():
                        Report.objects.create(**create_kwargs)
                    created_reports += 1
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    self.stdout.write(self.style.WARNING(f"Skipped one report due to error: {err}"))
                    continue

            if created_reports < target_reports:
                self.stdout.write(
                    self.style.WARNING(
                        f"Created {created_reports}/{target_reports} reports (stopped after {attempts} attempts; likely uniqueness/required-field constraints)."
                    )
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"Created {created_reports} reports."))

        else:
            self.stdout.write(self.style.NOTICE("Skipping reports (reports=0)."))

        # Final sanity checks
        self.stdout.write(self.style.SUCCESS(f"Student.objects.count() = {Student.objects.count()}"))
        if Opportunity is not None:
            # Avoid ORM count if the model has fields not yet migrated to DB.
            try:
                with connection.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(Opportunity._meta.db_table)};")
                    sql_opps = cur.fetchone()[0]
                self.stdout.write(self.style.SUCCESS(f"SQL COUNT Opportunities = {sql_opps}"))
            except Exception:
                pass
        if Submission is not None:
            try:
                with connection.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(Submission._meta.db_table)};")
                    sql_subs = cur.fetchone()[0]
                self.stdout.write(self.style.SUCCESS(f"SQL COUNT Submissions = {sql_subs}"))
            except Exception:
                pass
        if Rating is not None:
            try:
                with connection.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(Rating._meta.db_table)};")
                    sql_ratings = cur.fetchone()[0]
                self.stdout.write(self.style.SUCCESS(f"SQL COUNT Ratings = {sql_ratings}"))
            except Exception:
                pass

        if Report is not None:
            try:
                with connection.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(Report._meta.db_table)};")
                    sql_reports = cur.fetchone()[0]
                self.stdout.write(self.style.SUCCESS(f"SQL COUNT Reports = {sql_reports}"))
            except Exception:
                pass

        with connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {Student._meta.db_table};")
            sql_students = cur.fetchone()[0]
        self.stdout.write(self.style.SUCCESS(f"SQL COUNT Students = {sql_students}"))