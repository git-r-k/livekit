"""HR evaluation of a screening transcript via OpenAI.

Used by the Flask server to lazy-generate `{room}_evaluation.json` on demand.
Keeping this out of the LiveKit agent's shutdown path avoids the 10-second
shutdown timeout killing the OpenAI round-trip.
"""

import json
import os
from datetime import datetime, timezone

from openai import OpenAI


EVAL_SCHEMA_HINT = """{
  "candidate_info": {
    "current_company": null,
    "current_role": null,
    "total_years_experience": null,
    "current_ctc": null,
    "expected_ctc": null,
    "notice_period": null,
    "earliest_join_in_days": null,
    "current_location": null,
    "preferred_work_mode": null,
    "open_to_relocation": null
  },
  "tech_stack": {
    "languages": [],
    "frontend": [],
    "backend": [],
    "databases": [],
    "cloud_devops": [],
    "other_tools": []
  },
  "technical_assessment": {
    "questions": [
      {
        "topic": "current_role | tech_stack | recent_project | system_design | debugging | tradeoffs | other",
        "question_asked": "what the interviewer asked, in 1 line",
        "candidate_answer_summary": "1-2 line factual summary of what the candidate said",
        "depth": "shallow | adequate | strong",
        "score_out_of_10": 0,
        "notes": "what was good / missing"
      }
    ],
    "average_score": 0
  },
  "scores": {
    "communication_clarity": {"score": 0, "notes": ""},
    "technical_depth":       {"score": 0, "notes": ""},
    "problem_solving":       {"score": 0, "notes": ""},
    "ownership_initiative":  {"score": 0, "notes": ""},
    "english_fluency":       {"score": 0, "notes": ""},
    "confidence":            {"score": 0, "notes": ""},
    "overall_fit_for_role":  {"score": 0, "notes": ""}
  },
  "summary": "2-3 sentence neutral summary for HR",
  "strengths": [],
  "concerns": [],
  "red_flags": [],
  "recommendation": "strong_yes | yes | maybe | no | strong_no",
  "recommendation_reasoning": "",
  "follow_up_questions_for_next_round": []
}"""


MENTORSHIP_SCHEMA_HINT = """{
  "summary": "2-3 sentence neutral summary of how the call went and the student's current state",
  "sentiment": "positive | neutral | concerned | distressed",
  "engagement": "high | medium | low",
  "takeaways": [
    "first short takeaway, 1 sentence",
    "second short takeaway, 1 sentence",
    "third short takeaway, 1 sentence"
  ],
  "action_items": [
    {"key": "action_1", "text": "concrete next step the student should take, 1 sentence", "category": "study | practice | mentorship | wellness | logistics"},
    {"key": "action_2", "text": "concrete next step the student should take, 1 sentence", "category": "study | practice | mentorship | wellness | logistics"}
  ],
  "topics_discussed": [],
  "concerns_flagged": [],
  "follow_up_for_batch_lead": ""
}"""


def _build_transcript(recorder_data: dict) -> str:
    lines = []
    for ev in recorder_data.get("events", []):
        if ev.get("type") != "message":
            continue
        ev_role = ev.get("role")
        text = ev.get("text")
        if not text or not ev_role:
            continue
        speaker = "MIRA" if ev_role == "assistant" else "CANDIDATE"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines).strip()


def _run_openai_json(system: str, transcript: str) -> dict:
    client = OpenAI()
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_EVAL_MODEL", "gpt-4o-mini"),
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"TRANSCRIPT:\n\n{transcript}\n\nProduce the JSON evaluation now.",
            },
        ],
    )
    return json.loads(resp.choices[0].message.content)


def evaluate_transcript(
    recorder_data: dict, role: str, company: str, candidate_name: str
) -> dict | None:
    """Synchronous screening-mode evaluation. Returns parsed JSON or None if no transcript."""
    transcript = _build_transcript(recorder_data)
    if not transcript:
        return None

    system = f"""You are a senior technical recruiter analyzing a phone-screening transcript so HR can decide whether to advance the candidate.

Role being screened: {role}
Company: {company}
Candidate name: {candidate_name or "(not provided)"}

Return STRICT JSON in exactly this shape (use null when not discussed; never fabricate; keep arrays empty when nothing applies):
{EVAL_SCHEMA_HINT}

Scoring anchor (1-10):
- 1-3: red flag, unable to communicate, or wrong fit
- 4-5: junior level, lacks SDE-2 depth
- 6-7: solid SDE-2 signal
- 8-10: exceptional, hire fast

Rules:
- "confidence" is judged from specificity, depth, and lack of hedging — NOT volume.
- "english_fluency" reflects clarity in English; ignore accent.
- "technical_assessment.questions" must list every technical/screening question the recruiter actually asked. If the candidate dodged, score low and note it.
- For ctc/notice_period, extract verbatim where possible (e.g. "4.5 LPA", "30 days").
- Be honest. Low scores are fine when warranted.
- "red_flags" should include things like: refusal to discuss compensation, unable to describe own project, evasiveness about current role, asking the interviewer to teach concepts, etc.
"""
    return _run_openai_json(system, transcript)


def evaluate_mentorship_transcript(
    recorder_data: dict, student_name: str
) -> dict | None:
    """Mentorship-mode evaluation: a Miss Ozone nudge call with a student.

    Output is shaped for the student-dashboard summary card:
    3 short takeaways + 2 concrete action items.
    """
    transcript = _build_transcript(recorder_data)
    if not transcript:
        return None

    system = f"""You are an academic mentor reviewing the transcript of a short AI-led check-in call between Miss Ozone (an AI mentor) and a student named {student_name or "(not provided)"} at MAS (My Analytics School).

The goal of these calls is to nudge, motivate, and surface concerns — not to interview or score the student. Your job is to read the transcript and produce a JSON summary the student and their batch lead can act on.

Return STRICT JSON in exactly this shape (use empty arrays when nothing applies; never fabricate):
{MENTORSHIP_SCHEMA_HINT}

Rules:
- "takeaways" must be EXACTLY 3 items, each one short sentence the student would find useful when they re-read it later.
- "action_items" must be EXACTLY 2 items. Each is a concrete next step the student can do this week. Keep `key` short and stable (snake_case).
- Speak to the student in the second person ("You discussed...", "Try to...") in takeaways and action items.
- "sentiment" reflects how the student sounded during the call. "concerned" / "distressed" should be used when there are real signals (mentions of stress, dropping behind, family issues), not just neutral tone.
- "engagement" reflects how willing the student was to talk and reflect — short one-word answers throughout = low.
- "concerns_flagged" should include anything a batch lead should follow up on (mental health, attendance gaps, payment issues, motivation drop). Empty array if nothing.
- "follow_up_for_batch_lead" is one sentence the batch lead should see; empty string if nothing notable.
- Do NOT invent facts that weren't in the transcript.
"""
    return _run_openai_json(system, transcript)


def build_evaluation_record(
    room: str, recorder_data: dict
) -> dict | None:
    """Generate the full evaluation record (with header fields) for a stored call.

    Branches on `metadata.evaluation_mode`:
      - "mentorship" → Miss Ozone takeaways + action items
      - anything else (default) → HR screening scorecard
    """
    meta = recorder_data.get("metadata", {}) or {}
    mode = (meta.get("evaluation_mode") or "screening").strip().lower()

    if mode == "mentorship":
        body = evaluate_mentorship_transcript(
            recorder_data,
            student_name=meta.get("name", ""),
        )
    else:
        body = evaluate_transcript(
            recorder_data,
            role=meta.get("role", ""),
            company=meta.get("company", ""),
            candidate_name=meta.get("name", ""),
        )

    if body is None:
        return None
    return {
        "room": room,
        "evaluation_mode": mode,
        "candidate_name": meta.get("name") or None,
        "phone": meta.get("phone") or None,
        "company": meta.get("company"),
        "role": meta.get("role"),
        "correlation_id": meta.get("correlation_id"),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "call_started_at": recorder_data.get("started_at"),
        "call_ended_at": recorder_data.get("ended_at"),
        **body,
    }
