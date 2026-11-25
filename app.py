"""Streamlit UI for LaunchScout."""
import io
import os
import queue
import threading
from pathlib import Path

import markdown as md
import streamlit as st
from dotenv import load_dotenv
from xhtml2pdf import pisa

load_dotenv()
from crew import run_best_of, run_crew  # noqa: E402
import scoring  # noqa: E402

AGENT_ORDER = ["Market Researcher", "Review Analyst", "Strategy Analyst", "Report Writer"]

st.set_page_config(page_title="LaunchScout", page_icon="🔭", layout="wide")
st.title("🔭 LaunchScout: Competitor Research Crew")
st.caption("Four AI agents research your market and deliver a sourced competitor report.")


def to_pdf(markdown_text: str) -> bytes:
    # The built-in PDF font has no rupee glyph
    html = md.markdown(markdown_text.replace("₹", "Rs "), extensions=["tables"])
    html = (
        "<html><head><style>body{font-family:Helvetica;font-size:10pt}"
        "table{border-collapse:collapse}td,th{border:1px solid #999;padding:3px}"
        "</style></head><body>" + html + "</body></html>"
    )
    buf = io.BytesIO()
    pisa.CreatePDF(html, dest=buf)
    return buf.getvalue()


missing = [k for k in ("SERPER_API_KEY",) if not os.getenv(k)]
if missing:
    st.warning(f"Missing in .env: {', '.join(missing)}. See .env.example.")

with st.form("idea"):
    idea = st.text_input("Business idea", "Healthy millet meal boxes delivered for lunch")
    city = st.text_input("City", "Coimbatore")
    customer = st.text_input("Target customer", "Office workers, age 25 to 40")
    known = st.text_input("Known competitors (optional)", "")
    want_deck = st.checkbox("Also create a 6-slide pitch deck (adds a Pitch Deck agent)")
    runs = st.number_input("Runs (keep the best-scoring one; each extra run costs the same again)", min_value=1, max_value=3, value=1)
    hierarchical = st.checkbox("Hierarchical mode: a Manager agent delegates the work (experimental, slower)")
    go = st.form_submit_button("Run research crew", type="primary")

if go:
    if not (idea and city and customer):
        st.error("Idea, city and target customer are required.")
        st.stop()

    events: queue.Queue = queue.Queue()
    state = {"done": False, "error": None, "report": None}

    def on_step(step):
        text = getattr(step, "thought", None) or getattr(step, "text", None) or str(step)
        tool = getattr(step, "tool", None)
        line = f"🔧 {tool}: {str(getattr(step, 'tool_input', ''))[:150]}" if tool else f"💭 {str(text)[:300]}"
        events.put(("step", line))

    def on_task(out):
        events.put(("task", f"✅ {getattr(out, 'agent', 'Agent')} finished"))

    def run():
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
            state["done"] = True

    threading.Thread(target=run, daemon=True).start()

    agent_order = AGENT_ORDER + (["Pitch Deck Designer"] if want_deck else [])
    cols = st.columns(len(agent_order))
    badges = [c.empty() for c in cols]
    log_box = st.empty()
    log: list[str] = []
    finished = 0

    def paint():
        for i, name in enumerate(agent_order):
            icon = "✅" if i < finished else ("⏳" if i == finished and not state["done"] else "⚪")
            badges[i].markdown(f"### {icon} {name}")
        log_box.code("\n".join(log[-25:]) or "Starting…", language=None)

    import time

    paint()
    while not (state["done"] and events.empty()):
        try:
            kind, line = events.get(timeout=0.5)
            log.append(line)
            if kind == "task":
                finished += 1
        except queue.Empty:
            pass
        paint()
    paint()

    if state["error"]:
        st.error(f"Crew failed: {state['error']}")
        st.stop()

    st.session_state["report"] = state["report"]
    out = Path(__file__).parent / "output"

    def _read(name):
        p = out / name
        return p.read_text(encoding="utf-8") if p.exists() else None

    st.session_state["changes"] = _read("changes.md")
    st.session_state["report_warn"] = _read("report_warnings.txt")
    import json as _json
    _sc = _read("score.json")
    st.session_state["score"] = _json.loads(_sc) if _sc else None
    st.session_state["best_of"] = _read("best_of.md")
    _dm = _read("deck.md") if want_deck else None
    st.session_state["deck"] = _dm if _dm and "## Slide" in _dm else None
    st.session_state["deck_warn"] = _read("deck_warnings.txt") if want_deck else None
    pptx = out / "deck.pptx"
    st.session_state["deck_pptx"] = pptx.read_bytes() if (want_deck and pptx.exists()) else None

if "report" not in st.session_state:  # first page load: show the last saved run so downloads work without a new run
    import json as _j
    _o = Path(__file__).parent / "output"
    def _rd(n):
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

report = st.session_state.get("report")
if report:
    st.divider()
    tab_names = ["📄 Report", "📈 Changes since last run", "🏆 Quality score"] + (["🎤 Pitch deck"] if st.session_state.get("deck") else [])
    tabs = st.tabs(tab_names)
    with tabs[0]:
        if st.session_state.get("report_warn"):
            st.warning("Quality check found problems the crew could not fix. Review before sharing:\n\n"
                       + "\n".join("- " + w for w in st.session_state["report_warn"].splitlines()))
        st.markdown(report)
        c1, c2 = st.columns(2)
        c1.download_button("⬇ Download Markdown", report, "report.md", "text/markdown")
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
                st.markdown(st.session_state["best_of"])
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
