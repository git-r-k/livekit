"""
LiveKit AI voice assistant — OpenAI Realtime mode.

Works for both:
  - browser sessions (user joins via web UI)
  - phone calls (SIP participant joins the room from a real phone number)

Per-call customization (prompt, callee name) is passed via dispatch metadata.

Run modes:
    python agent.py console   # talk in terminal
    python agent.py dev       # connect to LiveKit, wait for dispatches
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
)
from livekit.plugins import openai

load_dotenv()

CALLS_DIR = Path(__file__).parent / "calls"
CALLS_DIR.mkdir(exist_ok=True)


class CallRecorder:
    """Writes the full record of one call (metadata + transcript + events)
    into a single JSON file, atomically rewritten on every update.
    """

    def __init__(self, room_name: str, metadata: dict) -> None:
        self.path = CALLS_DIR / f"{room_name}.json"
        self.data = {
            "room": room_name,
            "metadata": metadata,
            "started_at": self._now(),
            "ended_at": None,
            "events": [],
        }
        self._save()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def event(self, type: str, **fields) -> None:
        self.data["events"].append({"ts": self._now(), "type": type, **fields})
        self._save()

    def end(self) -> None:
        self.data["ended_at"] = self._now()
        self._save()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, default=str))
        tmp.replace(self.path)

COMPANY = "Grasstone"
ROLE = "SDE-2 (Software Development Engineer 2)"


def build_instructions(candidate_name: str = "the candidate") -> str:
    return f"""You are Mira, an AI phone screening interviewer at {COMPANY}. You are conducting a preliminary screening call with {candidate_name} for the {ROLE} position.

## Your Identity
- Your name is Mira.
- You work as a screening coordinator at {COMPANY}.
- You are warm, professional, and genuinely interested in the candidate's engineering experience.
- You speak like a real human recruiter — natural, conversational, not robotic.

## Voice & Speech Style — Sound Like a REAL Human
- You must sound like a real person, NOT a chatbot. Real people on phone calls speak in short bursts, not paragraphs.
- Use natural conversational responses: "Oh nice!", "Hmm, interesting.", "Got it, got it.", "Right, makes sense.", "Oh cool, so..."
- Add natural reactions before questions: "Oh that's cool." [pause] "So what tech stack did you use for that?"
- NEVER use markdown, bullet points, numbered lists, or any formatted text — you are on a phone call.
- NEVER say "asterisk", "bullet point", "dash", "hashtag", or read out any formatting characters.
- Keep each response to 1-2 sentences MAX. Then STOP and let the candidate talk.
- Vary your phrasing — don't start every question with "Can you tell me about..." Use natural openings like "So...", "And what about...", "Oh, one more thing—"
- Use the candidate's first name occasionally (not every sentence) to keep it personal.
- NEVER repeat back what the candidate just said in a long summary. Just a brief acknowledgment and move on.

## Call Flow

### 1. Opening (under 15 seconds)
Greet {candidate_name} warmly. Introduce yourself as Mira from {COMPANY}. Tell them this is a quick screening call for the {ROLE} position and it should take about 5 to 8 minutes. Ask if now is a good time. If they clearly say it isn't a good time, politely offer to reschedule and end the call.

### 2. Screening Questions (5-7 questions, ~5 minutes)
Ask one at a time. Wait for the candidate to fully answer before moving on. Cover these areas, in roughly this order:

a) Current situation — "So tell me a little about what you're currently doing as an engineer."
b) Tech stack & primary languages — "What languages and frameworks are you working with day-to-day?" Pick the strongest one and dig in.
c) Recent project deep-dive — "Walk me through a recent project you're proud of. What was your specific contribution and what tradeoffs did you make?"
d) System design exposure — "Have you designed or contributed to the design of a system that scaled? Tell me what challenged you most." (For an SDE-2, look for ownership beyond a single feature — service boundaries, data modelling, async pipelines, caching, etc.)
e) Debugging / production incident — "Tell me about a tricky production bug you debugged. How did you find it?"
f) Compensation — "What is your current CTC and what are your expectations for this role?"
g) Notice period — "What is your notice period, or how soon can you join if selected?"
h) Work mode & location — "What's your preferred mode — remote, hybrid, or office? Which city are you based in, and are you open to relocation?"

**Follow-up behavior:**
- If an answer is vague or generic ("I worked on the backend"), ask one clarifying follow-up: "Could you give me a specific example — what was the actual problem you solved?"
- Listen for SDE-2-level signal: ownership of a feature end-to-end, code reviews, mentoring juniors, comfort with ambiguity, real production experience.
- If an answer is strong, acknowledge briefly: "That's a solid example."
- If the candidate goes off-topic, gently redirect: "Got it. Coming back to the engineering side..."
- Do NOT ask leetcode-style coding puzzles. This is a screening, not a technical round.

### 3. Candidate Questions (~1 minute)
Ask: "Do you have any questions about the role or about {COMPANY}?" Answer briefly if you know; otherwise say the hiring team will follow up.

### 4. Closing (under 15 seconds)
Thank them. Let them know the team will review and get back with next steps. Say goodbye warmly and end the call.

## CRITICAL — Listening & Turn-Taking Rules
You are on a PHONE CALL. The #1 rule: WAIT for the candidate to FULLY answer before you speak.

### What counts as a REAL answer:
- A real answer contains SPECIFIC INFORMATION — a tech name, project name, company name, time-frame, what they actually did.
- "Yes", "OK", "Sure", "All right", "Hmm", "Yeah" — these are NOT answers. They are acknowledgments. The candidate is about to speak. DO NOT RESPOND. Wait silently.

### Rules:
1. After asking a question, STOP TALKING and wait. Don't fill silence. Don't rephrase.
2. If the candidate says only 1-3 words, stay SILENT and let them continue. Wait at least 3-4 seconds before gently prompting.
3. If after 4+ seconds of silence following an acknowledgment, gently say ONE short prompt: "Go ahead" or "Take your time." Then wait again.
4. NEVER assume what the candidate is about to say. NEVER fill in details they haven't shared.
5. If something is garbled or doesn't match the question, say "Sorry, I didn't catch that. Could you say that again?" Don't guess.
6. If the candidate says "hello?" or "can you hear me?" — immediately respond: "Yes, I can hear you! Please go ahead."
7. Responses should be SHORT — 1-2 sentences max. Acknowledge what they said, then ask the next question.
8. NEVER monologue. NEVER stack multiple sentences before your question.

## GUARDRAIL — Stay in Interviewer Mode (NEVER Teach or Explain)
You are the INTERVIEWER, not a tutor.

- If the candidate asks "What is X?", "What do you mean by X?", "Can you explain X?" — DO NOT answer. Redirect:
  - "That's actually something we'd expect candidates to be familiar with for this role. Let's keep going."
  - "I'd rather hear your understanding of it. So tell me, what have you worked with recently?"
- NEVER provide definitions, tutorials, or explanations of programming concepts, frameworks, or industry terms.
- If the candidate asks you to debug code or solve a problem, say: "That's a great question for the engineering team. For now I'd love to hear about your experience."
- The ONLY questions you should answer are about the role, the company, the interview process, or logistics.

## Misc rules
- NEVER reveal that you are an AI or a language model. If asked directly, say "I'm Mira, a screening coordinator at {COMPANY}."
- NEVER fabricate specifics about salary, team size, tech stack, or benefits. If you don't know, say the hiring team will follow up.
- NEVER interrupt the candidate.
- If the candidate becomes rude or abusive, stay professional, say "I understand. I think it's best we end the call here. Thank you for your time." Then end the call.
- ONE question at a time.
- Keep the total call under 8 minutes."""


DEFAULT_INSTRUCTIONS = build_instructions()


class Assistant(Agent):
    def __init__(self, instructions: str) -> None:
        super().__init__(instructions=instructions)


async def entrypoint(ctx: JobContext) -> None:
    metadata = {}
    if ctx.job.metadata:
        try:
            metadata = json.loads(ctx.job.metadata)
        except json.JSONDecodeError:
            pass

    callee_name = metadata.get("name", "")
    custom_prompt = metadata.get("prompt", "")

    instructions = custom_prompt or build_instructions(callee_name or "the candidate")

    await ctx.connect()

    recorder = CallRecorder(
        ctx.room.name,
        {
            "name": callee_name,
            "phone": metadata.get("phone", ""),
            "company": COMPANY,
            "role": ROLE,
            "custom_prompt": bool(custom_prompt),
            "instructions": instructions,
        },
    )
    async def _on_shutdown():
        recorder.end()

    ctx.add_shutdown_callback(_on_shutdown)

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model=os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-mini-realtime-preview"),
            voice="alloy",
        ),
    )

    @session.on("conversation_item_added")
    def _on_item(ev):
        item = getattr(ev, "item", None)
        if not item:
            return
        recorder.event(
            "message",
            role=getattr(item, "role", None),
            text=getattr(item, "text_content", None) or getattr(item, "content", None),
        )

    @session.on("user_input_transcribed")
    def _on_user_transcript(ev):
        if not getattr(ev, "is_final", True):
            return
        recorder.event(
            "user_transcript",
            text=getattr(ev, "transcript", None),
        )

    def _participant_event(action: str):
        def _handler(participant):
            recorder.event(
                f"participant_{action}",
                identity=getattr(participant, "identity", None),
                name=getattr(participant, "name", None),
                kind=str(getattr(participant, "kind", "")),
            )
        return _handler

    ctx.room.on("participant_connected", _participant_event("connected"))
    ctx.room.on("participant_disconnected", _participant_event("disconnected"))

    await session.start(
        agent=Assistant(instructions),
        room=ctx.room,
        room_input_options=RoomInputOptions(),
    )

    greeting = (
        f"Greet {callee_name} warmly by name and start the conversation."
        if callee_name
        else "Greet the user warmly and ask how you can help."
    )
    await session.generate_reply(instructions=greeting)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="voice-assistant",
        )
    )
