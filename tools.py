"""PriceFinderTool: lets the Market Researcher copy price evidence instead of inventing it.

Language models tend to invent plausible prices. This tool removes that temptation: given a competitor and the URL
of its page, it fetches the page, finds the text around every price it can see and returns ready-made
`EVIDENCE | competitor | url | "quote"` lines. The agent must copy one of those lines unchanged, and verify.py later
checks that the quote really appears on the page. If the page needs JavaScript (Swiggy, Zomato) and shows no text,
the tool falls back to Google snippets of that exact page via Serper.
"""
import html
import re
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

import verify

# Regular expression that recognises the many ways Indian price text is written, e.g.
# Rs 180 / ₹1,299 / INR 250 / 180 rupees / 180/- ; numbers must be at least 2 digits
PRICE_RE = re.compile(
    # currency-marked amounts: Rs 180, Rs. 1,299, ₹250, INR 250, 180 rupees, 180/-
    r"(?<![A-Za-z])(?:₹|Rs\.?|INR)\s?\d[\d,]*(?:\.\d+)?(?:\s?[-–]\s?(?:₹|Rs\.?)?\s?\d[\d,]*)?"
    r"|\b\d[\d,]*(?:\.\d+)?\s?(?:rupees|/-)"
    # plan / price words followed by a bare amount or range: "monthly diet plan 20999-26999"
    r"|\b(?:price|pricing|plan|cost|starts? from|starting at)\b[^.\d]{0,40}\d{3,}(?:\s?[-–]\s?\d{3,})?"
    # menu style "Item name. 279" (Swiggy-like listings without a currency sign)
    r"|[A-Za-z)][A-Za-z0-9 ()&',/-]{8,70}\.\s(?!(?:19|20)\d\d\b)\d{2,4}\b(?!\s?(?:%|g\b|kcal))",
    re.I,
)
MAX_QUOTES = 10   # never return more than this many evidence lines (keeps the agent's context small)
CONTEXT = 90  # characters either side of a price


class PriceFinderInput(BaseModel):
    """The arguments the agent must supply when it calls the tool (CrewAI shows these descriptions to the LLM)."""
    competitor: str = Field(..., description="Competitor name exactly as it will appear in the report table")
    url: str = Field(..., description="Exact URL of the page that should show this competitor's price")
    hint: str = Field("", description="What the product is, e.g. 'millet lunch meal' or 'cold pressed juice bottle'; helps find the menu/price line on pages that need JavaScript")


def _clean_text(html_text: str) -> str:
    """Turn raw HTML into plain readable text: drop scripts/styles and tags, collapse whitespace."""
    text = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html_text)   # remove <script> and <style> blocks entirely
    text = re.sub(r"<[^>]+>", " ", text)                              # remove the remaining tags
    return re.sub(r"\s+", " ", re.sub(r"&nbsp;|&#160;", " ", text)).strip()  # non-breaking spaces, then runs of spaces


def _quotes_from(text: str) -> list:
    """Return up to MAX_QUOTES short text snippets, each surrounding one price found in `text`."""
    out = []
    for m in PRICE_RE.finditer(text):
        # A match with no 2-digit number (e.g. "Rs 5") is almost always noise, not a real price.
        if not re.search(r"\d{2,}", m.group(0)):
            continue

        # Take CONTEXT characters on each side of the price so the quote shows what the price is for.
        start = max(0, m.start() - CONTEXT)
        end = min(len(text), m.end() + CONTEXT)

        # trim to whole words so the quote does not start or end in the middle of a word
        chunk = text[start:end]
        if start > 0:
            chunk = chunk.split(" ", 1)[-1]
        if end < len(text):
            chunk = chunk.rsplit(" ", 1)[0]

        # Double quotes would break the EVIDENCE line format, so use single quotes inside the quote.
        chunk = chunk.replace('"', "'").strip()

        # Skip duplicates and snippets that overlap one we already have.
        if chunk and chunk not in out and not any(chunk in o or o in chunk for o in out):
            out.append(chunk)
        if len(out) >= MAX_QUOTES:
            break
    return out


class PriceFinderTool(BaseTool):
    """CrewAI tool: find price evidence for one competitor page and return it as EVIDENCE lines."""
    name: str = "price_finder"

    # This description is what the LLM reads to decide when and how to use the tool.
    description: str = (
        "Given a competitor name and the URL of its page, returns ready-to-use EVIDENCE lines containing the prices "
        "actually shown on that page (or in Google snippets of that exact page when the page needs JavaScript). "
        "Use it for EVERY competitor before writing a price, and copy one returned line verbatim. "
        "If it returns no lines, the price is Not found on that page: try another URL or write Not found."
    )
    args_schema: Type[BaseModel] = PriceFinderInput

    def _run(self, competitor: str, url: str, hint: str = "") -> str:
        # Step 1: try the page itself. verify.fetch_page returns (readable_text, ...) and an empty text for
        # pages that need JavaScript or block automated requests.
        # verify.fetch_page tells us whether the page has real readable text.
        readable, _ = verify.fetch_page(url)
        quotes, source = [], ""
        if readable:
            try:
                import requests

                # Fetch the raw HTML again (fetch_page returns normalised text) so the quote keeps its original wording.
                raw = requests.get(url, timeout=25, headers=verify.HEADERS).text
                quotes, source = _quotes_from(_clean_text(raw)), "page text"
            except Exception:
                quotes = []

        # Step 2: fall back to Google snippets of the same page (works for JavaScript-only sites).
        if not quotes:
            extras = ([f"{hint} price", f"{hint} menu"] if hint else []) + ["price per meal starts from", "meal price per meal", "subscription plan price", "menu price rs"]
            for _ in range(2):  # Google snippets vary between calls; results accumulate
                for extra in extras:
                    snippets = verify.snippet_texts(competitor, url, extra)

                    # Turn each Google snippet into price quotes (the same routine as for page text).
                    quotes = [q for s in snippets for q in _quotes_from(s)][:MAX_QUOTES]
                    if quotes:
                        break
                if quotes:
                    break
            source = "Google snippet of this page"

        # Step 3: nothing found anywhere -> tell the agent plainly not to guess.
        if not quotes:
            return (
                f"NO PRICE FOUND for {competitor} at {url} (page readable: {readable}). "
                "Do not guess a price. Try a different URL (menu, pricing or order page) or write Not found."
            )

        # Step 4: return the evidence lines in the exact format verify.parse_evidence understands.
        lines = [f'EVIDENCE | {competitor} | {url} | "{q}"' for q in quotes]
        return (
            f"{len(lines)} price line(s) for {competitor} from {source}. Copy exactly ONE line that matches the price you "
            "report (keep the quote unchanged), and state in the table what that price covers.\n" + "\n".join(lines)
        )
