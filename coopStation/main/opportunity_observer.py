import json
import requests
from datetime import datetime
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone
from openai import OpenAI
from bs4 import BeautifulSoup

FIXED_QUERIES = [
    "co-op training opportunities Saudi Arabia 2026",
    "cooperative training internship Saudi Arabia 2026",
]

MAX_RESULTS_PER_QUERY = 8
REQUEST_TIMEOUT = 10


def _now_str():
    return datetime.now().strftime("%H:%M:%S")


def _log_step(step_log, step, detail=""):
    step_log.append({"time": _now_str(), "step": step, "detail": detail})


def get_groq_search_queries():
    if not settings.GROQ_API_KEY:
        return []
    try:
        client = OpenAI(
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "user",
                "content": (
                    "You are helping find cooperative training (co-op/internship) "
                    "opportunities for university students in Saudi Arabia for 2026.\n\n"
                    "Generate 3 diverse Google search queries to find these opportunities.\n"
                    "Consider using Arabic queries, specific company names, or different phrasings.\n"
                    "Scope: cooperative training / co-op / internship / تدريب تعاوني\n"
                    "Location: Saudi Arabia only\n"
                    "Year: 2026\n\n"
                    "Return ONLY a JSON array of 3 strings. No explanation. No markdown.\n"
                    'Example: ["query one", "query two", "query three"]'
                ),
            }],
            temperature=0.7,
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        queries = json.loads(text.strip())
        if isinstance(queries, list):
            return [str(q) for q in queries[:3]]
        return []
    except Exception:
        return []


def search_serper(query):
    if not settings.SERPER_API_KEY:
        return []
    try:
        response = requests.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": settings.SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "q": query,
                "num": MAX_RESULTS_PER_QUERY,
                "gl": "sa",
                "hl": "en",
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = response.json()
        results = []
        for item in data.get("organic", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })
        return results
    except Exception:
        return []


def fetch_page_text(url):
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; CoopStationBot/1.0; "
                    "+https://coopstation.sa)"
                )
            },
        )
        http_status = resp.status_code
        if http_status != 200:
            return ("", http_status)

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer",
                         "header", "aside", "iframe"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        text = text[:3000]
        return (text, http_status)
    except Exception:
        return ("", 0)


def extract_opportunity_with_groq(title, snippet, page_text, url):
    if not settings.GROQ_API_KEY:
        return {"is_opportunity": False, "confidence": 0.0,
                "reason": "Groq not configured."}

    content = page_text if page_text else snippet

    try:
        client = OpenAI(
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "user",
                "content": (
                    "You are extracting cooperative training (co-op/internship) "
                    "opportunity data for a platform for university students in Saudi Arabia.\n\n"
                    f"Page title from search: {title}\n"
                    f"Source URL: {url}\n"
                    f"Page content:\n{content}\n\n"
                    "Determine if this page contains a real co-op or internship opportunity "
                    "for university students in Saudi Arabia.\n\n"
                    "Return ONLY a JSON object with these exact keys, no extra text:\n"
                    "{\n"
                    '  "is_opportunity": true or false,\n'
                    '  "extracted_title": "job title or empty string",\n'
                    '  "company": "company name or empty string",\n'
                    '  "location": "city, Saudi Arabia or empty string",\n'
                    '  "deadline": "YYYY-MM-DD or empty string",\n'
                    '  "description": "1-2 sentence description or empty string",\n'
                    '  "confidence": 0.0 to 1.0,\n'
                    '  "reason": "one sentence explanation"\n'
                    "}\n\n"
                    "Rules:\n"
                    "- is_opportunity = true only if this is clearly a co-op/internship for students\n"
                    "- If no deadline found, leave deadline as empty string\n"
                    "- Location should be a Saudi city if found, otherwise \"Saudi Arabia\"\n"
                    "- confidence reflects how sure you are this is a real student opportunity"
                ),
            }],
            temperature=0,
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        return data
    except Exception as e:
        return {
            "is_opportunity": False,
            "confidence": 0.0,
            "reason": f"Groq extraction failed: {str(e)}",
        }


def url_already_exists(url):
    from .models import Opportunity
    return Opportunity.objects.filter(source_link=url).exists()


def _detect_category(title: str, description: str, categories: dict) -> object:
    """
    Returns the best-matching OppCategory for a given title+description using
    keyword matching only (no LLM). Falls back to 'General' if nothing matches.

    `categories` is a dict mapping category name → OppCategory instance.
    Rules are checked in order; the first match wins.
    """
    text = (title + " " + (description or "")).lower()

    # Each rule: (category_name, [keywords_any_of_which_triggers_it])
    # Order matters — more specific first.
    RULES = [
        ("Cybersecurity", [
            "cybersecurity", "cyber security", "information security", "أمن المعلومات",
            "network security", "penetration", "soc analyst", "أمن سيبراني",
        ]),
        ("Data & AI", [
            "machine learning", "artificial intelligence", "data science", "deep learning",
            "nlp", "computer vision", "تعلم الآلة", "ذكاء اصطناعي", "data analyst",
            "ai &", "& ai", "ai co-op", "analytics",
        ]),
        ("Software Engineering", [
            "software engineer", "software development", "mobile development",
            "android", "ios", "flutter", "react native", "full stack", "fullstack",
            "backend", "frontend", "هندسة البرمجيات", "تطوير البرمجيات",
        ]),
        ("Information Systems", [
            "information systems", "نظم المعلومات", "نظام المعلومات",
        ]),
        ("Computer Science & IT", [
            "it co-op", "it infrastructure", "information technology", "computer science",
            "it trainee", "it intern", "network engineer", "systems administrator",
            "cloud engineer", "cloud computing", "devops", "علوم الحاسب",
            "تقنية المعلومات", "الخدمات الإلكترونية",
        ]),
        ("Telecommunications", [
            "telecom", "telecommunication", "network engineer", "5g", "rf engineer",
            "اتصالات", "شبكات الاتصال",
        ]),
        ("Finance", [
            "finance", "investment", "banking", "treasury", "wealth management",
            "financial analyst", "tadawul", "stock exchange", "تمويل", "مالية",
            "استثمار", "بنك", "مصرف", "sidf",
        ]),
        ("Accounting", [
            "accounting", "accountant", "audit", "external audit", "internal audit",
            "محاسبة", "محاسب", "مراجعة",
        ]),
        ("Marketing", [
            "marketing", "digital marketing", "brand", "social media marketing",
            "content creator", "seo", "تسويق", "تسويق رقمي",
        ]),
        ("Design (UI/UX & Graphic)", [
            "ux/ui", "ux design", "ui design", "user experience", "user interface",
            "graphic design", "visual design", "product design",
            "interior design", "تصميم داخلي", "الداخلي", "تصميم جرافيك", "تصميم بصري",
        ]),
        ("Architecture & Planning", [
            "architecture", "urban planning", "civil planning", "هندسة معمارية",
            "تخطيط عمراني",
        ]),
        ("Engineering", [
            "engineering", "mechanical", "electrical", "civil", "chemical",
            "industrial engineer", "aerospace", "aviation", "construction",
            "network engineering", "cloud engineering", "هندسة", "ميكانيكا",
            "كهربائي", "مدني", "ملاحة جوية", "ملاحة", "طيران",
        ]),
        ("Project Management", [
            "project management", "pmo", "program manager", "scrum", "agile",
            "إدارة المشاريع", "مدير مشروع",
        ]),
        ("Business & Management", [
            "business", "management", "operations", "supply chain", "logistics",
            "human resources", "hr co-op", "procurement", "strategy", "consulting",
            "pwc", "bain", "mckinsey", "deloitte", "hadaf", "هدف", "إدارة أعمال",
            "إدارة", "موارد بشرية", "سلسلة التوريد", "contracts", "quality",
        ]),
        ("Law", [
            "law", "legal", "compliance", "contracts lawyer", "قانون", "قانوني",
            "امتثال",
        ]),
        ("Shariah & Islamic Studies", [
            "shariah", "islamic studies", "fiqh", "شريعة", "دراسات إسلامية", "فقه",
        ]),
        ("Healthcare", [
            "healthcare", "health care", "medical", "hospital", "clinical",
            "رعاية صحية", "طبي", "مستشفى",
        ]),
        ("Pharmacy", [
            "pharmacy", "pharmacist", "pharmaceutical", "صيدلة", "صيدلاني",
        ]),
        ("Education", [
            "education", "teaching", "teacher", "curriculum", "e-learning",
            "تعليم", "تدريس", "معلم",
        ]),
        ("Media & Communications", [
            "media", "journalism", "broadcasting", "public relations",
            "إعلام", "صحافة", "علاقات عامة",
        ]),
        ("Arts & Humanities", [
            "arts", "humanities", "literature", "history", "آداب", "إنسانيات",
        ]),
        ("Agriculture & Environmental", [
            "agriculture", "environment", "sustainability", "زراعة", "بيئة",
            "استدامة",
        ]),
    ]

    for cat_name, keywords in RULES:
        if cat_name not in categories:
            continue
        if any(kw in text for kw in keywords):
            return categories[cat_name]

    return categories.get("General") or categories.get("Other") or next(iter(categories.values()))


def run_fetch_session(admin):
    from .models import (
        FetchSession, FetchSessionStatus, AIDiscovery,
        Opportunity, OppCategory, Submission, SubmissionStatus,
        SubmitterType, SubValidation, ValidationResult,
        ParsedState, AuditLog,
    )
    from django.db import transaction
    import dateutil.parser as date_parser
    from .link_validator import (
        _SOCIAL_BLOCKLIST, _step1_safety, _step2_authenticity,
        validate_submission, build_validation_note,
    )

    session = FetchSession.objects.create(initiated_by=admin)

    all_queries = FIXED_QUERIES.copy()
    groq_queries = get_groq_search_queries()
    all_queries.extend(groq_queries)

    session.total_searches = len(all_queries)
    session.save(update_fields=["total_searches"])

    all_categories = {c.name: c for c in OppCategory.objects.all()}

    seen_urls = set()

    for query in all_queries:
        serper_results = search_serper(query)

        for item in serper_results:
            url = item.get("url", "")
            title = item.get("title", "")
            snippet = item.get("snippet", "")

            if not url:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)

            step_log = []
            result_label = "failed"
            parsed_status = ParsedState.UNSURE
            http_status = 0
            confidence = 0.0
            evidence = ""
            created_sub = None

            _log_step(step_log, "🔍 Serper returned URL",
                      f"Query: {query} | Title: {title}")

            # Already in DB — skip immediately
            if url_already_exists(url):
                _log_step(step_log, "⚠️ Skipped", "URL already exists in database")
                result_label = "skipped"
                parsed_status = ParsedState.NOT_OPPORTUNITY
                AIDiscovery.objects.create(
                    fetch_session=session,
                    query=query,
                    fetch_url=url,
                    fetched_at=timezone.now(),
                    http_status=0,
                    parsed_status=parsed_status,
                    confidence=0,
                    evidence="URL already in database",
                    is_relevant=False,
                    result_label=result_label,
                    step_log=step_log,
                )
                session.total_skipped += 1
                session.save(update_fields=["total_skipped"])
                continue

            # STEP A — Social media blocklist
            _domain = urlparse(url).netloc.lower().removeprefix("www.")
            if any(blocked in _domain for blocked in _SOCIAL_BLOCKLIST):
                _log_step(step_log, "⚠️ Skipped",
                          f"Social media domain ({_domain})")
                result_label = "skipped"
                parsed_status = ParsedState.NOT_OPPORTUNITY
                AIDiscovery.objects.create(
                    fetch_session=session,
                    query=query,
                    fetch_url=url,
                    fetched_at=timezone.now(),
                    http_status=0,
                    parsed_status=parsed_status,
                    confidence=0,
                    evidence="Social media domain",
                    is_relevant=False,
                    result_label=result_label,
                    step_log=step_log,
                )
                session.total_skipped += 1
                session.save(update_fields=["total_skipped"])
                continue

            # STEP B — Safety check
            r_safety = _step1_safety(url)
            _log_step(step_log, "🛡️ Safety check", r_safety.get("detail", ""))
            if not r_safety["passed"]:
                _log_step(step_log, "❌ Failed — safety check", r_safety.get("detail", ""))
                AIDiscovery.objects.create(
                    fetch_session=session,
                    query=query,
                    fetch_url=url,
                    fetched_at=timezone.now(),
                    http_status=0,
                    parsed_status=ParsedState.UNSURE,
                    confidence=0,
                    evidence=r_safety.get("detail", ""),
                    is_relevant=False,
                    result_label="failed",
                    step_log=step_log,
                )
                session.total_found += 1
                session.save(update_fields=["total_found"])
                continue

            # STEP C — Authenticity check
            r_auth = _step2_authenticity(url)
            _log_step(step_log, "🔒 Authenticity check", r_auth.get("detail", ""))
            if not r_auth["passed"]:
                _log_step(step_log, "❌ Failed — authenticity check", r_auth.get("detail", ""))
                AIDiscovery.objects.create(
                    fetch_session=session,
                    query=query,
                    fetch_url=url,
                    fetched_at=timezone.now(),
                    http_status=0,
                    parsed_status=ParsedState.NOT_OPPORTUNITY,
                    confidence=0,
                    evidence=r_auth.get("detail", ""),
                    is_relevant=False,
                    result_label="failed",
                    step_log=step_log,
                )
                session.total_found += 1
                session.save(update_fields=["total_found"])
                continue

            # Fetch page and extract structured data (title, company, description)
            page_text, http_status = fetch_page_text(url)
            _log_step(step_log, "📄 BeautifulSoup fetched page",
                      f"HTTP {http_status} | {len(page_text)} chars extracted")

            extracted = extract_opportunity_with_groq(title, snippet, page_text, url)
            confidence = extracted.get("confidence", 0.0)
            evidence = extracted.get("reason", "")
            opp_title = extracted.get("extracted_title", "") or title
            opp_company = extracted.get("company", "")
            opp_description = extracted.get("description", "")

            _log_step(step_log, "🤖 Groq extracted data",
                      f"confidence={confidence:.0%} | "
                      f"title={opp_title} | "
                      f"company={opp_company} | "
                      f"reason={evidence}")

            # STEP D — Run validator (steps 3+4 only; steps 1+2 already checked above)
            val_result = validate_submission(
                url=url,
                title=opp_title,
                company=opp_company,
                description=opp_description,
                skip_to_step=3,
            )
            fs = val_result["final_status"]
            _log_step(step_log, "🔬 Validator result",
                      f"final_status={fs} | steps run: {val_result['step']}/4")

            if fs == "rejected":
                reject_detail = next(
                    (val_result["steps"].get(k, {}).get("detail", "")
                     for k in ("step4_relevance", "step3_availability")
                     if val_result["steps"].get(k)),
                    "",
                )
                _log_step(step_log, "❌ Rejected by validator", reject_detail)
                AIDiscovery.objects.create(
                    fetch_session=session,
                    query=query,
                    fetch_url=url,
                    fetched_at=timezone.now(),
                    http_status=http_status,
                    parsed_status=ParsedState.NOT_OPPORTUNITY,
                    confidence=confidence,
                    evidence=evidence,
                    is_relevant=False,
                    result_label="failed",
                    step_log=step_log,
                )
                session.total_found += 1
                session.save(update_fields=["total_found"])
                continue

            # Approved or flagged — create opportunity in the database
            deadline = None
            raw_deadline = extracted.get("deadline", "")
            if raw_deadline:
                try:
                    deadline = date_parser.parse(raw_deadline).date()
                except Exception:
                    deadline = None

            from datetime import date as _date
            opp_status_val = "closed" if (deadline and deadline < _date.today()) else "open"
            sub_status      = SubmissionStatus.APPROVED if fs == "approved" else SubmissionStatus.PENDING
            val_result_code = ValidationResult.PASS    if fs == "approved" else ValidationResult.PENDING

            try:
                with transaction.atomic():
                    opp = Opportunity.objects.create(
                        title=opp_title,
                        company=opp_company,
                        description=opp_description,
                        location=extracted.get("location", "Saudi Arabia"),
                        deadline=deadline,
                        source_link=url,
                        status=opp_status_val,
                        category=_detect_category(opp_title, opp_description, all_categories),
                    )

                    sub = Submission(
                        opportunity=opp,
                        submitted_by_type=SubmitterType.AI,
                        status=sub_status,
                        decision_at=timezone.now() if fs == "approved" else None,
                    )
                    sub.save()

                    val_note = build_validation_note(
                        val_result, url=url, title=opp_title, company=opp_company
                    )
                    SubValidation.objects.create(
                        submission=sub,
                        result=val_result_code,
                        note=val_note,
                    )

                    created_sub = sub
                    parsed_status = ParsedState.OPEN
                    result_label = "added" if fs == "approved" else "flagged"

                    action_label = (
                        "✅ Added to platform"
                        if fs == "approved"
                        else "⏳ Added — flagged for admin review"
                    )
                    _log_step(step_log, action_label,
                              f"Opportunity ID: {opp.id} | "
                              f"Title: {opp.title} | "
                              f"Company: {opp.company}")

                    session.total_added += 1
                    session.save(update_fields=["total_added"])

            except Exception as e:
                result_label = "failed"
                parsed_status = ParsedState.UNSURE
                _log_step(step_log, "❌ Database save failed", str(e))

            AIDiscovery.objects.create(
                fetch_session=session,
                query=query,
                fetch_url=url,
                fetched_at=timezone.now(),
                http_status=http_status,
                parsed_status=parsed_status,
                confidence=confidence,
                evidence=evidence,
                is_relevant=(result_label in ("added", "flagged")),
                created_submission=created_sub,
                result_label=result_label,
                step_log=step_log,
            )

            session.total_found += 1
            session.save(update_fields=["total_found"])

    session.status = FetchSessionStatus.COMPLETED
    session.finished_at = timezone.now()
    session.save(update_fields=["status", "finished_at"])

    AuditLog.objects.create(
        admin=admin,
        action="fetch_opportunities",
        target_type="fetch_session",
        target_id=session.id,
        target_label=f"Session #{session.id}",
        note=(
            f"Found {session.total_found}, "
            f"Added {session.total_added}, "
            f"Skipped {session.total_skipped}"
        ),
    )

    return session
