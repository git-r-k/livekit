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


def evaluate_transcript(
    recorder_data: dict, role: str, company: str, candidate_name: str
) -> dict | None:
    """Synchronous evaluation. Returns the parsed JSON, or None if no transcript."""
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


def build_evaluation_record(
    room: str, recorder_data: dict
) -> dict | None:
    """Generate the full evaluation record (with header fields) for a stored call."""
    meta = recorder_data.get("metadata", {}) or {}
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
        "candidate_name": meta.get("name") or None,
        "phone": meta.get("phone") or None,
        "company": meta.get("company"),
        "role": meta.get("role"),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "call_started_at": recorder_data.get("started_at"),
        "call_ended_at": recorder_data.get("ended_at"),
        **body,
    }
