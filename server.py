"""
Flask server.

Endpoints:
  GET  /                       -> browser UI (static/index.html)
  GET  /token                  -> JWT for the browser to join a LiveKit room
  POST /call                   -> outbound phone call: dispatches agent + dials via SIP
  GET  /health
  GET  /calls                  -> list all calls (summary)
  GET  /calls/<room>           -> full conversation + evaluation JSON
  GET  /calls/<room>/conversation  -> raw conversation JSON
  GET  /calls/<room>/evaluation    -> raw evaluation JSON
"""

import asyncio
import json
import os
import re
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request
from flask_cors import CORS
from livekit import api

from evaluation import build_evaluation_record

load_dotenv()

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]
LIVEKIT_SIP_TRUNK_ID = os.environ.get("LIVEKIT_SIP_TRUNK_ID")
AGENT_NAME = "voice-assistant"

TWILIO_ENV_KEYS = (
    "TWILIO_TERMINATION_URI",
    "TWILIO_SIP_USERNAME",
    "TWILIO_SIP_PASSWORD",
    "TWILIO_PHONE_NUMBER",
)
AUTO_TRUNK_NAME = "auto-twilio-trunk"


def _normalize_e164(number: str) -> str:
    digits = re.sub(r"[^\d+]", "", number or "")
    if not digits.startswith("+"):
        digits = "+" + digits.lstrip("+")
    return digits

CALLS_DIR = Path(os.getenv("CALLS_DIR") or (Path(__file__).parent / "calls"))
CALLS_DIR.mkdir(parents=True, exist_ok=True)
ROOM_RE = re.compile(r"^call-[a-zA-Z0-9_-]+$")


def _safe_room(room: str) -> str:
    if not ROOM_RE.match(room):
        abort(400, description="invalid room name")
    return room


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _ensure_evaluation(room: str) -> tuple[dict | None, str | None]:
    """Return (evaluation_dict, error). Lazily generates and caches if missing."""
    eval_path = CALLS_DIR / f"{room}_evaluation.json"
    cached = _load_json(eval_path)
    if cached is not None:
        return cached, None

    conv = _load_json(CALLS_DIR / f"{room}.json")
    if conv is None:
        return None, "conversation not found"
    if not conv.get("ended_at"):
        return None, "call still in progress"

    try:
        evaluation = build_evaluation_record(room, conv)
    except Exception as e:
        return None, f"evaluation failed: {e}"

    if evaluation is None:
        return None, "no transcript to evaluate"

    eval_path.write_text(json.dumps(evaluation, indent=2, default=str))
    return evaluation, None


def run_async(coro):
    """Run an async LiveKit SDK coroutine from a sync Flask handler."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _sync_trunk_from_env() -> str | None:
    """If TWILIO_* env vars are set, ensure a LiveKit outbound trunk that uses
    those exact credentials, and return its id. Existing auto-managed trunks
    are deleted and replaced so credential rotation is just an env+restart."""
    if not all(os.environ.get(k) for k in TWILIO_ENV_KEYS):
        return None

    address = os.environ["TWILIO_TERMINATION_URI"].strip()
    phone = _normalize_e164(os.environ["TWILIO_PHONE_NUMBER"])
    username = os.environ["TWILIO_SIP_USERNAME"]
    password = os.environ["TWILIO_SIP_PASSWORD"]

    lk = api.LiveKitAPI(url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
    try:
        existing = await lk.sip.list_sip_outbound_trunk(api.ListSIPOutboundTrunkRequest())
        for t in getattr(existing, "items", []):
            if t.name == AUTO_TRUNK_NAME:
                await lk.sip.delete_sip_trunk(
                    api.DeleteSIPTrunkRequest(sip_trunk_id=t.sip_trunk_id)
                )

        res = await lk.sip.create_sip_outbound_trunk(
            api.CreateSIPOutboundTrunkRequest(
                trunk=api.SIPOutboundTrunkInfo(
                    name=AUTO_TRUNK_NAME,
                    address=address,
                    numbers=[phone],
                    auth_username=username,
                    auth_password=password,
                )
            )
        )
        return res.sip_trunk_id
    finally:
        await lk.aclose()


try:
    _auto_trunk_id = run_async(_sync_trunk_from_env())
    if _auto_trunk_id:
        LIVEKIT_SIP_TRUNK_ID = _auto_trunk_id
        print(f"[startup] using auto-managed Twilio trunk {_auto_trunk_id}", flush=True)
    elif LIVEKIT_SIP_TRUNK_ID:
        print(f"[startup] using static LIVEKIT_SIP_TRUNK_ID={LIVEKIT_SIP_TRUNK_ID}", flush=True)
    else:
        print("[startup] no SIP trunk configured — outbound /call will return 400", flush=True)
except Exception as e:
    print(
        f"[startup] auto-trunk sync failed: {e}; falling back to LIVEKIT_SIP_TRUNK_ID={LIVEKIT_SIP_TRUNK_ID}",
        flush=True,
    )


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/token")
def token():
    identity = request.args.get("identity") or f"user-{uuid.uuid4().hex[:8]}"
    room = request.args.get("room") or "voice-assistant"

    grant = api.VideoGrants(
        room_join=True,
        room=room,
        can_publish=True,
        can_subscribe=True,
    )

    jwt = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(grant)
        .to_jwt()
    )

    return jsonify({"token": jwt, "url": LIVEKIT_URL, "room": room, "identity": identity})


@app.post("/call")
def call():
    """
    Outbound phone call.

    Body:
      {
        "phone":            "+15551234567",   # E.164, required
        "name":             "Alice",          # optional
        "prompt":           "...",            # optional, fully overrides MIRA's instructions
        "jd":               "...",            # optional, job description woven into MIRA's prompt
        "questions":        ["...", "..."],   # optional, must-ask questions (string or list)
        "evaluation_mode":  "mentorship",     # optional: "screening" (default) | "mentorship"
        "webhook_url":      "https://...",    # optional: POSTed when the call ends
        "webhook_secret":   "...",            # optional: sent as x-webhook-secret header
        "correlation_id":   "miss-ozone-uuid" # optional: opaque, echoed back in webhook
      }
    """
    if not LIVEKIT_SIP_TRUNK_ID:
        return jsonify({"error": "LIVEKIT_SIP_TRUNK_ID not configured. See SIP_SETUP.md"}), 400

    data = request.get_json(force=True) or {}
    phone = data.get("phone")
    if not phone:
        return jsonify({"error": "phone is required (E.164, e.g. +15551234567)"}), 400

    name = data.get("name", "")
    prompt = data.get("prompt", "")
    jd = data.get("jd", "") or ""
    questions = data.get("questions") or []
    if isinstance(questions, str):
        questions = [q.strip() for q in questions.splitlines() if q.strip()]
    elif isinstance(questions, list):
        questions = [str(q).strip() for q in questions if str(q).strip()]
    else:
        questions = []

    evaluation_mode = (data.get("evaluation_mode") or "screening").strip().lower()
    if evaluation_mode not in ("screening", "mentorship"):
        return jsonify({"error": "evaluation_mode must be 'screening' or 'mentorship'"}), 400
    webhook_url = (data.get("webhook_url") or "").strip() or None
    webhook_secret = data.get("webhook_secret") or None
    correlation_id = data.get("correlation_id") or None

    room_name = f"call-{uuid.uuid4().hex[:10]}"
    metadata = json.dumps({
        "name": name,
        "prompt": prompt,
        "phone": phone,
        "jd": jd,
        "questions": questions,
        "evaluation_mode": evaluation_mode,
        "webhook_url": webhook_url,
        "webhook_secret": webhook_secret,
        "correlation_id": correlation_id,
    })

    async def trigger():
        lk = api.LiveKitAPI(url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
        try:
            await lk.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=AGENT_NAME,
                    room=room_name,
                    metadata=metadata,
                )
            )
            await lk.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    sip_trunk_id=LIVEKIT_SIP_TRUNK_ID,
                    sip_call_to=phone,
                    room_name=room_name,
                    participant_identity=f"phone-{phone}",
                    participant_name=name or "Caller",
                    wait_until_answered=False,
                )
            )
        finally:
            await lk.aclose()

    try:
        run_async(trigger())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "room": room_name,
        "phone": phone,
        "status": "dialing",
        "evaluation_mode": evaluation_mode,
        "correlation_id": correlation_id,
    })


@app.get("/health")
def health():
    return {"ok": True, "sip_configured": bool(LIVEKIT_SIP_TRUNK_ID)}


@app.get("/calls")
def list_calls():
    """List every call with a short summary. Newest first."""
    if not CALLS_DIR.exists():
        return jsonify({"calls": []})

    rooms = {}
    for path in CALLS_DIR.glob("call-*.json"):
        name = path.stem
        if name.endswith("_evaluation"):
            room = name[: -len("_evaluation")]
            rooms.setdefault(room, {})["has_evaluation"] = True
        else:
            rooms.setdefault(name, {})["has_conversation"] = True

    items = []
    for room, flags in rooms.items():
        conv = _load_json(CALLS_DIR / f"{room}.json") or {}
        evaln = _load_json(CALLS_DIR / f"{room}_evaluation.json") or {}
        meta = conv.get("metadata", {})
        items.append({
            "room": room,
            "candidate_name": meta.get("name") or evaln.get("candidate_name"),
            "phone": meta.get("phone") or evaln.get("phone"),
            "company": meta.get("company") or evaln.get("company"),
            "role": meta.get("role") or evaln.get("role"),
            "started_at": conv.get("started_at"),
            "ended_at": conv.get("ended_at"),
            "has_conversation": flags.get("has_conversation", False),
            "has_evaluation": flags.get("has_evaluation", False),
            "recommendation": evaln.get("recommendation"),
            "overall_score": (evaln.get("scores") or {}).get("overall_fit_for_role", {}).get("score"),
        })

    items.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    return jsonify({"calls": items, "count": len(items)})


@app.get("/calls/<room>")
def get_call(room):
    """Return conversation + evaluation together. Generates the evaluation lazily."""
    room = _safe_room(room)
    conversation = _load_json(CALLS_DIR / f"{room}.json")
    if conversation is None:
        abort(404, description="call not found")
    evaluation, _ = _ensure_evaluation(room)
    return jsonify({"room": room, "conversation": conversation, "evaluation": evaluation})


@app.get("/calls/<room>/conversation")
def get_call_conversation(room):
    room = _safe_room(room)
    data = _load_json(CALLS_DIR / f"{room}.json")
    if data is None:
        abort(404, description="conversation not found")
    return jsonify(data)


@app.get("/calls/<room>/evaluation")
def get_call_evaluation(room):
    """Return the HR scorecard. Generates and caches it on first request."""
    room = _safe_room(room)
    evaluation, error = _ensure_evaluation(room)
    if evaluation is None:
        if error == "conversation not found":
            abort(404, description=error)
        if error == "call still in progress":
            return jsonify({"error": error}), 409
        return jsonify({"error": error or "evaluation unavailable"}), 500
    return jsonify(evaluation)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=True)
