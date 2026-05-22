import json
import hashlib
from pathlib import Path
from typing import Optional

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Opportunity, OppCategory

# Optional: OpportunityVersion may not exist yet in some schemas.
try:
    from accounts.models import OpportunityVersion  # type: ignore
except Exception:  # pragma: no cover
    OpportunityVersion = None  # type: ignore


# ---- Category catalog (keep in sync with your models choices if you have them) ----
DEFAULT_CATEGORIES = [
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


def seed_default_categories() -> None:
    """Create default opportunity categories if they don't exist."""
    for name in DEFAULT_CATEGORIES:
        OppCategory.objects.get_or_create(name=name)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_location(raw: Optional[str]) -> Optional[str]:
    """Store as 'Region,City' (City can be empty)."""
    if not raw:
        return None
    raw = raw.strip()
    if "," in raw:
        region, city = [p.strip() for p in raw.split(",", 1)]
        return f"{region},{city}" if city else f"{region},"
    return f"{raw},"


def pick_category_from_text(text: str) -> str:
    t = (text or "").lower()

    if any(k in t for k in ["شريعة", "فقه", "islamic", "sharia"]):
        return "Shariah & Islamic Studies"
    if any(k in t for k in ["قانون", "law", "legal"]):
        return "Law"
    if any(k in t for k in ["زراعة", "agric", "environment", "sustainab"]):
        return "Agriculture & Environmental"
    if any(k in t for k in ["صيدلة", "pharmacy"]):
        return "Pharmacy"
    if any(k in t for k in ["health", "medical", "medicine", "nursing"]):
        return "Healthcare"

    if any(k in t for k in ["cyber", "siem", "soc", "security operations", "infosec"]):
        return "Cybersecurity"
    if any(k in t for k in ["data", "analytics", "machine learning", "ai", "ml"]):
        return "Data & AI"
    if any(k in t for k in ["software", "backend", "frontend", "programming", "developer"]):
        return "Software Engineering"
    if any(k in t for k in ["information systems", "mis"]):
        return "Information Systems"
    if any(k in t for k in ["it", "computer science", "network", "cloud"]):
        return "Computer Science & IT"

    if "accounting" in t or "محاس" in t:
        return "Accounting"
    if "finance" in t or "مالية" in t:
        return "Finance"
    if "marketing" in t or "تسويق" in t:
        return "Marketing"
    if any(k in t for k in ["business", "management", "hr", "إدارة", "موارد"]):
        return "Business & Management"

    if any(k in t for k in ["architecture", "urban", "planning", "تصميم معماري"]):
        return "Architecture & Planning"
    if any(k in t for k in ["design", "ui", "ux", "graphic", "motion"]):
        return "Design (UI/UX & Graphic)"

    if any(k in t for k in ["engineering", "mechanical", "electrical", "civil", "industrial"]):
        return "Engineering"

    return "Other"


def coerce_category(name: Optional[str], majors_text: str, desc_text: str) -> str:
    if name:
        normalized = name.strip()
        if normalized in DEFAULT_CATEGORIES:
            return normalized

    inferred = pick_category_from_text(f"{majors_text}\n{desc_text}")
    return inferred if inferred in DEFAULT_CATEGORIES else "Other"


class Command(BaseCommand):
    help = "Import curated opportunities from JSON and (optionally) create OpportunityVersion snapshots."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            type=str,
            default="accounts/data/curated_opportunities.json",
            help="Path to curated JSON file",
        )
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help="If exists, update and create a new version snapshot.",
        )

    def handle(self, *args, **options):
        json_path = Path(options["path"]).expanduser().resolve()
        update_existing = bool(options.get("update_existing"))

        if not json_path.exists():
            self.stderr.write(self.style.ERROR(f"JSON file not found: {json_path}"))
            return

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            self.stderr.write(self.style.ERROR("JSON must be a list of objects."))
            return

        seed_default_categories()

        created_count = 0
        updated_count = 0
        version_count = 0

        for item in payload:
            if not isinstance(item, dict):
                continue

            company = (item.get("company") or "").strip() or None
            title = (item.get("title") or "").strip()
            if not title:
                continue

            majors_text = (item.get("major") or "").strip()
            # You said you want to remove descriptions for now, so we default to empty.
            desc_text = ""

            cat_name = coerce_category(item.get("category"), majors_text, desc_text)
            category, _ = OppCategory.objects.get_or_create(name=cat_name)

            location = normalize_location(item.get("location"))
            status = (item.get("status") or "open").lower()
            if status not in {"open", "closed"}:
                status = "open"

            deadline = item.get("deadline")
            source_link = (item.get("source_link") or "").strip() or None

            content_hash = None

            # Find existing
            opp = None
            if source_link:
                opp = Opportunity.objects.filter(source_link=source_link).first()
            if not opp:
                opp = Opportunity.objects.filter(company=company, title=title).first()

            if opp is None:
                # Create
                create_kwargs = {
                    "company": company,
                    "title": title,
                    "location": location,
                    "deadline": deadline,
                    "status": status,
                    "major": majors_text or None,
                    "category": category,
                    "source_link": source_link,
                }

                # Only set these fields if they exist in your current model.
                for opt_field, opt_value in {
                    "description_hash": content_hash,
                    "last_checked_at": timezone.now(),
                }.items():
                    if hasattr(Opportunity, opt_field):
                        create_kwargs[opt_field] = opt_value

                opp = Opportunity.objects.create(**create_kwargs)
                created_count += 1

                # Version snapshot (only if model exists)
                if OpportunityVersion is not None:
                    try:
                        OpportunityVersion.objects.create(
                            opportunity=opp,
                            fetched_at=timezone.now(),
                            source_link=source_link,
                            description_text=None,
                            content_hash=content_hash,
                            changed=True,
                        )
                        version_count += 1
                    except Exception:
                        pass

            else:
                if update_existing:
                    changed_any = False

                    fields_to_update = {
                        "location": location,
                        "deadline": deadline,
                        "status": status,
                        "major": majors_text or None,
                        "category": category,
                        "source_link": source_link,
                    }

                    if hasattr(opp, "last_checked_at"):
                        fields_to_update["last_checked_at"] = timezone.now()

                    prev_hash = getattr(opp, "description_hash", None)

                    for field, value in fields_to_update.items():
                        if getattr(opp, field) != value:
                            setattr(opp, field, value)
                            changed_any = True

                    if changed_any:
                        opp.save()
                        updated_count += 1

                        if OpportunityVersion is not None:
                            try:
                                OpportunityVersion.objects.create(
                                    opportunity=opp,
                                    fetched_at=timezone.now(),
                                    source_link=source_link,
                                    description_text=None,
                                    content_hash=content_hash,
                                    changed=False if prev_hash == content_hash else True,
                                )
                                version_count += 1
                            except Exception:
                                pass

        self.stdout.write(self.style.SUCCESS("Curated import done."))
        self.stdout.write(f"Created: {created_count}")
        self.stdout.write(f"Updated: {updated_count}")