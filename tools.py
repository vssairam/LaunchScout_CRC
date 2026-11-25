"""PriceFinderTool: lets the Market Researcher copy price evidence instead of inventing it."""
import html
import re
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

import verify

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
MAX_QUOTES = 10
CONTEXT = 90  # characters either side of a price


class PriceFinderInput(BaseModel):
    competitor: str = Field(..., description="Competitor name exactly as it will appear in the report table")
    url: str = Field(..., description="Exact URL of the page that should show this competitor's price")
    hint: str = Field("", description="What the product is, e.g. 'millet lunch meal' or 'cold pressed juice bottle'; helps find the menu/price line on pages that need JavaScript")


def _clean_text(html_text: str) -> str:
    text = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html_text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", re.sub(r"&nbsp;|&#160;", " ", text)).strip()


def _quotes_from(text: str) -> list:
    out = []
    for m in PRICE_RE.finditer(text):
        if not re.search(r"\d{2,}", m.group(0)):
            continue
        start = max(0, m.start() - CONTEXT)
        end = min(len(text), m.end() + CONTEXT)
        # trim to whole words
        chunk = text[start:end]
        if start > 0:
            chunk = chunk.split(" ", 1)[-1]
        if end < len(text):
            chunk = chunk.rsplit(" ", 1)[0]
        chunk = chunk.replace('"', "'").strip()
        if chunk and chunk not in out and not any(chunk in o or o in chunk for o in out):
            out.append(chunk)
        if len(out) >= MAX_QUOTES:
            break
    return out


class PriceFinderTool(BaseTool):
    name: str = "price_finder"
    description: str = (
        "Given a competitor name and the URL of its page, returns ready-to-use EVIDENCE lines containing the prices "
        "actually shown on that page (or in Google snippets of that exact page when the page needs JavaScript). "
        "Use it for EVERY competitor before writing a price, and copy one returned line verbatim. "
        "If it returns no lines, the price is Not found on that page: try another URL or write Not found."
    )
    args_schema: Type[BaseModel] = PriceFinderInput

    def _run(self, competitor: str, url: str, hint: str = "") -> str:
        readable, _ = verify.fetch_page(url)
        quotes, source = [], ""
        if readable:
            try:
                import requests

                raw = requests.get(url, timeout=25, headers=verify.HEADERS).text
                quotes, source = _quotes_from(_clean_text(raw)), "page text"
            except Exception:
                quotes = []
        if not quotes:
            extras = ([f"{hint} price", f"{hint} menu"] if hint else []) + ["price per meal starts from", "meal price per meal", "subscription plan price", "menu price rs"]
            for _ in range(2):  # Google snippets vary between calls; results accumulate
                for extra in extras:
                    snippets = verify.snippet_texts(competitor, url, extra)
                    quotes = [q for s in snippets for q in _quotes_from(s)][:MAX_QUOTES]
                    if quotes:
                        break
                if quotes:
                    break
            source = "Google snippet of this page"
        if not quotes:
            return (
                f"NO PRICE FOUND for {competitor} at {url} (page readable: {readable}). "
                "Do not guess a price. Try a different URL (menu, pricing or order page) or write Not found."
            )
        lines = [f'EVIDENCE | {competitor} | {url} | "{q}"' for q in quotes]
        return (
            f"{len(lines)} price line(s) for {competitor} from {source}. Copy exactly ONE line that matches the price you "
            "report (keep the quote unchanged), and state in the table what that price covers.\n" + "\n".join(lines)
        )
