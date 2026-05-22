"""
CLI wrapper for the CoopStation link validator.
Produces a detailed validation report. No database writes.

Usage:
  .venv/bin/python validate_cli.py \\
    --url "https://example.com/careers/coop" \\
    --title "Software Engineering Trainee" \\
    --company "Acme Corp" \\
    --description "A 6-month co-op program..."

  # Save report to a file:
  .venv/bin/python validate_cli.py --url ... --title ... --company ... --description ... --output report.txt
"""

import argparse
import os
import sys
import django
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "CoopStation01.settings")
django.setup()

from accounts.link_validator import validate_submission  # noqa: E402

W = 66  # report width

_STATUS_ICON  = {"approved": "✓ APPROVED", "flagged": "⚠ FLAGGED", "rejected": "✗ REJECTED"}
_STEP_TITLES  = {
    "step1_safety":       "STEP 1 · SAFETY CHECK",
    "step2_authenticity": "STEP 2 · AUTHENTICITY CHECK",
    "step3_availability": "STEP 3 · AVAILABILITY CHECK",
    "step4_relevance":    "STEP 4 · RELEVANCE CHECK",
}


# ── Formatting helpers ────────────────────────────────────────────────────────

def _line(char="─"):
    return char * W

def _header(title, status_label):
    left  = f"  {title}"
    right = f"  {status_label}  "
    gap   = W - len(left) - len(right)
    return left + " " * max(gap, 1) + right

def _fmt_points(p):
    if p is None or isinstance(p, str):
        return str(p)
    return f"+{p}" if p > 0 else str(p)

def _write(lines, out):
    out.write("\n".join(lines) + "\n")


# ── Per-step renderers ────────────────────────────────────────────────────────

def _render_step1(data, lines):
    bd = data.get("breakdown", {})
    lines.append("  Score Breakdown:")
    if bd.get("skipped"):
        lines.append(f"    Reason  : {bd.get('reason', 'n/a')}")
    else:
        lines.append(f"    Malicious engines  :  {bd.get('malicious_engines',  0):>3}   (any → REJECTED)")
        lines.append(f"    Suspicious engines :  {bd.get('suspicious_engines', 0):>3}   (any → FLAGGED)")
        lines.append(f"    Harmless engines   :  {bd.get('harmless_engines',   0):>3}")
        lines.append(f"    Undetected engines :  {bd.get('undetected_engines', 0):>3}")
        verdict = "MALICIOUS" if bd.get("malicious_engines", 0) else \
                  "SUSPICIOUS" if bd.get("suspicious_engines", 0) else "CLEAN ✓"
        lines.append(f"    Verdict            :  {verdict}")


def _render_step2(data, lines):
    bd = data.get("breakdown", {})
    rule = bd.get("rule_matched", "")
    lines.append(f"  Domain  : {bd.get('domain', 'n/a')}")

    if rule == "social_media_blocklist":
        lines.append("  Rule    : Social media blocklist — instant reject")
    elif rule == "ats_whitelist":
        lines.append("  Rule    : Trusted ATS whitelist — instant pass")
    else:
        lines.append(f"  Path    : {bd.get('path', 'n/a')}")
        lines.append("  Rule    : Domain trust scoring")

    factors = bd.get("factors", [])
    if factors:
        lines.append("")
        lines.append("  Score Breakdown:")
        col = 46
        for f in factors:
            pts = _fmt_points(f["points"])
            signal = f["signal"]
            lines.append(f"    {signal:<{col}}  {pts}")

        score = bd.get("total_score")
        if score is not None:
            lines.append(f"    {'─' * col}  ────")
            lines.append(f"    {'TOTAL':<{col}}  {score}")
            lines.append(f"    {'Threshold: ≥1 PASSED · =0 PASSED (low trust) · <0 FLAGGED':<{col}}")


def _render_step3(data, lines):
    bd = data.get("breakdown", {})
    scrape = bd.get("scrape", {})

    if scrape.get("success"):
        lines.append(f"  Scraping : Page reached successfully — {scrape.get('chars_extracted', 0):,} characters extracted")
    else:
        lines.append("  Scraping : Could not reach the page")

    factors = bd.get("factors", [])
    if not factors:
        return

    lines.append("")
    lines.append("  LLM Analysis  (Groq · LLaMA 3.1 8B Instant):")
    col_q = 48
    for f in factors:
        pts = _fmt_points(f["points"])
        lines.append(f"    {f['question']:<{col_q}}  {f['answer']:<8}  →  {pts}")

    score = bd.get("total_score")
    if score is not None:
        lines.append(f"    {'─' * col_q}  ──────────────")
        lines.append(f"    {'TOTAL SCORE':<{col_q}}  {score}")
        lines.append(f"    Threshold: ≥2 PASSED · 0-1 FLAGGED · <0 REJECTED")


def _render_step4(data, lines):
    bd = data.get("breakdown", {})
    factors = bd.get("factors", [])
    if not factors:
        return

    lines.append("  LLM Analysis  (Groq · LLaMA 3.1 8B Instant):")
    col_q = 54
    for f in factors:
        pts = _fmt_points(f["points"])
        lines.append(f"    {f['question']:<{col_q}}  {f['answer']:<4}  →  {pts}")

    score     = bd.get("total_score")
    max_score = bd.get("max_score", 6)
    if score is not None:
        lines.append(f"    {'─' * col_q}  ──────────")
        lines.append(f"    {'TOTAL SCORE':<{col_q}}  {score}/{max_score}")
        lines.append(f"    Threshold: ≥5 APPROVED · 3-4 FLAGGED · <3 REJECTED")


_RENDERERS = {
    "step1_safety":       _render_step1,
    "step2_authenticity": _render_step2,
    "step3_availability": _render_step3,
    "step4_relevance":    _render_step4,
}


# ── Verdict note builder ─────────────────────────────────────────────────────

def _build_verdict_note(result: dict) -> str:
    """Generates a specific 1-2 sentence explanation of why the result is flagged or rejected."""
    fs     = result["final_status"]
    steps  = result["steps"]
    action = "flagged for admin review" if fs == "flagged" else "rejected"

    # ── Step 1: safety ──────────────────────────────────────────────────────
    s1 = steps.get("step1_safety")
    if s1 and not s1["passed"]:
        bd = s1.get("breakdown", {})
        if bd.get("malicious_engines", 0):
            return (f"This submission was {action} because VirusTotal flagged the URL as malicious "
                    f"({bd['malicious_engines']} engine(s)). The URL may be harmful.")
        if bd.get("suspicious_engines", 0):
            return (f"This submission was {action} because VirusTotal marked the URL as suspicious "
                    f"({bd['suspicious_engines']} engine(s)). It requires manual safety review.")
        return (f"This submission was {action} because the VirusTotal safety check was unavailable "
                f"and the URL could not be verified as safe.")

    # ── Step 2: authenticity ────────────────────────────────────────────────
    s2 = steps.get("step2_authenticity")
    if s2 and not s2["passed"]:
        bd = s2.get("breakdown", {})
        domain = bd.get("domain", "the submitted domain")
        rule   = bd.get("rule_matched", "")
        if rule == "social_media_blocklist":
            return (f"This submission was {action} because '{domain}' is a social media platform. "
                    f"Co-op listings must link directly to a company or ATS website.")
        score = bd.get("total_score", 0)
        return (f"This submission was {action} because '{domain}' received a domain trust score of {score}. "
                f"The domain shows no recognisable trust signals (no .sa/.gov.sa/.edu.sa, no /careers/ path).")

    # ── Step 3: availability ────────────────────────────────────────────────
    s3 = steps.get("step3_availability")
    if s3:
        bd     = s3.get("breakdown", {})
        scrape = bd.get("scrape", {})
        score  = bd.get("total_score")

        if not s3["passed"]:
            if not scrape.get("success"):
                return (f"This submission was {action} because the page could not be reached. "
                        f"The URL may be broken, behind bot protection, or temporarily unavailable.")
            return (f"This submission was {action} because the opportunity appears to be closed. "
                    f"The page indicated a passed deadline or showed a 'no longer accepting' message.")

        if s3.get("final_status") == "flagged":
            factors = bd.get("factors", [])
            missing = [f["question"].split("·")[1].strip() for f in factors if f["points"] == 0 and "Apply" in f["question"]]
            closed  = any(f["points"] < 0 for f in factors)
            if closed:
                return (f"This submission was {action} because signals of a closed or expired opportunity "
                        f"were detected on the page (availability score: {score}). An admin will verify.")
            return (f"This submission was {action} because the availability check scored {score}/2 — "
                    f"no apply button or application form was detected on the page, and no open deadline was confirmed. "
                    f"An admin will verify whether the opportunity is still accepting applications.")

    # ── Step 4: relevance ───────────────────────────────────────────────────
    s4 = steps.get("step4_relevance")
    if s4:
        bd      = s4.get("breakdown", {})
        score   = bd.get("total_score", 0)
        mx      = bd.get("max_score", 6)
        answers = bd.get("llm_answers", {}) or {}
        mismatches = []
        if answers.get("q1") == "no": mismatches.append("company name")
        if answers.get("q2") == "no": mismatches.append("job title")
        if answers.get("q3") == "no": mismatches.append("opportunity type (not a co-op/internship)")
        if answers.get("q4") == "no": mismatches.append("description content")

        if not s4["passed"]:
            mismatch_str = ", ".join(mismatches) if mismatches else "the submitted fields"
            return (f"This submission was {action} because the URL content did not match the submission "
                    f"(relevance score: {score}/{mx}). Mismatched fields: {mismatch_str}.")

        if s4.get("final_status") == "flagged":
            mismatch_str = ", ".join(mismatches) if mismatches else "some fields"
            return (f"This submission was {action} because only a partial match was found between the "
                    f"submitted details and the page content (score: {score}/{mx}). "
                    f"Uncertain fields: {mismatch_str}. An admin will review.")

    # ── Accumulated flags across multiple passed steps ──────────────────────
    flagged_steps = []
    for key, label in [("step1_safety", "Step 1"), ("step2_safety", "Step 2"),
                       ("step3_availability", "Step 3"), ("step4_relevance", "Step 4")]:
        s = steps.get(key)
        if s and s.get("final_status") == "flagged":
            flagged_steps.append(label)

    if flagged_steps:
        return (f"This submission was {action} because multiple steps raised concerns: "
                f"{', '.join(flagged_steps)}. Each flagged step contributed uncertainty that "
                f"requires manual admin review.")

    return f"This submission was {action} based on the validation results above."


# ── Main report builder ───────────────────────────────────────────────────────

def build_report(args, result) -> str:
    lines = []
    now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

    lines.append(_line("═"))
    lines.append(f"  COOPSTATION LINK VALIDATOR — DETAILED REPORT")
    lines.append(_line("═"))
    lines.append(f"  Generated  :  {now}")
    lines.append(f"  URL        :  {args.url}")
    lines.append(f"  Title      :  {args.title}")
    lines.append(f"  Company    :  {args.company}")
    lines.append("")

    for key, title in _STEP_TITLES.items():
        step_data = result["steps"].get(key)

        if step_data is None:
            lines.append(_line())
            lines.append(f"  {title:<48}  — SKIPPED")
            lines.append(_line())
            lines.append("")
            continue

        passed = step_data["passed"]
        fs     = step_data.get("final_status", "approved" if passed else "rejected")
        label  = _STATUS_ICON.get(fs, fs.upper())

        lines.append(_line("═"))
        lines.append(_header(title, label))
        lines.append(_line("═"))
        lines.append(f"  Summary  :  {step_data.get('detail', '')}")
        lines.append("")

        _RENDERERS[key](step_data, lines)

        lines.append("")

    # ── Final verdict ──
    fs   = result["final_status"]
    icon = _STATUS_ICON.get(fs, fs.upper())
    lines.append(_line("═"))
    lines.append(f"  FINAL VERDICT")
    lines.append(_line("═"))
    lines.append(f"  Status    :  {icon}")
    lines.append(f"  Steps run :  {result['step']} / 4")

    if fs in ("flagged", "rejected"):
        lines.append("")
        lines.append(f"  {_build_verdict_note(result)}")

    lines.append(_line("═"))
    lines.append("")

    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CoopStation Link Validator — detailed CLI report")
    parser.add_argument("--url",         required=True,  help="URL of the co-op opportunity")
    parser.add_argument("--title",       required=True,  help="Submitted opportunity title")
    parser.add_argument("--company",     required=True,  help="Submitted company name")
    parser.add_argument("--description", required=True,  help="Submitted opportunity description")
    parser.add_argument("--output",      default=None,   help="Optional path to save the report (e.g. report.txt)")
    args = parser.parse_args()

    print(f"\n  Running validation — this may take 15–30 seconds...\n")

    result = validate_submission(
        url=args.url,
        title=args.title,
        company=args.company,
        description=args.description,
    )

    report = build_report(args, result)

    print(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"  Report saved to: {args.output}\n")


if __name__ == "__main__":
    main()
