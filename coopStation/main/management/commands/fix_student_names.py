from django.core.management.base import BaseCommand
from accounts.models import Student, AuditLog

PREFIXES_TO_REMOVE = [
    "السيد ", "السيدة ", "المهندس ", "المهندسة ",
    "الدكتور ", "الدكتورة ", "الأستاذ ", "الأستاذة ",
    "د. ", "م. ", "أ. ",
]

NAME_REPLACEMENTS = {
    "ali":              "عبدالله محمد الغامدي",
    "فردوس بن ظافر":   "فردوس سعد الظافري",
    "لورين الحكير":    "لورين خالد الحكير",
    "هاجر الحكير":     "هاجر فهد الحكير",
    "ناهد حجار":       "ناهد أحمد الحجار",
    "رمحي آل سلطان":   "راشد سلطان القحطاني",
    "هيثم بن لافي":    "هيثم عبدالله اللافي",
    "نعيم أبا الخيل":  "نعيم محمد أبا الخيل",
    "صنديد المغاولة":  "سعد ناصر المغاولة",
    "يسري العليان":    "يسري عمر العليان",
    "عبد المجيد بقشان": "عبدالمجيد سالم بقشان",
    "إخلاص آل جعفر":  "إخلاص عبدالله الجعفري",
    "تاج آل رفيع":     "تاج محمد الرفيعي",
    "Ali":              "علي محمد السلمي",
}


def _strip_prefix(name):
    for prefix in PREFIXES_TO_REMOVE:
        if name.startswith(prefix):
            return name[len(prefix):].strip()
    return name.strip()


def _is_english(name):
    return all(ord(c) < 128 for c in name.replace(" ", ""))


class Command(BaseCommand):
    help = "Strip name prefixes, fix placeholder names, and clear audit log."

    def handle(self, *args, **options):
        students = list(Student.objects.all())
        self.stdout.write(f"Processing {len(students)} students...\n")

        # ── Step 1: Strip prefixes ──────────────────────────────────────────
        stripped = 0
        for student in students:
            cleaned = _strip_prefix(student.full_name)
            if cleaned != student.full_name:
                self.stdout.write(
                    f"  Strip: {student.full_name!r}  →  {cleaned!r}"
                )
                student.full_name = cleaned
                student.save(update_fields=["full_name"])
                stripped += 1

        self.stdout.write(self.style.SUCCESS(f"\nStep 1 done: {stripped} names stripped.\n"))

        # ── Step 2: Fix specific names ──────────────────────────────────────
        fixed = 0
        for student in Student.objects.all():
            replacement = NAME_REPLACEMENTS.get(student.full_name)
            if replacement:
                self.stdout.write(
                    f"  Fix:   {student.full_name!r}  →  {replacement!r}"
                )
                student.full_name = replacement
                student.save(update_fields=["full_name"])
                fixed += 1

        self.stdout.write(self.style.SUCCESS(f"\nStep 2 done: {fixed} names replaced.\n"))

        # ── Step 3: Scan for remaining English names ────────────────────────
        self.stdout.write("Step 3 — Scanning for remaining English names:")
        english_found = []
        for s in Student.objects.all():
            if _is_english(s.full_name):
                english_found.append(s)
                self.stdout.write(self.style.WARNING(f"  ID {s.id}: {s.full_name!r}"))

        if not english_found:
            self.stdout.write("  None found — all names are Arabic.")
        self.stdout.write("")

        # ── Step 4: Clear audit log ─────────────────────────────────────────
        deleted, _ = AuditLog.objects.all().delete()
        self.stdout.write(self.style.SUCCESS(f"Step 4 done: Audit log cleared ({deleted} entries deleted)."))
