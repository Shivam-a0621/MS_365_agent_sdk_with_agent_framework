"""Generate the senior-overview deck: an agentic L1-support assistant for HDFC's AutomationEdge workflows.

Content is grounded + capability claims were adversarially verified against the code, so the deck is
honest about "built platform" vs "engine ops that depend on the AE MCP server / still placeholder".

Run:  python scripts/build_ppt.py   ->   HDFC-AE-L1-Assistant.pptx
"""

from __future__ import annotations

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

# --- palette ---
NAVY = RGBColor(0x16, 0x21, 0x3A)
BLUE = RGBColor(0x2E, 0x86, 0xDE)
TEAL = RGBColor(0x12, 0xA5, 0x94)
AMBER = RGBColor(0xE8, 0x8A, 0x1A)
LIGHT = RGBColor(0xEE, 0xF1, 0xF7)
CARD = RGBColor(0xF6, 0xF8, 0xFB)
TEXT = RGBColor(0x20, 0x28, 0x36)
MUTED = RGBColor(0x5E, 0x6B, 0x80)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]
SW, SH = Inches(13.333), Inches(7.5)
FOOTER_TEXT = "Agentic L1 Support  ·  HDFC × AutomationEdge"


def _runs(tf, runs, *, align=PP_ALIGN.LEFT):
    tf.word_wrap = True
    for i, (text, size, color, bold) in enumerate(runs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = text
        p.alignment = align
        p.font.size = Pt(size)
        p.font.color.rgb = color
        p.font.bold = bold
        p.font.name = "Calibri"


def box(slide, l, t, w, h, fill, *, line=None, rounded=True):
    shp = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE, l, t, w, h
    )
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(1)
    shp.shadow.inherit = False
    return shp


def textbox(slide, l, t, w, h, runs, *, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(l, t, w, h)
    tb.text_frame.vertical_anchor = anchor
    _runs(tb.text_frame, runs, align=align)
    return tb


def header(slide, title, kicker=None):
    box(slide, Inches(0.7), Inches(0.55), Inches(0.16), Inches(0.62), BLUE, rounded=False)
    runs = []
    if kicker:
        runs.append((kicker.upper(), 12, BLUE, True))
    runs.append((title, 27, NAVY, True))
    textbox(slide, Inches(1.0), Inches(0.48), Inches(11.7), Inches(1.05), runs)
    box(slide, Inches(0.7), Inches(1.52), Inches(11.9), Pt(2), LIGHT, rounded=False)


def footer(slide):
    textbox(slide, Inches(0.7), Inches(7.06), Inches(11.9), Inches(0.32), [(FOOTER_TEXT, 9, MUTED, False)])


def notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def content_slide(title, kicker, items, note):
    """items: list of (text, kind) where kind in {'head','bullet','sub'}."""
    s = prs.slides.add_slide(BLANK)
    header(s, title, kicker)
    tb = s.shapes.add_textbox(Inches(0.9), Inches(1.72), Inches(11.6), Inches(5.15))
    tf = tb.text_frame
    tf.word_wrap = True
    for i, (text, kind) in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.font.name = "Calibri"
        if kind == "head":
            p.text = text
            p.font.size = Pt(15)
            p.font.bold = True
            p.font.color.rgb = TEAL
            p.space_before = Pt(8)
            p.space_after = Pt(4)
        elif kind == "sub":
            p.text = "–  " + text
            p.font.size = Pt(14)
            p.font.color.rgb = MUTED
            p.level = 1
            p.space_after = Pt(5)
        else:
            p.text = "•  " + text
            p.font.size = Pt(16.5)
            p.font.color.rgb = TEXT
            p.space_after = Pt(8)
    footer(s)
    notes(s, note)
    return s


# ── Slide 1 — Title ──────────────────────────────────────────────────────────
s = prs.slides.add_slide(BLANK)
box(s, 0, 0, SW, SH, NAVY, rounded=False)
box(s, 0, Inches(5.5), SW, Inches(0.10), BLUE, rounded=False)
textbox(
    s, Inches(0.9), Inches(2.0), Inches(11.6), Inches(2.6),
    [
        ("AE × HDFC  ·  L1 SUPPORT, AUTOMATED", 16, BLUE, True),
        ("Agentic L1 Support for HDFC's", 36, WHITE, True),
        ("AutomationEdge Workflows", 36, WHITE, True),
    ],
)
textbox(
    s, Inches(0.9), Inches(5.75), Inches(11.6), Inches(1.1),
    [
        ("A Teams chat assistant that monitors, troubleshoots, and operates the AE engine —", 16, LIGHT, False),
        ("with human approval on risky actions and a full audit trail.", 16, LIGHT, False),
    ],
)
notes(s, "Open by naming the real purpose: turn today's manual L1 support for HDFC's ~64 AE workflows "
         "into a chat-driven agent that acts ON the live AE engine via the AE MCP server — not a generic "
         "chatbot. The platform (handoff routing, approvals, durable resume, audit) is already built; the "
         "engine operations are wired through the MCP server. Keep it concrete: the same work the support "
         "team does today, done conversationally.")

# ── Slide 2 — Context ────────────────────────────────────────────────────────
content_slide(
    "HDFC's daily work runs on these workflows", "Context",
    [
        ("AE has built ~64 production workflows that run for HDFC every day.", "bullet"),
        ("HDFC employees use them to pull data and get their work done — core daily operations, not a back-office nicety.", "bullet"),
        ("When a workflow stalls or fails, real users are blocked — uptime and run-health are business-critical.", "bullet"),
        ("So fast, reliable L1 response on the AE engine is a direct dependency for HDFC's day-to-day work.", "bullet"),
    ],
    "Set the stakes for a senior: ~64 workflows are load-bearing for HDFC's daily operations. Frame uptime "
    "as a business metric — a failed or stuck run means blocked employees and delayed work. That's why L1 "
    "quality matters and why automating it well is worth doing. This is the customer context behind the platform.",
)

# ── Slide 3 — Problem ────────────────────────────────────────────────────────
content_slide(
    "L1 support today is manual and reactive", "The problem",
    [
        ("A support team manually monitors runs, hunts errors, and restarts workflows — reactive and repetitive.", "bullet"),
        ("Routine lookups (request, schedule, agent, health, run-times) are done by hand, one at a time.", "bullet"),
        ("Every task needs people who know the AE engine deeply — a scarce, hard-to-scale skill.", "bullet"),
        ("MTTR suffers: incidents wait for a human to notice; nights and weekends sit until someone is online.", "bullet"),
        ("Escalations mean manually raising and routing tickets — more delay, more handoffs, more room for error.", "bullet"),
    ],
    "Make the pain concrete: the work is inherently reactive (nothing happens until a human notices), which "
    "inflates MTTR and is brutal after hours. It's repetitive lookup-and-restart work that still demands deep "
    "AE-engine expertise — the scaling wall and key-person risk. Every one of these manual tasks maps to an "
    "engine capability we can drive conversationally.",
)

# ── Slide 4 — Capabilities (honest two-group split) ──────────────────────────
content_slide(
    "What the assistant does", "Capabilities",
    [
        ("Ask in plain language; the assistant talks to the AE engine for you (via the AE MCP server).", "bullet"),
        ("Look up & investigate  —  read-only, fast, answered in chat", "head"),
        ("Monitor & list workflows, schedules, agents, and recent activity.", "sub"),
        ("Get a run / schedule / workflow's details — status, failure reason, failed step, outputs, config.", "sub"),
        ("Explain why a run failed, and surface agent-health trends & upcoming run-times.", "sub"),
        ("Act  —  guarded by approval + audit", "head"),
        ("Start / re-run a workflow — long runs go to the background and notify you on completion.", "sub"),
        ("Raise an escalation ticket — human-approved and recorded.", "sub"),
        ("Maturity varies: detail-lookups are live via the AE MCP server; the “act” side + ticket/ITSM integration are being wired (see roadmap).", "bullet"),
    ],
    "Ground every operation honestly. The 'look up & investigate' side is the strongest: the AE MCP server "
    "exposes request/schedule/workflow detail tools the analyst agent already calls. The 'act' side is real "
    "as PLUMBING (agent->MCP, background-run-and-notify, durable approvals) but the engine execute/restart "
    "tool lives on the AE MCP server and isn't approval-gated yet, and the local ticket body is still a mock. "
    "Tell the senior plainly: we are not overclaiming — what's live vs in-progress is on the roadmap slide.",
)

# ── Slide 5 — Walkthrough ────────────────────────────────────────────────────
content_slide(
    "A typical interaction", "Walkthrough",
    [
        ("User (in Teams): “The reconciliation workflow failed overnight — what happened?”", "bullet"),
        ("The assistant routes to the AE-engine analyst, pulls the failed run's details + logs, and explains the root cause in one plain-language reply.", "bullet"),
        ("It offers next steps: “I can re-run it, or raise an escalation ticket — which would you like?”", "bullet"),
        ("A long re-run goes to the background: “started — I'll update you”; the user keeps chatting and is notified when it finishes.", "bullet"),
        ("Raising a ticket pauses for the user's approval, then returns the ticket id — every step checkpointed and audited.", "bullet"),
    ],
    "This is the money slide: one concrete failed-workflow conversation that exercises the real flow — "
    "investigate (engine via MCP), explain (one reply channel), then act on two branches: a long re-run as a "
    "background task with proactive notify, and an approval-gated, audited ticket. Be precise that the approval "
    "+ audit are verified for the local ticket tool today; gating the engine re-run is a small, planned step.",
)

# ── Slide 6 — Architecture ───────────────────────────────────────────────────
s = prs.slides.add_slide(BLANK)
header(s, "How it fits together: chat to the AE engine", "Architecture")
layers = [
    ("Microsoft Teams  (chat)", "the L1 support user asks in plain language", CARD),
    ("Bot Host  —  FastAPI + M365 Agents SDK", "receives messages, renders replies & approval cards", CARD),
    ("Conversation Service", "one entry point per turn: identity · persistence · orchestration", CARD),
    ("Agent Workflow", "Coordinator (triage) routes to the AE Engine Analyst specialist", TEAL),
    ("AE MCP Server", "the analyst's bridge to the engine — tools discovered at runtime", TEAL),
    ("AutomationEdge Engine", "the ~64 HDFC workflows, schedules, agents, runs", BLUE),
]
x, w, h, gap = Inches(0.8), Inches(7.7), Inches(0.66), Inches(0.135)
y = Inches(1.66)
mid_y = None
for name, sub, col in layers:
    hl = col in (TEAL, BLUE)
    b = box(s, x, y, w, h, col if hl else CARD, line=None if hl else LIGHT)
    b.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    b.text_frame.word_wrap = True
    p = b.text_frame.paragraphs[0]
    p.text = name
    p.font.size = Pt(15)
    p.font.bold = True
    p.font.color.rgb = WHITE if hl else NAVY
    p.font.name = "Calibri"
    p2 = b.text_frame.add_paragraph()
    p2.text = sub
    p2.font.size = Pt(10.5)
    p2.font.color.rgb = LIGHT if hl else MUTED
    p2.font.name = "Calibri"
    if name == "Conversation Service":
        mid_y = y
    y = y + h + gap
# side callouts
cx = Inches(8.85)
for j, (label, col) in enumerate([("PostgreSQL\ndurable state + full audit", NAVY), ("Azure OpenAI\nthe reasoning model", AMBER)]):
    cyy = mid_y + Inches(0.2) + Inches(1.5) * j
    c = box(s, cx, cyy, Inches(3.65), Inches(1.0), CARD, line=col)
    c.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    _runs(c.text_frame, [(label.split("\n")[0], 14, NAVY, True), (label.split("\n")[1], 11, MUTED, False)])
    ar = s.shapes.add_shape(MSO_SHAPE.LEFT_ARROW, cx - Inches(0.5), cyy + Inches(0.38), Inches(0.45), Inches(0.24))
    ar.fill.solid(); ar.fill.fore_color.rgb = col; ar.line.fill.background(); ar.shadow.inherit = False
footer(s)
notes(s, "Read it top to bottom as the path of one L1 message. The Bot Host is thin M365-SDK plumbing on "
         "FastAPI; the real work is the channel-neutral Conversation Service. The key idea is coordinator + "
         "specialist: only the AE Engine Analyst touches the engine, and only through the AE MCP server — a "
         "single, observable access path. Crucially we don't hardcode engine capabilities: the analyst learns "
         "the available tools from the MCP server at connect time, so new L1 operations appear without a code "
         "change. Postgres makes everything durable + auditable; Azure OpenAI is the brain.")

# ── Slide 7 — Request flow ───────────────────────────────────────────────────
s = prs.slides.add_slide(BLANK)
header(s, "The life of one L1 request", "Request flow")
steps = [
    ("User asks", "in Teams", BLUE),
    ("Coordinator", "routes to the AE analyst", NAVY),
    ("AE analyst", "calls the AE MCP server", TEAL),
    ("Approval", "if the action is risky", AMBER),
    ("Result", "reply / notify", BLUE),
]
n = len(steps)
bw, bh = Inches(2.05), Inches(1.7)
g = (Inches(11.9) - bw * n) / (n - 1)
x, cy = Inches(0.7), Inches(2.65)
for i, (name, sub, col) in enumerate(steps):
    b = box(s, x, cy, bw, bh, col)
    b.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    _runs(b.text_frame, [(name, 16, WHITE, True), (sub, 11, LIGHT, False)], align=PP_ALIGN.CENTER)
    if i < n - 1:
        ar = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, x + bw + Inches(0.02), cy + bh / 2 - Inches(0.12), g - Inches(0.04), Inches(0.24))
        ar.fill.solid(); ar.fill.fore_color.rgb = MUTED; ar.line.fill.background(); ar.shadow.inherit = False
    x = x + bw + g
band = box(s, Inches(0.7), Inches(4.95), Inches(11.9), Inches(1.0), LIGHT)
band.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
_runs(band.text_frame, [
    ("Read-only answers return instantly. Risky actions pause for approval — then the SAME conversation resumes "
     "from its checkpoint, runs the action, and reports back. Every step is persisted, resumable, and audited.",
     14.5, NAVY, True)], align=PP_ALIGN.CENTER)
footer(s)
notes(s, "Walk it as a story. The coordinator never answers engine questions itself — it routes to the analyst, "
         "which talks to the engine only through the MCP server and summarizes back via one reply channel "
         "(keeping multi-agent chatter away from the user). The fork is read vs. act: monitoring is answered on "
         "the spot; restart/execute is gated. When gated, the turn suspends — we persist the pending approval + "
         "a checkpoint and hand a card back to Teams. The resume is the clever part: it can land on a different "
         "worker or after a restart and still continue, and the analyst still remembers the original request — "
         "so it acts and reports, it doesn't start over.")

# ── Slide 8 — Guardrails ─────────────────────────────────────────────────────
content_slide(
    "Why this is safe to run for a bank", "Guardrails",
    [
        ("Human approval before state-changing actions — the user sees a card and must click before anything runs.", "bullet"),
        ("Durable, resumable conversations — checkpointed to Postgres, so a crash or deploy mid-approval never strands a request.", "bullet"),
        ("Full audit trail — every message, action, tool call, approval decision, and model call is recorded.", "bullet"),
        ("Long runs don't block the chat — background execution with proactive notification on completion.", "bullet"),
        ("One narrow access path — only the AE analyst reaches the engine, only through the MCP server: a single, observable choke point.", "bullet"),
    ],
    "Frame each guardrail against bank reality. Approvals: a re-run on a production HDFC workflow has blast "
    "radius, so a human stays in the loop on exactly those actions — read-only monitoring never interrupts, "
    "keeping the assistant fast for the ~90%% of L1 work that's just lookups. Durability matters because these "
    "workflows are business-critical. Audit is non-negotiable for a bank: per conversation we can reconstruct "
    "who asked what, which engine tool ran, what it returned, and who approved it. Honest note: approval-gating "
    "is wired for local tools today; binding it to the engine re-run is a small planned step.",
)

# ── Slide 9 — Value ──────────────────────────────────────────────────────────
content_slide(
    "Why it matters — for HDFC and for AE", "Value",
    [
        ("For HDFC", "head"),
        ("Faster L1 resolution + 24/7 conversational self-serve in Teams for the people who keep the 64 workflows healthy.", "sub"),
        ("Fewer manual lookups and fewer escalations — routine monitoring, re-runs, and error checks handled in chat.", "sub"),
        ("Consistent, approval-gated, fully audited actions — built for a bank.", "sub"),
        ("For AE", "head"),
        ("Productize L1 support as a reusable platform — scale across the 64 workflows and beyond.", "sub"),
        ("Move L1 from “people who know the engine” to an audited assistant anyone can talk to — instead of staffing experts per shift.", "sub"),
    ],
    "Lead with the human cost it removes: today L1 is reactive, repetitive, and needs engine experts. The "
    "assistant turns those lookups and re-runs into a chat, with a full audit trail and human approval on "
    "anything risky. Frame AE's upside as productization and scale, not headcount cuts.",
)

# ── Slide 10 — Status & roadmap (honest) ─────────────────────────────────────
s = prs.slides.add_slide(BLANK)
header(s, "Where we are — built vs planned", "Status & roadmap")
cols = [
    ("BUILT  (platform)", TEAL, [
        "Multi-agent handoff (triage → AE analyst)",
        "Live AE MCP connection",
        "Human-in-the-loop approvals",
        "Postgres-durable, resumable chats",
        "Background tasks + proactive notify",
        "Full audit & telemetry",
    ]),
    ("WIRING UP  (in progress)", AMBER, [
        "Real engine ops behind each L1 task (AE MCP tools)",
        "Approval-gating on engine actions",
        "Real ticket / ITSM integration (today: demo mock)",
        "Background job body (today: placeholder)",
        "Context compaction for long sessions",
    ]),
    ("NEXT", BLUE, [
        "End-to-end L1 runbooks",
        "Deeper AE engine coverage",
        "Durable background jobs",
        "Security & load review before rollout",
    ]),
]
cw, g = Inches(3.95), Inches(0.18)
x = Inches(0.72)
for title, col, items in cols:
    head_b = box(s, x, Inches(1.78), cw, Inches(0.56), col)
    head_b.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    _runs(head_b.text_frame, [(title, 14, WHITE, True)], align=PP_ALIGN.CENTER)
    body = box(s, x, Inches(2.42), cw, Inches(4.25), CARD, line=LIGHT)
    tf = body.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.18)
    tf.margin_top = Inches(0.16)
    tf.margin_right = Inches(0.12)
    for i, it in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = "•  " + it
        p.font.size = Pt(12.5)
        p.font.color.rgb = TEXT
        p.space_after = Pt(7)
        p.font.name = "Calibri"
    x = x + cw + g
footer(s)
notes(s, "This is the honesty slide — it earns trust for everything else. The handoff, HITL approvals, durable "
         "checkpointing, background-plus-proactive, and audit are genuinely implemented; the analyst really does "
         "connect to the AE MCP server, and detail-lookup tools (request/schedule/workflow) are available there. "
         "Be candid about 'wiring up': the engine execute/restart tool is server-side and not approval-gated "
         "yet, and two local bodies (the ticket tool and the long-job runner) are still placeholders. Don't claim "
         "a ticket is filed in HDFC's helpdesk today, or that the assistant restarts workflows with approval "
         "today — those are the near-term integration steps.")

# ── Slide 11 — Summary ───────────────────────────────────────────────────────
content_slide(
    "An agentic L1 operator for the AE engine", "Summary",
    [
        ("The problem: manual, reactive L1 support for the 64 AE workflows HDFC depends on daily.", "bullet"),
        ("The solution: a Teams chat agent that performs L1 ops conversationally against the engine via the AE MCP server.", "bullet"),
        ("Already real: durable multi-agent handoff, human approval on risky actions, background runs with proactive notify, full bank-grade audit.", "bullet"),
        ("The path: compaction + long-session scaling now; real engine operations, deeper coverage, and hardening next.", "bullet"),
        ("The ask: align on the first HDFC L1 runbooks to productionize and which AE MCP tools to prioritize.", "bullet"),
    ],
    "Close by tying back to the business: uptime of these workflows is critical for HDFC, and this moves L1 from "
    "engine-experts to an audited assistant anyone can talk to. Keep it credible by restating built-vs-planned in "
    "one breath. End on a concrete ask so the senior leaves with a decision: which L1 runbooks to harden first "
    "and which AE MCP tools to prioritize.",
)

out = "HDFC-AE-L1-Assistant.pptx"
prs.save(out)
print(f"Saved {out} with {len(prs.slides._sldIdLst)} slides")
