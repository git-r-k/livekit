# SIP Setup — outbound phone calls

This connects LiveKit to a real phone network so the agent can call any number.
You need a **SIP trunk provider** (Twilio is easiest). One-time setup, ~15 min.

---

## 1. Twilio — get a number and SIP credentials

1. Sign up at https://www.twilio.com (free trial gives ~$15 credit + a free number).
2. **Buy a phone number** (or use the free trial one). Console → Phone Numbers → Buy a Number. Make sure it has **Voice** enabled.
3. **Create a SIP trunk**:
   - Console → Elastic SIP Trunking → Trunks → **Create new trunk**
   - Name it `livekit-trunk`
4. **Termination** (LiveKit → Twilio, for outbound calls):
   - Open the trunk → **Termination** tab
   - Set a **Termination SIP URI** like `livekit-test.pstn.twilio.com` (must be globally unique)
   - Add **Credential List**: create one with a username + strong password — **save these, LiveKit needs them**
5. **Origination** (Twilio → LiveKit, only needed for inbound — skip for now)
6. **Assign your number to the trunk** so outbound caller-ID is set:
   - Trunk → **Numbers** tab → add your purchased number

You should now have:
- Twilio termination URI: `livekit-test.pstn.twilio.com`
- SIP username + password
- A phone number to use as caller-ID (E.164 format, e.g. `+15551234567`)

---

## 2. LiveKit — create an outbound trunk

Use the LiveKit CLI (`lk`) — install: https://docs.livekit.io/home/cli/

```bash
# point CLI at your project
lk project add --api-key $LIVEKIT_API_KEY --api-secret $LIVEKIT_API_SECRET --url $LIVEKIT_URL my-project
lk project set-default my-project
```

Create `outbound-trunk.json`:

```json
{
  "trunk": {
    "name": "Twilio outbound",
    "address": "livekit-test.pstn.twilio.com",
    "numbers": ["+15551234567"],
    "auth_username": "your-twilio-sip-username",
    "auth_password": "your-twilio-sip-password"
  }
}
```

Replace:
- `address` → your Twilio termination URI
- `numbers[0]` → your purchased Twilio number (E.164)
- `auth_username` / `auth_password` → the credential list you made

Create the trunk:

```bash
lk sip outbound create outbound-trunk.json
```

Output gives you a **trunk ID** like `ST_xxxxxxxxxxxx`. Copy it.

---

## 3. Wire it up

Paste the trunk ID into `.env`:

```
LIVEKIT_SIP_TRUNK_ID=ST_xxxxxxxxxxxx
```

---

## 4. Test

```bash
./run.sh
```

Open http://localhost:5001 → scroll to **Call a phone** → enter your own number in E.164 (e.g. `+919876543210`) → Call.

Your phone rings; pick up; the AI greets you and starts talking.

You can also trigger via curl:

```bash
curl -X POST http://localhost:5001/call \
  -H "Content-Type: application/json" \
  -d '{"phone":"+15551234567","name":"Rohan","prompt":"You are a casual friend checking in."}'
```

---

## Trial-account gotcha

Twilio trial accounts can only call **verified** numbers. Add the number you want to test in Twilio Console → Phone Numbers → Verified Caller IDs.

## Inbound (someone calls your Twilio number)

Not covered here — needs an additional **inbound trunk** + **dispatch rule** in LiveKit, plus Twilio Origination config. Ping me if you want it.
