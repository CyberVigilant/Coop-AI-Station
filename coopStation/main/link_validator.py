import base64
import json
import re
import time
from urllib.parse import urlparse, unquote

import requests
from openai import OpenAI
from django.conf import settings

_PRIORITY = {"rejected": 2, "flagged": 1, "approved": 0}

_VT_BASE = "https://www.virustotal.com/api/v3"

_SOCIAL_BLOCKLIST = [
    "linkedin.com", "twitter.com", "x.com", "instagram.com",
    "facebook.com", "snapchat.com", "tiktok.com", "t.me", "wa.me",
]

_ATS_WHITELIST = [
    "workday.com", "greenhouse.io", "lever.co", "taleo.net",
    "myworkdayjobs.com", "jobs.smartrecruiters.com", "icims.com",
    "bamboohr.com", "recruitee.com",
]

_AVAIL_SYSTEM = (
    "You are a validator checking if a co-op training opportunity "
    "is still open for applications. You will receive the text "
    "content of a webpage. Answer only in JSON."
)

_RELEV_SYSTEM = (
    "You are a validator checking if a submitted co-op opportunity "
    "matches the actual webpage content. Answer only in JSON."
)


def _worse(a: str, b: str) -> str:
    return a if _PRIORITY.get(a, 0) >= _PRIORITY.get(b, 0) else b


def _build_result(step: int, passed: bool, final_status: str, steps: dict) -> dict:
    return {"step": step, "passed": passed, "final_status": final_status, "steps": steps}


def _call_llm(system_prompt: str, user_prompt: str):
    def _parse_json(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            block = text.split("```")[1]
            if block.startswith("json"):
                block = block[4:]
            text = block.strip()
        return json.loads(text)

    def _attempt() -> dict:
        client = OpenAI(
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=300,
        )
        return _parse_json(response.choices[0].message.content)

    try:
        return _attempt()
    except Exception:
        try:
            return _attempt()
        except Exception:
            return None


def _step1_safety(url: str) -> dict:
    vt_key = getattr(settings, "VIRUSTOTAL_API_KEY", "")
    if not vt_key:
        return {
            "passed": True,
            "detail": "Safety check skipped (no API key)",
            "breakdown": {"skipped": True, "reason": "VIRUSTOTAL_API_KEY not configured"},
        }

    headers = {"x-apikey": vt_key}
    url_id = base64.urlsafe_b64encode(url.encode()).rstrip(b"=").decode()

    def _check_stats(stats: dict) -> dict:
        malicious  = stats.get("malicious",  0)
        suspicious = stats.get("suspicious", 0)
        harmless   = stats.get("harmless",   0)
        undetected = stats.get("undetected", 0)
        breakdown = {
            "malicious_engines":  malicious,
            "suspicious_engines": suspicious,
            "harmless_engines":   harmless,
            "undetected_engines": undetected,
        }
        if malicious > 0:
            return {
                "passed": False,
                "detail": f"VirusTotal: {malicious} engine(s) flagged as malicious",
                "final_status": "rejected",
                "breakdown": breakdown,
            }
        if suspicious > 0:
            return {
                "passed": False,
                "detail": f"VirusTotal: {suspicious} engine(s) flagged as suspicious",
                "final_status": "flagged",
                "breakdown": breakdown,
            }
        return {"passed": True, "detail": "VirusTotal: no threats detected", "breakdown": breakdown}

    try:
        r = requests.get(f"{_VT_BASE}/urls/{url_id}", headers=headers, timeout=10)
        if r.status_code == 200:
            return _check_stats(r.json()["data"]["attributes"]["last_analysis_stats"])
        if r.status_code != 404:
            r.raise_for_status()

        submit = requests.post(f"{_VT_BASE}/urls", headers=headers, data={"url": url}, timeout=10)
        submit.raise_for_status()
        analysis_id = submit.json()["data"]["id"]

        for _ in range(3):
            time.sleep(5)
            analysis = requests.get(f"{_VT_BASE}/analyses/{analysis_id}", headers=headers, timeout=10)
            analysis.raise_for_status()
            data = analysis.json()["data"]
            if data["attributes"]["status"] == "completed":
                return _check_stats(data["attributes"]["stats"])

        return {
            "passed": True,
            "detail": "VirusTotal: URL queued for analysis, no prior flags found",
            "breakdown": {"skipped": True, "reason": "Analysis still queued after polling"},
        }
    except Exception as e:
        return {
            "passed": False,
            "detail": "Safety check unavailable",
            "final_status": "flagged",
            "breakdown": {"skipped": True, "reason": f"API error: {e}"},
        }


def _step2_authenticity(url: str) -> dict:
    if not url.startswith("http://") and not url.startswith("https://"):
        return {
            "passed": False,
            "detail": "Invalid URL — not a valid web address",
            "final_status": "rejected",
            "breakdown": {
                "domain": "",
                "rule_matched": "invalid_url",
                "factors": [{"signal": "URL does not start with http:// or https://", "points": "INSTANT REJECT"}],
                "total_score": None,
            },
        }

    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    path   = unquote(parsed.path.lower())
    factors = []

    # LinkedIn is allowed from human submitters (student/admin); the AI Observer
    # blocks it in its own pre-check before ever reaching this step.
    _human_blocklist = [b for b in _SOCIAL_BLOCKLIST if b != "linkedin.com"]
    for blocked in _human_blocklist:
        if blocked in domain:
            return {
                "passed": False,
                "detail": f"Domain '{domain}' is a social media platform",
                "final_status": "rejected",
                "breakdown": {
                    "domain": domain,
                    "rule_matched": "social_media_blocklist",
                    "factors": [{"signal": f"Domain is on social media blocklist ({blocked})", "points": "INSTANT REJECT"}],
                    "total_score": None,
                },
            }

    score = 0
    if domain.endswith(".gov.sa"):
        score += 3
        factors.append({"signal": "Domain ends in .gov.sa (Saudi government)", "points": +3})
    elif domain.endswith(".edu.sa"):
        score += 2
        factors.append({"signal": "Domain ends in .edu.sa (Saudi education)", "points": +2})
    elif domain.endswith(".sa"):
        score += 1
        factors.append({"signal": "Domain ends in .sa (Saudi domain)", "points": +1})

    if domain.endswith(".org"):
        score += 1
        factors.append({"signal": "Domain ends in .org", "points": +1})

    career_keywords = ["career", "jobs", "وظائف", "تدريب", "coop", "intern"]
    if any(kw in path for kw in career_keywords):
        score += 1
        factors.append({"signal": "Path contains a career/job keyword", "points": +1})

    if len(domain) < 5:
        score -= 2
        factors.append({"signal": f"Domain is suspiciously short ({len(domain)} chars)", "points": -2})

    if not factors:
        factors.append({"signal": "No trust signals found in domain or path", "points": 0})

    breakdown = {
        "domain": domain,
        "path": path,
        "rule_matched": "trust_scoring",
        "factors": factors,
        "total_score": score,
    }

    if score >= 1:
        return {"passed": True,  "detail": f"Domain trust score: {score}", "breakdown": breakdown}
    elif score == 0:
        return {"passed": True,  "detail": "Unverified domain, low trust", "breakdown": breakdown}
    else:
        return {"passed": False, "detail": f"Domain trust score too low: {score}", "final_status": "flagged", "breakdown": breakdown}


def _scrape_page(url: str) -> tuple:
    """Returns (text, success, chars_extracted). Uses real browser User-Agent to avoid bot blocking."""
    import ssl
    from requests.adapters import HTTPAdapter
    from urllib3.util.ssl_ import create_urllib3_context
    from bs4 import BeautifulSoup
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    _headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    class _LaxTLSAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            ctx = create_urllib3_context()
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            kwargs["ssl_context"] = ctx
            super().init_poolmanager(*args, **kwargs)

    def _fetch(session):
        resp = session.get(url, headers=_headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)[:3000]
        success = resp.status_code < 400 and resp.status_code not in (403, 429)
        return (text, success, len(text))

    try:
        with requests.Session() as s:
            return _fetch(s)
    except requests.exceptions.SSLError:
        try:
            with requests.Session() as s:
                s.mount("https://", _LaxTLSAdapter())
                return _fetch(s)
        except Exception:
            return ("", False, 0)
    except requests.exceptions.ConnectionError:
        return ("", False, 0)
    except requests.exceptions.Timeout:
        return ("", False, 0)
    except Exception:
        return ("", False, 0)


def _step3_availability(url: str) -> tuple:
    """Returns (step_result, page_text)."""
    text, success, chars = _scrape_page(url)

    if not success or not text:
        return (
            {
                "passed": False,
                "detail": "Could not reach the page",
                "final_status": "flagged",
                "breakdown": {
                    "scrape": {"success": False, "chars_extracted": 0},
                    "llm_answers": None,
                    "factors": [],
                    "total_score": None,
                },
            },
            "",
        )

    user_prompt = (
        "Analyze the following webpage text and answer these questions.\n"
        "Reply ONLY with a JSON object, nothing else.\n\n"
        "{\n"
        '  "q1": "Does the page contain an apply button or application form? (yes/no)",\n'
        '  "q2": "Does the page show any \'closed\', \'filled\', or \'no longer accepting\' message? (yes/no)",\n'
        '  "q3": "Does the page look like a real job or training posting page? (yes/no)",\n'
        '  "q4": "Is there any deadline date mentioned, and if so, has it passed? Answer: open / closed / no_date"\n'
        "}\n\n"
        f"WEBPAGE TEXT:\n{text[:3000]}"
    )

    llm_result = _call_llm(_AVAIL_SYSTEM, user_prompt)
    if llm_result is None:
        return (
            {
                "passed": False,
                "detail": "LLM availability analysis failed",
                "final_status": "flagged",
                "breakdown": {
                    "scrape": {"success": True, "chars_extracted": chars},
                    "llm_answers": None,
                    "factors": [],
                    "total_score": None,
                },
            },
            text,
        )

    score   = 0
    factors = []
    q1 = llm_result.get("q1", "no").lower()
    q2 = llm_result.get("q2", "no").lower()
    q3 = llm_result.get("q3", "no").lower()
    q4 = llm_result.get("q4", "no_date").lower().strip()

    if q1 == "yes":
        score += 2
        factors.append({"question": "Q1 · Apply button or form present?", "answer": "yes", "points": +2})
    else:
        factors.append({"question": "Q1 · Apply button or form present?", "answer": "no",  "points":  0})

    if q2 == "yes":
        score -= 3
        factors.append({"question": "Q2 · Closed / filled message found?", "answer": "yes", "points": -3})
    else:
        factors.append({"question": "Q2 · Closed / filled message found?", "answer": "no",  "points":  0})

    if q3 == "yes":
        score += 1
        factors.append({"question": "Q3 · Looks like a real job/training page?", "answer": "yes", "points": +1})
    else:
        factors.append({"question": "Q3 · Looks like a real job/training page?", "answer": "no",  "points":  0})

    if q4 == "open":
        score += 1
        factors.append({"question": "Q4 · Deadline status?", "answer": "open",    "points": +1})
    elif q4 == "closed":
        score -= 2
        factors.append({"question": "Q4 · Deadline status?", "answer": "closed",  "points": -2})
    else:
        factors.append({"question": "Q4 · Deadline status?", "answer": "no_date", "points":  0})

    breakdown = {
        "scrape": {"success": True, "chars_extracted": chars},
        "llm_answers": {"q1": q1, "q2": q2, "q3": q3, "q4": q4},
        "factors": factors,
        "total_score": score,
    }

    if score >= 2:
        return ({"passed": True,  "detail": f"Opportunity appears available (score: {score})", "breakdown": breakdown}, text)
    elif score >= 0:
        return ({"passed": True,  "detail": "Availability unclear, flagged for admin review", "final_status": "flagged", "breakdown": breakdown}, text)
    else:
        return ({"passed": False, "detail": "Opportunity appears closed", "final_status": "rejected", "breakdown": breakdown}, text)


def _step4_relevance(title: str, company: str, description: str, page_text: str) -> dict:
    user_prompt = (
        "A user submitted this co-op opportunity:\n"
        f"- Title: {title}\n"
        f"- Company: {company}\n"
        f"- Description: {description}\n\n"
        "The actual webpage content is:\n"
        f"{page_text[:3000]}\n\n"
        "Answer these questions. Reply ONLY with JSON, nothing else.\n\n"
        "{\n"
        '  "q1": "Does the company name on the page match or closely relate to the submitted company name? (yes/no)",\n'
        '  "q2": "Does the job title or role on the page match the submitted title? (yes/no)",\n'
        '  "q3": "Is this page about a co-op, internship, or training opportunity (not a full-time job)? (yes/no)",\n'
        '  "q4": "Does the page content generally match what the submitted description says? (yes/no)"\n'
        "}"
    )

    llm_result = _call_llm(_RELEV_SYSTEM, user_prompt)
    if llm_result is None:
        return {
            "passed": False,
            "detail": "LLM relevance analysis failed",
            "final_status": "flagged",
            "breakdown": {"llm_answers": None, "factors": [], "total_score": None, "max_score": 6},
        }

    score   = 0
    factors = []
    q1 = llm_result.get("q1", "no").lower()
    q2 = llm_result.get("q2", "no").lower()
    q3 = llm_result.get("q3", "no").lower()
    q4 = llm_result.get("q4", "no").lower()

    if q1 == "yes":
        score += 2
        factors.append({"question": "Q1 · Company name on page matches submitted company?", "answer": "yes", "points": +2})
    else:
        factors.append({"question": "Q1 · Company name on page matches submitted company?", "answer": "no",  "points":  0})

    if q2 == "yes":
        score += 2
        factors.append({"question": "Q2 · Job title on page matches submitted title?",      "answer": "yes", "points": +2})
    else:
        factors.append({"question": "Q2 · Job title on page matches submitted title?",      "answer": "no",  "points":  0})

    if q3 == "yes":
        score += 1
        factors.append({"question": "Q3 · Page is about co-op/internship (not full-time)?", "answer": "yes", "points": +1})
    else:
        factors.append({"question": "Q3 · Page is about co-op/internship (not full-time)?", "answer": "no",  "points":  0})

    if q4 == "yes":
        score += 1
        factors.append({"question": "Q4 · Page content matches submitted description?",     "answer": "yes", "points": +1})
    else:
        factors.append({"question": "Q4 · Page content matches submitted description?",     "answer": "no",  "points":  0})

    breakdown = {
        "llm_answers": {"q1": q1, "q2": q2, "q3": q3, "q4": q4},
        "factors": factors,
        "total_score": score,
        "max_score": 6,
    }

    if score >= 5:
        return {"passed": True,  "detail": f"Strong match (score: {score}/6)",                    "final_status": "approved", "breakdown": breakdown}
    elif score >= 3:
        return {"passed": True,  "detail": f"Partial match, needs admin review (score: {score}/6)", "final_status": "flagged",  "breakdown": breakdown}
    else:
        return {"passed": False, "detail": "Submitted fields do not match the URL content",         "final_status": "rejected", "breakdown": breakdown}


def build_validation_note(link_result: dict, url: str = "", title: str = "", company: str = "") -> str:
    """
    Builds the SubValidation.note string from a link_validator result.

    Line 1  — machine-parseable: "AI Confidence: XX%. <human-friendly reason>"
    Rest    — full CLI-style detailed report shown in the admin Raw modal.
    """
    fs    = link_result["final_status"]
    step  = link_result["step"]
    steps = link_result["steps"]

    # Confidence score
    if fs == "approved":
        confidence_pct = 100
    elif fs == "flagged":
        confidence_pct = {4: 75, 3: 50, 2: 25}.get(step, 25)
    else:  # rejected
        confidence_pct = {4: 40, 3: 30, 2: 15, 1: 0}.get(step, 0)

    # Human-friendly reason for the table Reason column
    if fs == "approved":
        reason = "All 4 steps passed."
    else:
        _short_labels = [
            ("step1_safety",       "Step 1 (Safety)"),
            ("step2_authenticity", "Step 2 (Authenticity)"),
            ("step3_availability", "Step 3 (Availability)"),
            ("step4_relevance",    "Step 4 (Relevance)"),
        ]
        parts = []
        for key, label in _short_labels:
            s = steps.get(key)
            if s and (not s["passed"] or s.get("final_status") in ("flagged", "rejected")):
                parts.append(f"{label}: {s.get('detail', '')}")
        reason = " | ".join(parts) if parts else f"Step {step} completed."
        if len(reason) > 200:
            reason = reason[:197] + "..."

    # ── Full CLI-style detailed report ────────────────────────────────────────
    W = 66
    _ICONS = {"approved": "✓ APPROVED", "flagged": "⚠ FLAGGED", "rejected": "✗ REJECTED"}
    _STEP_TITLES = {
        "step1_safety":       "STEP 1 · SAFETY CHECK",
        "step2_authenticity": "STEP 2 · AUTHENTICITY CHECK",
        "step3_availability": "STEP 3 · AVAILABILITY CHECK",
        "step4_relevance":    "STEP 4 · RELEVANCE CHECK",
    }

    def _ln(char="─"):
        return char * W

    def _header(t, lbl):
        left  = f"  {t}"
        right = f"  {lbl}  "
        gap   = W - len(left) - len(right)
        return left + " " * max(gap, 1) + right

    def _pts(p):
        if p is None or isinstance(p, str):
            return str(p)
        return f"+{p}" if p > 0 else str(p)

    def _r1(data, out):
        bd = data.get("breakdown", {})
        out.append("  Score Breakdown:")
        if bd.get("skipped"):
            out.append(f"    Reason  : {bd.get('reason', 'n/a')}")
        else:
            out.append(f"    Malicious engines  :  {bd.get('malicious_engines',  0):>3}   (any → REJECTED)")
            out.append(f"    Suspicious engines :  {bd.get('suspicious_engines', 0):>3}   (any → FLAGGED)")
            out.append(f"    Harmless engines   :  {bd.get('harmless_engines',   0):>3}")
            out.append(f"    Undetected engines :  {bd.get('undetected_engines', 0):>3}")
            verdict = ("MALICIOUS" if bd.get("malicious_engines", 0)
                       else "SUSPICIOUS" if bd.get("suspicious_engines", 0) else "CLEAN ✓")
            out.append(f"    Verdict            :  {verdict}")

    def _r2(data, out):
        bd   = data.get("breakdown", {})
        rule = bd.get("rule_matched", "")
        out.append(f"  Domain  : {bd.get('domain', 'n/a')}")
        if rule == "social_media_blocklist":
            out.append("  Rule    : Social media blocklist — instant reject")
        elif rule == "ats_whitelist":
            out.append("  Rule    : Trusted ATS whitelist — instant pass")
        else:
            out.append(f"  Path    : {bd.get('path', 'n/a')}")
            out.append("  Rule    : Domain trust scoring")
        factors = bd.get("factors", [])
        if factors:
            out.append("")
            out.append("  Score Breakdown:")
            col = 46
            for f in factors:
                out.append(f"    {f['signal']:<{col}}  {_pts(f['points'])}")
            score = bd.get("total_score")
            if score is not None:
                out.append(f"    {'─' * col}  ────")
                out.append(f"    {'TOTAL':<{col}}  {score}")
                out.append(f"    {'Threshold: ≥1 PASSED · =0 PASSED (low trust) · <0 FLAGGED':<{col}}")

    def _r3(data, out):
        bd     = data.get("breakdown", {})
        scrape = bd.get("scrape", {})
        if scrape.get("success"):
            out.append(f"  Scraping : Page reached successfully — {scrape.get('chars_extracted', 0):,} characters extracted")
        else:
            out.append("  Scraping : Could not reach the page")
        factors = bd.get("factors", [])
        if not factors:
            return
        out.append("")
        out.append("  LLM Analysis  (Groq · LLaMA 3.1 8B Instant):")
        col_q = 48
        for f in factors:
            out.append(f"    {f['question']:<{col_q}}  {f['answer']:<8}  →  {_pts(f['points'])}")
        score = bd.get("total_score")
        if score is not None:
            out.append(f"    {'─' * col_q}  ──────────────")
            out.append(f"    {'TOTAL SCORE':<{col_q}}  {score}")
            out.append(f"    Threshold: ≥2 PASSED · 0-1 FLAGGED · <0 REJECTED")

    def _r4(data, out):
        bd      = data.get("breakdown", {})
        factors = bd.get("factors", [])
        if not factors:
            return
        out.append("  LLM Analysis  (Groq · LLaMA 3.1 8B Instant):")
        col_q = 54
        for f in factors:
            out.append(f"    {f['question']:<{col_q}}  {f['answer']:<4}  →  {_pts(f['points'])}")
        score     = bd.get("total_score")
        max_score = bd.get("max_score", 6)
        if score is not None:
            out.append(f"    {'─' * col_q}  ──────────")
            out.append(f"    {'TOTAL SCORE':<{col_q}}  {score}/{max_score}")
            out.append(f"    Threshold: ≥5 APPROVED · 3-4 FLAGGED · <3 REJECTED")

    _renderers = {
        "step1_safety":       _r1,
        "step2_authenticity": _r2,
        "step3_availability": _r3,
        "step4_relevance":    _r4,
    }

    lines = []
    lines.append(_ln("═"))
    lines.append("  COOPSTATION LINK VALIDATOR — DETAILED REPORT")
    lines.append(_ln("═"))
    if url:
        lines.append(f"  URL        :  {url}")
    if title:
        lines.append(f"  Title      :  {title}")
    if company:
        lines.append(f"  Company    :  {company}")
    lines.append("")

    for key, step_title in _STEP_TITLES.items():
        step_data = steps.get(key)
        if step_data is None:
            lines.append(_ln())
            lines.append(f"  {step_title:<48}  — SKIPPED")
            lines.append(_ln())
            lines.append("")
            continue
        passed = step_data["passed"]
        s_fs   = step_data.get("final_status", "approved" if passed else "rejected")
        label  = _ICONS.get(s_fs, s_fs.upper())
        lines.append(_ln("═"))
        lines.append(_header(step_title, label))
        lines.append(_ln("═"))
        lines.append(f"  Summary  :  {step_data.get('detail', '')}")
        lines.append("")
        _renderers[key](step_data, lines)
        lines.append("")

    icon = _ICONS.get(fs, fs.upper())
    lines.append(_ln("═"))
    lines.append("  FINAL VERDICT")
    lines.append(_ln("═"))
    lines.append(f"  Status    :  {icon}")
    lines.append(f"  Steps run :  {step} / 4")
    if fs in ("flagged", "rejected"):
        lines.append("")
        lines.append(f"  {reason}")
    lines.append(_ln("═"))

    return f"AI Confidence: {confidence_pct}%. {reason}\n" + "\n".join(lines)


def validate_submission(
    url: str, title: str, company: str, description: str,
    skip_to_step: int = 1, stop_after_step: int = 4,
) -> dict:
    """
    Runs validation steps in order, short-circuiting on failure.

    skip_to_step=1   — run all 4 steps (default, used for student/admin submissions)
    skip_to_step=3   — mark steps 1+2 as pre-checked, run only steps 3+4
                       (used by the Observer which runs steps 1+2 itself)
    stop_after_step=3 — stop and return after step 3, skip step 4
                        (used by the Status Monitor — availability check only)
    """
    _SKIPPED = {"passed": True, "detail": "Skipped — pre-checked by Observer"}
    steps_output = {
        "step1_safety":       None,
        "step2_authenticity": None,
        "step3_availability": None,
        "step4_relevance":    None,
    }
    overall_status = "approved"
    overall_passed = True

    if skip_to_step <= 1:
        r1 = _step1_safety(url)
        steps_output["step1_safety"] = r1
        if not r1["passed"]:
            return _build_result(1, False, r1.get("final_status", "rejected"), steps_output)
        if r1.get("final_status"):
            overall_status = _worse(overall_status, r1["final_status"])
    else:
        steps_output["step1_safety"] = dict(_SKIPPED)

    if skip_to_step <= 2:
        r2 = _step2_authenticity(url)
        steps_output["step2_authenticity"] = r2
        if not r2["passed"]:
            return _build_result(2, False, r2.get("final_status", "rejected"), steps_output)
        if r2.get("final_status"):
            overall_status = _worse(overall_status, r2["final_status"])
    else:
        steps_output["step2_authenticity"] = dict(_SKIPPED)

    r3, page_text = _step3_availability(url)
    steps_output["step3_availability"] = r3
    if not r3["passed"]:
        return _build_result(3, False, r3.get("final_status", "rejected"), steps_output)
    if r3.get("final_status"):
        overall_status = _worse(overall_status, r3["final_status"])

    if stop_after_step == 3:
        steps_output["step4_relevance"] = {"passed": True, "detail": "Skipped — Status Monitor only runs Step 3"}
        return _build_result(3, True, overall_status, steps_output)

    r4 = _step4_relevance(title, company, description, page_text)
    steps_output["step4_relevance"] = r4
    if not r4["passed"]:
        return _build_result(4, False, r4.get("final_status", "rejected"), steps_output)
    if r4.get("final_status"):
        overall_status = _worse(overall_status, r4["final_status"])

    return _build_result(4, overall_passed, overall_status, steps_output)
