"""Pitch deck helpers: a guardrail that keeps the deck faithful to the report, and a .pptx exporter.

The deck is the "Pitch Deck agent" stretch goal. The agent writes the deck as Markdown (six sections headed
"## Slide N: <title>", each with a few bullets). This module (1) parses that Markdown, (2) validates it against the
finished report so the deck cannot introduce new numbers or unsupported claims, and (3) exports it to PowerPoint.
"""
import re
from pathlib import Path

import verify

# The six required slides, in order. The agent is told these exact titles.
SLIDE_TITLES = [
    "The opportunity",
    "Who we compete with",
    "What customers say",
    "The market gap",
    "Our positioning and price",
    "30-day launch plan",
]
MAX_BULLETS = 5       # a slide with more bullets is rejected as too dense
MAX_DECK_TRIES = 3    # the guardrail asks the agent to fix the deck at most this many times

# Matches a slide heading such as "## Slide 3: What customers say" and captures the number and the title.
SLIDE_RE = re.compile(r"^##\s*Slide\s*(\d+)\s*[:.\-]\s*(.+?)\s*$", re.M | re.I)


def _figures(text: str) -> set:
    """Numbers of 2+ digits (₹/commas normalised); slide numbers and 'Week N' are single digits and ignored."""
    return set(re.findall(r"\d{2,}", verify.normalize(text)))


def parse_deck(md: str):
    """Return [(number, title, [bullets])] in order."""
    slides = []

    # re.split with capture groups keeps the captured number and title in the result list.
    parts = SLIDE_RE.split(md)

    # parts = [preamble, num, title, body, num, title, body, ...]
    for i in range(1, len(parts) - 2, 3):
        body = parts[i + 2]

        # A bullet is any line starting with "- " or "* "; [2:] removes that marker.
        bullets = [ln.strip()[2:].strip() for ln in body.splitlines() if ln.strip().startswith(("- ", "* "))]
        slides.append((int(parts[i]), parts[i + 1].strip(), bullets))
    return slides


def make_deck_guardrail(report_sink: dict):
    """Reject decks that are not exactly 6 short slides or that contain numbers absent from the report.

    `report_sink` is a dict that the report guardrail fills with the final report text (key "report"), so this
    guardrail can compare the deck's numbers with the report's numbers. Returns a CrewAI guardrail function:
    it returns (True, text) to accept or (False, feedback) to make the agent try again.
    """
    tries = {"n": 0}   # a dict (not an int) so the inner function can modify it

    def guardrail(output):
        tries["n"] += 1
        raw = (getattr(output, "raw", None) or str(output)).strip()

        # Models sometimes wrap the whole answer in a ``` code fence; remove it.
        if raw.startswith("```"):
            raw = raw.split(chr(10), 1)[1].rsplit("```", 1)[0].strip()
        slides = parse_deck(raw)
        problems = []

        # --- structure ---
        if len(slides) != 6:
            problems.append(f"the deck has {len(slides)} slides; it must have exactly 6 headed '## Slide N: <title>'")
        for n, title, bullets in slides:
            if not bullets:
                problems.append(f"slide {n} has no bullet points")
            if len(bullets) > MAX_BULLETS:
                problems.append(f"slide {n} has {len(bullets)} bullets; the maximum is {MAX_BULLETS}")

        # --- claims the report cannot support ---
        claims = verify.superlative_claims(raw)       # "first", "only", "best", market-size claims
        if claims:
            problems.append("remove uncheckable claims (" + ", ".join(claims[:4]) + "); only state what the report evidences")
        tags = verify.listing_tag_complaints(raw)     # platform labels ("No seating") presented as feedback
        if tags:
            problems.append("platform listing tags are not customer feedback (" + ", ".join(tags) + "); remove them from the deck")
        for s in verify.absence_as_fact(raw):         # "no competitor offers X" stated as a fact
            problems.append(f"absence stated as fact ({s!r}); write that none was found in the sources reviewed")

        # --- numbers: every figure in the deck must also appear in the report ---
        report = report_sink.get("report", "")
        if report:
            extra = sorted(_figures(raw) - _figures(report))
            if extra:
                problems.append(
                    "these numbers do not appear in the report, so remove them or use the report figures: " + ", ".join(extra[:8])
                )
        if not problems:
            return True, raw

        # After the last allowed try, accept the deck anyway; validate_deck() will list what is still wrong.
        if tries["n"] >= MAX_DECK_TRIES:
            return True, raw
        return False, "Deck rejected: " + "; ".join(problems) + ". Return the COMPLETE corrected deck Markdown."

    return guardrail


def validate_deck(md: str, report: str) -> list:
    """Hard check after the run; returns a list of problems (empty if fine)."""
    slides = parse_deck(md)
    problems = []
    if len(slides) != 6:
        problems.append(f"deck has {len(slides)} slides instead of 6")
    extra = sorted(_figures(md) - _figures(report))
    if extra:
        problems.append("numbers not in the report: " + ", ".join(extra[:8]))
    claims = verify.superlative_claims(md)
    if claims:
        problems.append("uncheckable claims: " + ", ".join(claims[:4]))
    tags = verify.listing_tag_complaints(md)
    if tags:
        problems.append("listing tags presented as feedback: " + ", ".join(tags))
    for s in verify.absence_as_fact(md):
        problems.append("absence stated as fact: " + s)
    return problems


def export_pptx(md: str, path: Path) -> Path:
    """Write the deck Markdown to a simple, clean .pptx."""

    # python-pptx is imported here so the rest of the app still works if it is not installed.
    from pptx import Presentation
    from pptx.util import Pt

    # A blank presentation using the default template.
    prs = Presentation()

    # (no-op kept from the original code: keeps the default 4:3 slide size)
    prs.slide_width, prs.slide_height = prs.slide_width, prs.slide_height
    for n, title, bullets in parse_deck(md):
        layout = prs.slide_layouts[1]  # title + content
        slide = prs.slides.add_slide(layout)
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.clear()
        for i, b in enumerate(bullets):
            # The first bullet reuses the empty first paragraph; later bullets add new paragraphs.
            p = body.paragraphs[0] if i == 0 else body.add_paragraph()

            # Strip Markdown markup (**bold**, _italic_, `code`) and turn [text](url) links into plain text.
            p.text = re.sub(r"[*_`]|\[([^\]]+)\]\([^)]+\)", lambda m: m.group(1) or "", b)
            p.font.size = Pt(20)
    prs.save(str(path))
    return path
