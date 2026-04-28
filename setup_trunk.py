"""
Create a LiveKit SIP outbound trunk from Twilio creds in .env.

Reads:
    TWILIO_TERMINATION_URI, TWILIO_SIP_USERNAME, TWILIO_SIP_PASSWORD,
    TWILIO_PHONE_NUMBER (any format; spaces/dashes stripped)

Writes back:
    LIVEKIT_SIP_TRUNK_ID  (the new ST_xxxx trunk id)

Run once:
    .venv/bin/python setup_trunk.py
"""

import asyncio
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit import api

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)


def normalize_e164(number: str) -> str:
    digits = re.sub(r"[^\d+]", "", number or "")
    if not digits.startswith("+"):
        raise SystemExit(f"phone number must be E.164 (start with +): got '{number}'")
    return digits


def update_env(key: str, value: str) -> None:
    text = ENV_PATH.read_text()
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(f"{key}={value}", text)
    else:
        text = text.rstrip() + f"\n{key}={value}\n"
    ENV_PATH.write_text(text)


async def main() -> None:
    required = [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "TWILIO_TERMINATION_URI",
        "TWILIO_SIP_USERNAME",
        "TWILIO_SIP_PASSWORD",
        "TWILIO_PHONE_NUMBER",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise SystemExit(f"missing in .env: {', '.join(missing)}")

    phone = normalize_e164(os.environ["TWILIO_PHONE_NUMBER"])
    address = os.environ["TWILIO_TERMINATION_URI"].strip()

    lk = api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"],
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    )
    try:
        res = await lk.sip.create_sip_outbound_trunk(
            api.CreateSIPOutboundTrunkRequest(
                trunk=api.SIPOutboundTrunkInfo(
                    name="Twilio outbound",
                    address=address,
                    numbers=[phone],
                    auth_username=os.environ["TWILIO_SIP_USERNAME"],
                    auth_password=os.environ["TWILIO_SIP_PASSWORD"],
                )
            )
        )
    finally:
        await lk.aclose()

    trunk_id = res.sip_trunk_id
    print(f"\n✅ trunk created: {trunk_id}")
    update_env("LIVEKIT_SIP_TRUNK_ID", trunk_id)
    print(f"✅ wrote LIVEKIT_SIP_TRUNK_ID to {ENV_PATH}")
    print(f"\nReady to make calls. Run: ./run.sh\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
