import json

from openai import OpenAI
from django.conf import settings


def validate_submission(title, company, description, source_link, existing_opportunities):
    """
    Calls Groq (llama-3.1-8b-instant) to validate a submitted co-op opportunity.

    Returns:
        {"result": "pass"|"fail"|"unsure", "confidence": float, "reason": str}
    """
    if not settings.GROQ_API_KEY:
        return {
            "result": "unsure",
            "confidence": 0.0,
            "reason": "AI validation is not configured (missing API key).",
        }

    existing_list = "\n".join(
        f"- {o.title} at {o.company}" for o in existing_opportunities[:30]
    )

    prompt = f"""
You are a validation assistant for a cooperative training (internship/co-op)
platform for university students in Saudi Arabia.

A student submitted the following opportunity:

Title: {title}
Company: {company}
Description: {description or "Not provided"}
Source Link: {source_link or "Not provided"}

Here are the currently listed opportunities on the platform (to check for duplicates):
{existing_list or "No existing opportunities yet."}

Your job is to evaluate this submission and return ONLY a JSON object
with these exact keys — no extra text, no markdown, no explanation outside the JSON:

{{
  "result": "pass" | "fail" | "unsure",
  "confidence": <float between 0.0 and 1.0>,
  "reason": "<one sentence explanation>"
}}

Rules for your decision:
- "pass" → Looks like a real co-op/internship opportunity.
  Not a duplicate. Link provided. Company name is real.
- "fail" → Clearly fake, spam, missing critical info (no title or company),
  or is an exact duplicate of an existing listing.
- "unsure" → Something seems off but not conclusive
  (e.g. vague description, unusual company name, no source link).

Confidence should reflect how certain you are of your result (0.0 = not sure, 1.0 = very sure).
Respond ONLY with the JSON. No preamble. No markdown code blocks.
"""

    try:
        client = OpenAI(
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        text = response.choices[0].message.content.strip()

        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        data = json.loads(text.strip())

        result = data.get("result", "unsure").lower()
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        reason = data.get("reason", "No reason provided.")

        if result not in ("pass", "fail", "unsure"):
            result = "unsure"

        return {"result": result, "confidence": confidence, "reason": reason}

    except Exception as e:
        return {
            "result": "unsure",
            "confidence": 0.0,
            "reason": f"AI validation failed: {str(e)}",
        }
