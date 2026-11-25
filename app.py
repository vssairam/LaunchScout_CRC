"""Streamlit UI for LaunchScout.

What this page does, top to bottom:
  1. Shows an input form (idea, city, target customer, optional known competitors and the extras).
  2. On submit, runs the CrewAI crew in a background thread and paints a live "which agent is working" row
     plus a step log while it runs.
  3. Shows the result in tabs: Report (with Markdown/PDF download), Changes since last run, Quality score and,
     if a deck was requested, the Pitch deck (with PowerPoint download).
  4. On a fresh page load it shows the last saved run from the output/ folder, so the downloads work without
     starting a new (paid) run.

Streamlit re-executes this whole script on every interaction, so anything that must survive a rerun
(the report, score, deck...) is kept in st.session_state.
"""
import io
import os
import queue
import threading
from pathlib import Path

import markdown as md
import streamlit as st
from dotenv import load_dotenv
from xhtml2pdf import pisa

# Load SERPER_API_KEY / OPENAI_API_KEY / LLM_MODEL from .env BEFORE importing crew, which reads them.
load_dotenv()
from crew import run_best_of, run_crew  # noqa: E402
import scoring  # noqa: E402

# Left-to-right order of the agents in the status row; matches the order of the tasks in crew.build_crew.
AGENT_ORDER = ["Market Researcher", "Review Analyst", "Strategy Analyst", "Report Writer"]

st.set_page_config(page_title="LaunchScout", page_icon="🔭", layout="wide")
st.title("🔭 LaunchScout: Competitor Research Crew")
st.caption("Four AI agents research your market and deliver a sourced competitor report.")


def to_pdf(markdown_text: str) -> bytes:
    """Convert the Markdown report to PDF bytes (Markdown -> HTML with tables -> PDF via xhtml2pdf)."""

    # The built-in PDF font has no rupee glyph
    html = md.markdown(markdown_text.replace("₹", "Rs "), extensions=["tables"])

    # Minimal styling so tables get borders and the text is readable.
    html = (
        "<html><head><style>body{font-family:Helvetica;font-size:10pt}"
        "table{border-collapse:collapse}td,th{border:1px solid #999;padding:3px}"
        "</style></head><body>" + html + "</body></html>"
    )
    buf = io.BytesIO()
    pisa.CreatePDF(html, dest=buf)
    return buf.getvalue()


# Warn early if the search key is missing (the crew would otherwise refuse to start).
missing = [k for k in ("SERPER_API_KEY",) if not os.getenv(k)]
if missing:
    st.warning(f"Missing in .env: {', '.join(missing)}. See .env.example.")

# st.form: widget values are only sent to the script when the submit button is pressed,
# so typing in a field does not rerun the page.
with st.form("idea"):
    # The inputs are pre-filled with the sample idea so the app can be tried with one click.
    idea = st.text_input("Business idea", "Healthy millet meal boxes delivered for lunch")
    city = st.text_input("City", "Coimbatore")
    customer = st.text_input("Target customer", "Office workers, age 25 to 40")
    known = st.text_input("Known competitors (optional)", "")
    want_deck = st.checkbox("Also create a 6-slide pitch deck (adds a Pitch Deck agent)")

    # 'Runs' above 1 means best-of-N: the crew runs several times and only the highest-scoring report is kept.
    runs = st.number_input("Runs (keep the best-scoring one; each extra run costs the same again)", min_value=1, max_value=3, value=1)
    hierarchical = st.checkbox("Hierarchical mode: a Manager agent delegates the work (experimental, slower)")

    # `go` is True only in the script run that was triggered by pressing the button.
    go = st.form_submit_button("Run research crew", type="primary")

if go:
    # Validate the required fields before spending any search or model credits.
    if not (idea and city and customer):
        st.error("Idea, city and target customer are required.")
        st.stop()

    # The crew runs in a worker thread and reports progress through this queue; the main thread (below)
    # drains the queue and repaints the page. `state` carries the final result or error back.
    events: queue.Queue = queue.Queue()
    state = {"done": False, "error": None, "report": None}

    def on_step(step):
        """CrewAI step callback: turn one agent step (a thought or a tool call) into a short log line."""
        text = getattr(step, "thought", None) or getattr(step, "text", None) or str(step)
        tool = getattr(step, "tool", None)
        line = f"🔧 {tool}: {str(getattr(step, 'tool_input', ''))[:150]}" if tool else f"💭 {str(text)[:300]}"
        events.put(("step", line))

    def on_task(out):
        """CrewAI task callback: fires when an agent finishes its task; the main loop counts these to tick badges."""
        events.put(("task", f"✅ {getattr(out, 'agent', 'Agent')} finished"))

    def run():
        """Worker thread body: run the crew (once, or best-of-N) and store the report or the exception."""
        try:
            if runs > 1:
                state["report"] = run_best_of(
                    int(runs), idea, city, customer, known, on_step, on_task, include_deck=want_deck, hierarchical=hierarchical
                )
            else:
                state["report"] = run_crew(
                    idea, city, customer, known, on_step, on_task, include_deck=want_deck, hierarchical=hierarchical
                )
        except Exception as e:  # surfaced in UI
            state["error"] = e
        finally:
            state["done"] = True   # always set, so the polling loop below can stop

    # Start the crew in a background thread so this page keeps updating while the agents work.
    threading.Thread(target=run, daemon=True).start()

    # One badge per agent (plus the deck designer when requested); st.empty() placeholders are updated in place.
    agent_order = AGENT_ORDER + (["Pitch Deck Designer"] if want_deck else [])
    cols = st.columns(len(agent_order))
    badges = [c.empty() for c in cols]
    log_box = st.empty()
    log: list[str] = []
    finished = 0   # number of agents that have finished; the agent at this index is the one working now

    def paint():
        """Redraw the badges (done / working / waiting) and the last 25 log lines."""
        for i, name in enumerate(agent_order):
            icon = "✅" if i < finished else ("⏳" if i == finished and not state["done"] else "⚪")
            badges[i].markdown(f"### {icon} {name}")
        log_box.code("\n".join(log[-25:]) or "Starting…", language=None)

    import time

    paint()

    # Poll until the worker is done AND every queued event has been shown.
    while not (state["done"] and events.empty()):
        try:
            kind, line = events.get(timeout=0.5)
            log.append(line)

            # A finished task means the next agent starts, so move the 'working' badge one place to the right.
            if kind == "task":
                finished += 1
        except queue.Empty:
            pass
        paint()
    paint()

    # If the crew raised an exception, show it instead of an empty result.
    if state["error"]:
        st.error(f"Crew failed: {state['error']}")
        st.stop()

    # Remember the results in session_state so they survive Streamlit's next rerun (e.g. a download click).
    st.session_state["report"] = state["report"]
    out = Path(__file__).parent / "output"

    def _read(name):
        """Text of output/<name>, or None when the file does not exist."""
        p = out / name
        return p.read_text(encoding="utf-8") if p.exists() else None

    st.session_state["changes"] = _read("changes.md")
    st.session_state["report_warn"] = _read("report_warnings.txt")
    import json as _json
    _sc = _read("score.json")
    st.session_state["score"] = _json.loads(_sc) if _sc else None
    st.session_state["best_of"] = _read("best_of.md")

    # Only treat the deck as real when it actually contains slide headings (a failed deck shows no tab).
    _dm = _read("deck.md") if want_deck else None
    st.session_state["deck"] = _dm if _dm and "## Slide" in _dm else None
    st.session_state["deck_warn"] = _read("deck_warnings.txt") if want_deck else None
    pptx = out / "deck.pptx"
    st.session_state["deck_pptx"] = pptx.read_bytes() if (want_deck and pptx.exists()) else None

if "report" not in st.session_state:  # first page load: show the last saved run so downloads work without a new run
    import json as _j
    _o = Path(__file__).parent / "output"
    def _rd(n):
        """Text of output/<n>, or None when the file does not exist."""
        q = _o / n
        return q.read_text(encoding="utf-8") if q.exists() else None
    if _rd("report.md"):
        st.session_state["report"] = _rd("report.md")
        st.session_state["changes"] = _rd("changes.md")
        st.session_state["report_warn"] = _rd("report_warnings.txt")
        _s = _rd("score.json")
        st.session_state["score"] = _j.loads(_s) if _s else None
        st.session_state["best_of"] = _rd("best_of.md")
        _d = _rd("deck.md")
        st.session_state["deck"] = _d if _d and "## Slide" in _d else None
        st.session_state["deck_warn"] = _rd("deck_warnings.txt")
        _p = _o / "deck.pptx"
        st.session_state["deck_pptx"] = _p.read_bytes() if (st.session_state["deck"] and _p.exists()) else None
        st.info("Showing the last saved run from the output folder. Click Run to create a new one.")

# ---- Results area: shown whenever a report exists (just produced, or loaded from output/) ----
report = st.session_state.get("report")
if report:
    st.divider()

    # The Pitch deck tab appears only when real slides exist.
    tab_names = ["📄 Report", "📈 Changes since last run", "🏆 Quality score"] + (["🎤 Pitch deck"] if st.session_state.get("deck") else [])
    tabs = st.tabs(tab_names)
    with tabs[0]:
        # Problems the guardrails could not fix are shown above the report so nobody shares it unaware.
        if st.session_state.get("report_warn"):
            st.warning("Quality check found problems the crew could not fix. Review before sharing:\n\n"
                       + "\n".join("- " + w for w in st.session_state["report_warn"].splitlines()))
        st.markdown(report)

        # Two download buttons side by side: Markdown and PDF.
        c1, c2 = st.columns(2)
        c1.download_button("⬇ Download Markdown", report, "report.md", "text/markdown")

        # Build the PDF only when the report text changed (Streamlit reruns the script on every click).
        if st.session_state.get("pdf_src") != report:
            st.session_state["pdf"] = to_pdf(report)
            st.session_state["pdf_src"] = report
        c2.download_button("⬇ Download PDF", st.session_state["pdf"], "report.pdf", "application/pdf")
    with tabs[1]:
        st.markdown(st.session_state.get("changes") or "No earlier run of this idea and city yet. Run it again next week to see price changes.")
    with tabs[2]:
        sc = st.session_state.get("score")
        if sc:
            st.markdown(scoring.format_score({"score": sc["score"], "breakdown": [tuple(b) for b in sc["breakdown"]]}))
            if st.session_state.get("best_of"):
                st.markdown(st.session_state["best_of"])   # table of all runs when "Runs" was more than 1
        else:
            st.markdown("No score available for this run.")
    if st.session_state.get("deck"):
        with tabs[3]:
            if st.session_state.get("deck_warn"):
                st.warning("Deck check: " + st.session_state["deck_warn"])
            st.markdown(st.session_state["deck"])
            if st.session_state.get("deck_pptx"):
                st.download_button(
                    "⬇ Download PowerPoint", st.session_state["deck_pptx"], "deck.pptx",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
