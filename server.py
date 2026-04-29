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
        "phone": "+15551234567",   # E.164
        "name":  "Alice",          # optional
        "prompt": "..."            # optional, overrides default agent instructions
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

    room_name = f"call-{uuid.uuid4().hex[:10]}"
    metadata = json.dumps({"name": name, "prompt": prompt, "phone": phone})

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

    return jsonify({"room": room_name, "phone": phone, "status": "dialing"})


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
