"""
Flask server.

Endpoints:
  GET  /              -> browser UI (static/index.html)
  GET  /token         -> JWT for the browser to join a LiveKit room
  POST /call          -> outbound phone call: dispatches agent + dials via SIP
  GET  /health
"""

import asyncio
import json
import os
import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from livekit import api

load_dotenv()

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]
LIVEKIT_SIP_TRUNK_ID = os.environ.get("LIVEKIT_SIP_TRUNK_ID")
AGENT_NAME = "voice-assistant"


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=True)
