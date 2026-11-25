"""Price-evidence verification for LaunchScout.

The researcher must back every numeric price with a line

    EVIDENCE | <competitor> | <url> | "<verbatim quote containing the price>"

`verify_research(raw)` checks each quote against the live page (or, for pages that need
JavaScript, against Google snippets from Serper) and returns a per-competitor status:

    verified            quote found on the cited page
    verified-approx     most quote words and the price figure found on the page
    verified-snippet    page unreadable; price figure appears in a search snippet for that site
    unverifiable        page unreadable and no snippet confirms it
    mismatch            page readable but the quote / price is NOT on it
    unsupported         table price is not backed by its own quote, or no evidence line exists
"""
import html
import os
import re
from functools import lru_cache
from urllib.parse import urlparse

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
MIN_READABLE_CHARS = 400
GOOD = {"verified", "verified-approx", "verified-snippet"}


def normalize(s: str) -> str:
    s = html.unescape(s or "").lower()
    s = s.replace("₹", " rs ").replace("rs.", " rs ").replace("inr", " rs ")
    s = re.sub(r"(?<=\d),(?=\d)", "", s)  # 20,999 -> 20999
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def numbers(s: str) -> set:
    return set(re.findall(r"\d{2,}", normalize(s)))


@lru_cache(maxsize=64)
def fetch_page(url: str):
    """Return (readable, normalized_text). Raw HTML is searched too, to catch embedded JSON."""
    try:
        r = requests.get(url, timeout=25, headers=HEADERS)
        raw = r.text
        text = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", raw)
        text = re.sub(r"<[^>]+>", " ", text)
        readable = r.status_code == 200 and len(re.sub(r"\s+", " ", text)) >= MIN_READABLE_CHARS
        return readable, normalize(text) + " " + normalize(raw)
    except Exception:
        return False, ""


_SNIPPETS: dict = {}


def snippet_texts(name: str, url: str, extra: str = "", rounds: int = 1) -> tuple:
    """Original-case Google snippets for the exact cited page (else other pages of the same site).

    Google returns different snippets between calls, so results accumulate across calls and rounds."""
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return ()
    parts = urlparse(url)
    domain = parts.netloc.replace("www.", "")
    slug = re.sub(r"[-_/]+", " ", parts.path).strip()
    slug = re.sub(r"rest\d+", "", slug)
    same_page, same_site = [], []
    queries = [f"{name} {extra} {domain}", f"{name} {slug} {domain}", f"{name} {domain} menu price"] * rounds
    for q in queries:
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json={"q": q, "num": 10},
                timeout=25,
            )
            for o in r.json().get("organic", []):
                text = (o.get("snippet", "") + " " + o.get("title", "")).strip()
                link = o.get("link", "")
                if link.rstrip("/") == url.rstrip("/"):
                    same_page.append(text)
                elif domain in link and _match(name, link + " " + text):
                    same_site.append(text)
        except Exception:
            continue
    ck = (name, url)
    found = list(_SNIPPETS.get(ck, ())) + (same_page or same_site)
    _SNIPPETS[ck] = tuple(dict.fromkeys(found))
    return _SNIPPETS[ck]


def snippets_for(name: str, url: str, quote: str = "") -> str:
    """Normalized snippets; searches again (up to 3 times) until the quote price figure shows up."""
    want = set(re.findall(r"\d{2,}", normalize(quote)))
    text = ""
    for _ in range(3):
        text = normalize(" ".join(snippet_texts(name, url, quote)))
        if not want or want & set(re.findall(r"\d{2,}", text)):
            break
    return text


def parse_evidence(raw: str):
    ev = []
    for ln in raw.splitlines():
        m = re.match(r"\s*EVIDENCE\s*\|\s*(.+?)\s*\|\s*(https?://\S+?)\s*\|\s*(.+?)\s*$", ln)
        if m:
            ev.append((m.group(1).strip(), m.group(2).strip(), m.group(3).strip().strip('"“”')))
    return ev


def _table_rows(raw: str):
    """Yield (header_cells, row_cells) for each data row of the first markdown table that has a Price column."""
    header = None
    for ln in raw.splitlines():
        if not ln.strip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if header is None:
            if any(c.lower().startswith("price") for c in cells):
                header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        yield header, cells


def table_prices(raw: str) -> dict:
    """competitor -> price text, for rows whose Price cell has a number. Numeric 'Monthly plan' cells are
    included too, under the key '<competitor> [monthly plan]', so they need evidence like any other price."""
    out = {}
    for header, cells in _table_rows(raw):
        for i, h in enumerate(header):
            hl = h.lower()
            if i < len(cells) and re.search(r"\d{2,}", cells[i]):
                if hl.startswith("price"):
                    out[cells[0]] = cells[i]
                elif hl.startswith("monthly"):
                    out[f"{cells[0]} [monthly plan]"] = cells[i]
    return out


NO_PLAN = re.compile(r"^\W*(not found|none|n/?a|no|-|unknown)\b", re.I)


def monthly_plan_competitors(raw: str) -> list:
    """Competitors whose Monthly plan cell says anything other than not found / none."""
    found = []
    for header, cells in _table_rows(raw):
        for i, h in enumerate(header):
            if h.lower().startswith("monthly") and i < len(cells) and cells[i] and not NO_PLAN.match(cells[i]):
                found.append(cells[0])
    return found


def _near(snippet_norm: str, quote: str) -> bool:
    """True if, around an occurrence of the quote price figure in the snippet, most quote words appear too.

    Stops a price from a different menu item (same number, different dish) passing as evidence."""
    nq = normalize(quote)
    figures = [n for n in re.findall(r"\d{2,}", nq)]
    words = [w for w in nq.split() if len(w) >= 4 and not w.isdigit() and w != "rs"]
    for fig in figures:
        for m in re.finditer(rf"(?<!\d){fig}(?!\d)", snippet_norm):
            window = snippet_norm[max(0, m.start() - 90): m.end() + 40]
            if not words or sum(w in window for w in words) / len(words) >= 0.6:
                return True
    return False


def _match(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a in b or b in a or a.split()[0] in b


def verify_research(raw: str) -> dict:
    """Return {competitor: (status, detail)} for every competitor with a numeric price."""
    evidence = parse_evidence(raw)
    results = {}
    for comp, cell in table_prices(raw).items():
        mine = [e for e in evidence if _match(comp, e[0])]
        if not mine:
            results[comp] = ("unsupported", "no EVIDENCE line for this price")
            continue
        status, detail = "unsupported", "no evidence line matched the table price"
        for _, url, quote in mine:
            if not numbers(cell) & numbers(quote):
                status, detail = "unsupported", f"table price '{cell}' not contained in its quote"
                continue
            readable, text = fetch_page(url)
            nq = normalize(quote)
            if readable and nq and nq in text:
                status, detail = "verified", url
                break
            if readable:
                words = [w for w in nq.split() if len(w) >= 3]
                hit = sum(w in text for w in words) / max(1, len(words))
                if hit >= 0.8 and numbers(quote) <= set(re.findall(r"\d{2,}", text)):
                    status, detail = "verified-approx", url
                    break
                status, detail = "mismatch", f"quote/price not found on {url}"
                continue
            snip = snippets_for(comp, url, quote)
            if snip and _near(snip, quote):
                status, detail = "verified-snippet", url
                break
            status, detail = "unverifiable", f"{url} is not readable and no search snippet confirms the price"
        results[comp] = (status, detail)
    return results


STALE_YEARS = 2  # a price from a source this many years old (or older) is too old to base a price on


def price_flags(raw: str, today_year: int = None) -> dict:
    """competitor -> list of problems with how the price is described (not whether it exists).

    - 'for two': a price for two people or a couple is not a per-meal price
    - 'stale': the price cell or its evidence quote shows a year that is STALE_YEARS or more years old
    """
    import datetime

    year_now = today_year or datetime.date.today().year
    quotes = {}
    for comp, _, quote in parse_evidence(raw):
        quotes.setdefault(comp, []).append(quote)
    flags = {}
    for comp, cell in table_prices(raw).items():
        text = cell + " " + " ".join(q for c, qs in quotes.items() if _match(comp, c) for q in qs)
        found = []
        if re.search(r"for\s+(two|2|a couple)|per\s+couple|for\s+couple|two\s+people|2\s+people", text, re.I):
            found.append("for-two")
        years = [int(y) for y in re.findall(r"\b(20\d\d)\b", cell)]
        if years and year_now - max(years) >= STALE_YEARS:
            found.append(f"stale-{max(years)}")
        if found:
            flags[comp] = found
    return flags


def format_report(results: dict) -> str:
    if not results:
        return "VERIFICATION: no numeric prices to verify."
    lines = ["VERIFICATION (automatic check of each price quote against its cited page):"]
    for comp, (st, detail) in results.items():
        lines.append(f"- {comp}: {st.upper()} ({detail})")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------
# Report-quality checks used by the guardrails
# ----------------------------------------------------------------------------------------------
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
MULT_RE = re.compile(_NUM + r"\s*(?:per\s+\w+)?\s*[x×*]\s*(\d{1,3})\s*(?:days?|meals?|times)?\s*=\s*" + _NUM, re.I)


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def arithmetic_errors(text: str) -> list:
    """Find 'A x N = B' statements (e.g. '219.50 per meal x 30 days = 6,570') and recompute them."""
    cleaned = re.sub(r"\([^)]*\)", " ", text.replace("₹", " ").replace("Rs.", " ").replace("Rs", " "))
    errors = []
    for m in MULT_RE.finditer(cleaned):
        a, n, b = _f(m.group(1)), _f(m.group(2)), _f(m.group(3))
        expected = a * n
        if abs(expected - b) > max(1.0, 0.001 * expected):
            errors.append(f"{m.group(1)} x {m.group(2)} = {expected:,.2f}, but the text says {m.group(3)}")
    return errors


def rows_failing_location(raw: str) -> list:
    """Competitors whose 'Location confirmed' cell starts with No (they must be excluded, not listed)."""
    bad = []
    for header, cells in _table_rows(raw):
        for i, h in enumerate(header):
            if h.lower().startswith("location") and i < len(cells) and re.match(r"\W*no\b", cells[i], re.I):
                bad.append(cells[0])
    return bad


SUPERLATIVE_RE = re.compile(
    r"(?:\b[\w']+'s|\bthe)\s+first\b(?!\s+(?:\d|week|month|day|year|few|batch|customers?|subscribers?|orders?|step|phase|time|slide|10|50|100))"
    r"|\bfirst[- ](?:ever\s+)?(?:subscription|millet|service|of\s+its\s+kind|in\b)"
    r"|\bthe\s+only\b|\bonly\s+(?:one|service|provider|company)\b|\bno\s+other\b|\bunmatched\b|\bunique\s+in\b",
    re.I,
)
MARKET_CLAIM_RE = re.compile(
    r"growing\s+demand|rising\s+demand|unmet\s+demand|untapped|booming|fast(?:est)?[- ]growing|huge\s+market|massive\s+market"
    r"|growing\s+(?:[\w-]+\s+){0,2}(?:consumer|market|base|segment|trend|awareness)|\bdemand\s+for\b|(?:health[- ]conscious|healthy|consumer|customer|market)\s+(?:[\w-]+\s+){0,3}demand\b|\bappreciated\s+by\b|\bhealth\s+benefits\s+(?:are|is)\b",
    re.I,
)


def superlative_claims(text: str) -> list:
    """Uncheckable 'first / only' claims and market-size hype."""
    return sorted({m.group(0).strip() for m in SUPERLATIVE_RE.finditer(text)} | {m.group(0).strip() for m in MARKET_CLAIM_RE.finditer(text)})


RETAIL_URL_RE = re.compile(r"/collections?/|/products?/|/dp/|/shop/|/store/|/catalog|amazon\.|flipkart\.|bigbasket\.|blinkit\.|zepto", re.I)
LISTING_TAG_RE = re.compile(r"no\s+seating(?:\s+is)?(?:\s+available)?|delivery\s+only|vegetarian\s+only|pure\s+veg\s+only|open\s+now", re.I)


def rows_without_city(raw: str, city: str) -> list:
    """Competitors whose 'Location confirmed' cell does not mention the target city (e.g. just 'their website')."""
    bad = []
    for header, cells in _table_rows(raw):
        for i, h in enumerate(header):
            if h.lower().startswith("location") and i < len(cells) and city.lower() not in cells[i].lower():
                bad.append(cells[0])
    return bad


def retail_rows(raw: str) -> list:
    """Competitors whose price comes from what looks like an online shop / product page, not a meal service."""
    bad = []
    for ln in raw.splitlines():
        if ln.strip().startswith("|") and RETAIL_URL_RE.search(ln):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if cells and not set("".join(cells)) <= set("-: ") and re.search(r"direct|adjacent", ln, re.I):
                bad.append(cells[0])
    return bad


def listing_tag_complaints(text: str) -> list:
    """'No seating available' style platform tags presented as customer feedback."""
    return sorted({m.group(0).lower() for m in LISTING_TAG_RE.finditer(text)})


SUBSCRIPTION_RE = re.compile(r"subscription|monthly|weekly\s+(?:plan|pack|meal)|per\s+month|meal\s+plan", re.I)


def missed_subscriptions(raw: str) -> list:
    """Competitors whose own EVIDENCE quote mentions a subscription/monthly plan while the Monthly plan cell says Not found."""
    has_plan = {_key(c) for c in monthly_plan_competitors(raw)}
    missed = []
    for comp, _, quote in parse_evidence(raw):
        if SUBSCRIPTION_RE.search(quote) and _key(comp) not in has_plan and comp not in missed:
            missed.append(comp)
    return missed


def _key(name: str) -> str:
    return normalize(name.replace("[monthly plan]", ""))


REQUIRED_COLUMNS = ("competitor", "direct", "location", "price", "monthly")


def missing_columns(raw: str, required=REQUIRED_COLUMNS) -> list:
    """Required column names missing from the first markdown table (matched by header prefix, e.g. 'Price (unit...)')."""
    for ln in raw.splitlines():
        if ln.strip().startswith("|") and not set(ln.replace("|", "").strip()) <= set("-: "):
            headers = [c.strip().lower() for c in ln.strip().strip("|").split("|")]
            return [r for r in required if not any(h.startswith(r) for h in headers)]
    return list(required)  # no table at all


# ---------------------------------------------------------------------------------------------
# Final consistency checks: Direct labels, contact notes, absence-as-fact wording
# ---------------------------------------------------------------------------------------------
GENERIC_WORDS = {
    "healthy", "delivered", "delivery", "lunch", "dinner", "breakfast", "boxes", "box", "meal", "meals", "food", "foods",
    "service", "services", "subscription", "daily", "fresh", "home", "order", "online", "best", "with", "from", "that",
    "this", "your", "for", "and", "the", "new", "pressed", "cold",
}


def idea_keywords(idea: str) -> list:
    """Distinctive words of the idea (e.g. 'millet', 'juice'); generic food/delivery words are ignored."""
    words = re.findall(r"[a-z]{4,}", idea.lower())
    return [w for w in dict.fromkeys(words) if w not in GENERIC_WORDS]


def direct_without_product(raw: str, idea: str) -> list:
    """Competitors marked Direct although neither their row nor their evidence quotes mention the idea's product word."""
    keys = idea_keywords(idea)
    if not keys:
        return []
    quotes = {}
    for comp, _, quote in parse_evidence(raw):
        quotes.setdefault(_key(comp), []).append(quote)
    bad = []
    for header, cells in _table_rows(raw):
        for i, h in enumerate(header):
            if h.lower().startswith("direct") and i < len(cells) and re.match(r"\W*direct", cells[i], re.I):
                text = (" ".join(cells) + " " + " ".join(quotes.get(_key(cells[0]), []))).lower()
                if not any(k in text for k in keys):
                    bad.append(cells[0])
    return bad


def direct_called_unfocused(raw: str, idea: str) -> list:
    """Direct competitors that the market-gap section describes as not focused on the product (clause by clause)."""
    sec = re.search(r"^##\s*3\..*?(?=^##\s*4\.)", raw, re.M | re.S)
    if not sec:
        return []
    clauses = re.split(r"[.;]|,\s+and\s+|\s+but\s+|\s+while\s+|\s+whereas\s+", sec.group(0), flags=re.I)
    unfocused = re.compile(r"(?:not|isn't|n't)\s+(?:\w+[- ])?(?:focus|specific|specialis|dedicated)", re.I)
    bad = []
    for header, cells in _table_rows(raw):
        for i, h in enumerate(header):
            if h.lower().startswith("direct") and i < len(cells) and re.match(r"\W*direct", cells[i], re.I):
                name = cells[0].split("(")[0].strip().lower()
                if any(name in c.lower() and unfocused.search(c) for c in clauses):
                    bad.append(cells[0])
    return bad


def contact_contradictions(raw: str) -> list:
    """'Prices to verify' says No contact found for a competitor whose table row has a phone number or link."""
    contacts = {}
    for header, cells in _table_rows(raw):
        for i, h in enumerate(header):
            if h.lower().startswith("contact") and i < len(cells):
                contacts[_key(cells[0])] = cells[i]
    bad = []
    for m in re.finditer(r"^[-*]\s*([^:\n]+):\s*No contact found", raw, re.M | re.I):
        name = m.group(1).strip()
        cell = contacts.get(_key(name), "")
        if re.search(r"\d{8,}|\d{4,5}\s\d{4,5}|https?://|wa\.me", cell):
            bad.append(name)
    return bad


ABSENCE_RE = re.compile(
    r"(?:does\s+not|doesn't|do\s+not|don't|no)\s+(?:currently\s+)?(?:provide|offer|have|include)?\s*(?:a\s+|any\s+)?(?:\w+\s+)?(?:subscription|monthly\s+plan|recurring)",
    re.I,
)
QUALIFIER_RE = re.compile(r"\b(found|identified|sources?|evidence|reviewed|appears?|seems?|visible|listed)\b", re.I)


def absence_as_fact(text: str) -> list:
    """Sentences asserting that a competitor lacks a subscription without saying it is based on what was found."""
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        if ABSENCE_RE.search(sentence) and not QUALIFIER_RE.search(sentence):
            out.append(sentence.strip()[:90])
    return out


# ---------------------------------------------------------------------------------------------
# Relevance heuristics (used in the final warnings and in the run score)
# ---------------------------------------------------------------------------------------------
WEIGHT_UNIT_RE = re.compile(r"per\s+\d+\s*(?:g|gm|gms|grams?|kg|ml|l|litres?)\b|per\s+(?:kg|gram|packet|pack)\b|/\s*(?:kg|100\s?g)\b", re.I)
BOILERPLATE_REVIEW_RE = re.compile(r"average\s+rating|based\s+on\s+user\s+reviews|currently\s+holds|rated\s+\d(?:\.\d)?\s+based\s+on", re.I)


def non_meal_unit_prices(raw: str) -> list:
    """Competitors whose price is per weight / pack (a packaged product), not per meal or unit of service."""
    return [c for c, cell in table_prices(raw).items() if not c.endswith("[monthly plan]") and WEIGHT_UNIT_RE.search(cell)]


def boilerplate_review_quotes(raw: str) -> list:
    """Section 2 entries whose 'reviewer said' quote is really a platform summary such as 'holds an average rating of 5.0'."""
    sec = raw.split("## 3")[0].split("## 2")[-1] if "## 2" in raw else ""
    out = []
    current = ""
    for ln in sec.splitlines():
        if ln.startswith("###"):
            current = ln.lstrip("# ").strip()
        elif BOILERPLATE_REVIEW_RE.search(ln) and re.search(r"said|wrote|mention", ln, re.I):
            out.append(current or "a competitor")
    return out


def gap_contradicts_direct(raw: str, idea: str) -> list:
    """Direct competitors named after the product while the report claims nobody has a dedicated product focus."""
    keys = idea_keywords(idea)
    if not keys:
        return []
    claim = re.compile(
        r"(?:no|lack|lacks|absence|missing|without)\s+(?:of\s+)?(?:a\s+|any\s+)?(?:dedicated|exclusive|specific|specialis\w+)[^.]{0,40}?(?:" + "|".join(keys) + r")"
        r"|competitors?\s+(?:lack|do\s+not\s+have|don't\s+have)[^.]{0,30}?(?:exclusive|dedicated)[^.]{0,20}?(?:" + "|".join(keys) + ")",
        re.I,
    )
    if not claim.search(raw):
        return []
    named = []
    for header, cells in _table_rows(raw):
        for i, h in enumerate(header):
            if h.lower().startswith("direct") and i < len(cells) and re.match(r"\W*direct", cells[i], re.I):
                if any(k in cells[0].lower() for k in keys):
                    named.append(cells[0])
    return named


GENERIC_SOCIAL_RE = re.compile(r"https?://(?:www\.)?(?:instagram|facebook|twitter|x)\.com/(?:popular|explore|search|hashtag|tags?)/", re.I)
NO_COMPLAINT_RE = re.compile(r"^\W*(no |none|not |evidence|n/a|insufficient|too thin)", re.I)


def generic_social_urls(raw: str) -> list:
    """Cited URLs that are social-media search/popular pages, not a page about the competitor."""
    return sorted(set(GENERIC_SOCIAL_RE.findall(raw) and [m.group(0) for m in re.finditer(GENERIC_SOCIAL_RE.pattern + r"[^\s)\]]*", raw, re.I)]))


def unsourced_complaints(raw: str) -> list:
    """Section 2 complaint bullets (a competitor heading followed by a claim) that carry no URL, or a generic social-media URL."""
    src = {m.group(1): m.group(2) for m in re.finditer(r"^\s*(\d+)\.\s*\[[^\]]*\]\((https?://[^)\s]+)\)", raw.split("## 6")[-1], re.M)}
    sec = raw.split("## 3")[0].split("## 2")[-1] if "## 2" in raw else ""
    out, current = [], ""
    for ln in sec.splitlines():
        if ln.startswith("#"):
            current = ln.lstrip("# ").strip()
            continue
        body = ln.strip().lstrip("-* ").strip()
        if not ln.strip().startswith(("-", "*")) or not current or NO_COMPLAINT_RE.match(body.replace("*", "")):
            continue
        if current.lower().startswith(("recurring", "evidence")):
            continue
        if body.replace(chr(42),"").rstrip().endswith(chr(58)):
            continue  # a sub-heading such as Complaints:
        if not re.search(r"reviewer|reviews?\b|rating|\bsaid\b|\bwrote\b|complain|[\"“]", body, re.I):
            continue  # not phrased as a customer claim
        urls = re.findall(r"https?://[^\s)\]]+", ln)
        urls += [src[n] for n in re.findall(r"\[(\d+)\]", ln) if n in src]  # [3] points at the numbered Sources list
        if not urls or any(GENERIC_SOCIAL_RE.match(u) for u in urls):
            out.append(f"{current}: {body[:80]}")
    return out


CITY_SLUGS = ("hyderabad", "bangalore", "bengaluru", "chennai", "mumbai", "delhi", "pune", "kolkata", "kochi", "madurai",
              "trichy", "tiruchirappalli", "salem", "erode", "tiruppur", "ahmedabad", "jaipur", "lucknow", "mysore", "mysuru",
              "vellore", "thanjavur", "noida", "gurgaon", "gurugram", "chandigarh", "indore", "nagpur", "surat", "vizag", "thiruvananthapuram")
CITY_URL_RE = re.compile(r"https?://(?:www\.)?(?:zomato|swiggy|justdial|magicpin|dineout)\.com/(?:city/)?([A-Za-z-]+)/", re.I)


def wrong_city_urls(raw: str, city: str) -> list:
    """Cited listing/review URLs whose path names a different city (e.g. zomato.com/hyderabad/...), so the page is not about the target city."""
    want = re.sub(r"[^a-z]", "", city.lower())
    out = []
    for u in re.findall(r"https?://[^\s)\]]+", raw):
        m = CITY_URL_RE.match(u)
        if m:
            slug = re.sub(r"[^a-z]", "", m.group(1).lower())
            if slug in CITY_SLUGS and slug != want and u not in out:
                out.append(u)
    return out
