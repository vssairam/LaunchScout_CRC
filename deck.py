"""Pitch deck helpers: a guardrail that keeps the deck faithful to the report, and a .pptx exporter."""
import re
from pathlib import Path

import verify

SLIDE_TITLES = [
    "The opportunity",
    "Who we compete with",
    "What customers say",
    "The market gap",
    "Our positioning and price",
    "30-day launch plan",
]
MAX_BULLETS = 5
MAX_DECK_TRIES = 3
SLIDE_RE = re.compile(r"^##\s*Slide\s*(\d+)\s*[:.\-]\s*(.+?)\s*$", re.M | re.I)


def _figures(text: str) -> set:
    """Numbers of 2+ digits (₹/commas normalised); slide numbers and 'Week N' are single digits and ignored."""
    return set(re.findall(r"\d{2,}", verify.normalize(text)))


def parse_deck(md: str):
    """Return [(number, title, [bullets])] in order."""
    slides = []
    parts = SLIDE_RE.split(md)
    # parts = [preamble, num, title, body, num, title, body, ...]
    for i in range(1, len(parts) - 2, 3):
        body = parts[i + 2]
        bullets = [ln.strip()[2:].strip() for ln in body.splitlines() if ln.strip().startswith(("- ", "* "))]
        slides.append((int(parts[i]), parts[i + 1].strip(), bullets))
    return slides


def make_deck_guardrail(report_sink: dict):
    """Reject decks that are not exactly 6 short slides or that contain numbers absent from the report."""
    tries = {"n": 0}

    def guardrail(output):
        tries["n"] += 1
        raw = (getattr(output, "raw", None) or str(output)).strip()
        if raw.startswith("```"):
            raw = raw.split(chr(10), 1)[1].rsplit("```", 1)[0].strip()
        slides = parse_deck(raw)
        problems = []
        if len(slides) != 6:
            problems.append(f"the deck has {len(slides)} slides; it must have exactly 6 headed '## Slide N: <title>'")
        for n, title, bullets in slides:
            if not bullets:
                problems.append(f"slide {n} has no bullet points")
            if len(bullets) > MAX_BULLETS:
                problems.append(f"slide {n} has {len(bullets)} bullets; the maximum is {MAX_BULLETS}")
        claims = verify.superlative_claims(raw)
        if claims:
            problems.append("remove uncheckable claims (" + ", ".join(claims[:4]) + "); only state what the report evidences")
        tags = verify.listing_tag_complaints(raw)
        if tags:
            problems.append("platform listing tags are not customer feedback (" + ", ".join(tags) + "); remove them from the deck")
        for s in verify.absence_as_fact(raw):
            problems.append(f"absence stated as fact ({s!r}); write that none was found in the sources reviewed")
        report = report_sink.get("report", "")
        if report:
            extra = sorted(_figures(raw) - _figures(report))
            if extra:
                problems.append(
                    "these numbers do not appear in the report, so remove them or use the report figures: " + ", ".join(extra[:8])
                )
        if not problems:
            return True, raw
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
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = prs.slide_width, prs.slide_height
    for n, title, bullets in parse_deck(md):
        layout = prs.slide_layouts[1]  # title + content
        slide = prs.slides.add_slide(layout)
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.clear()
        for i, b in enumerate(bullets):
            p = body.paragraphs[0] if i == 0 else body.add_paragraph()
            p.text = re.sub(r"[*_`]|\[([^\]]+)\]\([^)]+\)", lambda m: m.group(1) or "", b)
            p.font.size = Pt(20)
    prs.save(str(path))
    return path
