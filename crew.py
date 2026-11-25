"""LaunchScout crew: four sequential agents that produce a competitor report.

This is the heart of the project. It contains:

  * the four CrewAI agents (Market Researcher, Review Analyst, Strategy Analyst, Report Writer) and their tasks,
    plus the optional Pitch Deck Designer agent and the optional hierarchical Manager agent (build_crew);
  * two GUARDRAILS (make_competitor_guardrail, make_report_guardrail). A guardrail is a function that CrewAI
    runs on an agent's answer. It returns (True, answer) to accept it, or (False, feedback) to send the agent
    back to try again with that feedback. The guardrails use the checks in verify.py to catch invented prices,
    wrong-city listings, arithmetic mistakes, uncheckable claims and similar problems;
  * the entry points run_crew (one run), run_best_of (N runs, keep the best score) and the helpers that save
    every run under output/ (report, deck, score, history, run log).

Data flow: research table -> reviews -> strategy -> report -> (optional) deck. Each task passes its output to
the next one through the `context=[...]` argument.
"""
import os
import re
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv
from crewai import Agent, Crew, LLM, Process, Task
from crewai_tools import FileWriterTool, ScrapeWebsiteTool, SerperDevTool

import deck
import history
import scoring
import verify
from tools import PriceFinderTool

# Read SERPER_API_KEY, OPENAI_API_KEY and LLM_MODEL from the .env file (the crewai tools read the keys from the environment).
load_dotenv()

# All results are written here: report.md, deck.md/.pptx, score.json, run.log, history/ and runs/.
OUTPUT_DIR = Path(__file__).parent / "output"
REPORT_NAME = "report.md"


def _llm() -> LLM:
    """Create the language model used by every agent. The model name comes from LLM_MODEL in .env
    (any LiteLLM model string, e.g. openai/gpt-4o); a low temperature keeps the agents factual and repeatable."""
    return LLM(model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"), temperature=0.2)


# Quality targets enforced by the guardrails:
#   MIN_COMPETITORS / MIN_DIRECT - the research must list at least this many competitors / Direct competitors;
#   MAX_*_TRIES                  - how many times a guardrail may send the agent back before giving up and
#                                  passing the best attempt on (run_crew then lists what is still wrong).
MIN_COMPETITORS = 3
MIN_DIRECT = 2
MAX_RESEARCH_TRIES = 3
MAX_REPORT_TRIES = 3


def autofill_evidence(raw: str, hint: str = "") -> str:
    """Add EVIDENCE lines in code for table prices that lack one.

    For each competitor with a numeric price and no EVIDENCE line, run the price finder on every URL
    in the research output and keep the first quote that (a) contains the table price figure and
    (b) belongs to that competitor (name appears in the URL or the quote)."""

    # Competitors that have a numeric price in the table (including numeric monthly plans).
    prices = verify.table_prices(raw)

    # EVIDENCE lines the agent already wrote.
    have = verify.parse_evidence(raw)

    # Every URL mentioned in the research output (once each, in order); these are the pages we may search for a price.
    urls = list(dict.fromkeys(u.rstrip(".,)") for u in re.findall(r"https?://[^\s|)\]]+", raw) if "EVIDENCE" not in u))
    finder = PriceFinderTool()
    added = []

    # For each priced competitor that lacks an EVIDENCE line, look for a quote on the known URLs.
    for comp, cell in prices.items():
        if any(verify._match(comp, e[0]) for e in have):
            continue
        want = verify.numbers(cell)
        base = comp.replace(" [monthly plan]", "")

        # Words of the competitor's name; a quote or URL must contain one of them so we do not attach another business's price.
        name_tokens = [t for t in verify.normalize(base).split() if len(t) >= 4] or verify.normalize(base).split()
        for url in urls:
            slug = verify.normalize(url)
            try:
                # Ask the price finder tool (the same one the agent has) for price lines on this URL.
                out = finder._run(comp, url, hint)
            except Exception:
                continue
            for ln in out.splitlines():
                m = re.match(r'\s*EVIDENCE\s*\|\s*.+?\s*\|\s*(https?://\S+?)\s*\|\s*"(.+)"\s*$', ln)
                if not m:
                    continue
                quote = m.group(2)

                # Accept the quote only if it contains the table's price figure AND belongs to this competitor.
                if want & verify.numbers(quote) and any(t in slug or t in verify.normalize(quote) for t in name_tokens):
                    added.append(f'EVIDENCE | {comp} | {m.group(1)} | "{quote}"')
                    break
            else:
                continue
            break
    return raw + ((chr(10) + chr(10) + chr(10).join(added)) if added else "")


def make_competitor_guardrail(city: str = "", idea: str = ""):
    """Return the guardrail for the Market Researcher task.

    The guardrail rejects the research (and tells the agent exactly what to fix) until it has at least 3 competitors
    with source URLs and at least 2 Direct ones, a 'Price conflicts' line, the required columns, a city-confirmed
    location for each row, no online shops, no 'for two' or stale prices, and a verified EVIDENCE quote for every
    numeric price. On the final allowed attempt it accepts the output anyway, adding a NOTE and a VERIFICATION block.
    """
    tries = {"n": 0}

    def guardrail(output):
        tries["n"] += 1
        raw = getattr(output, "raw", None) or str(output)

        # Competitor rows = table lines that contain a URL (skipping the |---| separator line).
        rows = [
            ln for ln in raw.splitlines()
            if ln.strip().startswith("|") and "http" in ln
            and not set(ln.replace("|", "").strip()) <= set("-: ")
        ]

        # Of those, the rows whose 'Direct or adjacent' cell says Direct.
        direct = [r for r in rows if re.search(r"\|\s*direct\s*\|", r, re.I)]

        # Collect every problem found; each becomes one sentence of feedback for the agent.
        problems = []
        if len(rows) < MIN_COMPETITORS:
            problems.append(
                f"only {len(rows)} competitor row(s) with a source URL; at least {MIN_COMPETITORS} are required. "
                "Run NEW, wider searches: other platforms (Justdial, Zomato, Swiggy, Google Maps, Instagram, local blogs and news lists), "
                "synonyms for the product, and neighbouring areas of the city; keep the rows already found and add more"
            )
        if len(direct) < MIN_DIRECT:
            problems.append(
                f"only {len(direct)} competitor(s) marked Direct; search specifically for businesses that sell the SAME product as the idea "
                "(e.g. search the product keyword plus the city on Zomato, Swiggy, Justdial and Google Maps) and only keep Direct where a page shows it"
            )
        if not re.search(r"price conflicts", raw, re.I):
            problems.append("the 'Price conflicts' line is missing; add it (write 'none' if no business shows differing prices)")

        # Before judging the prices, repair missing EVIDENCE lines in code (the model often forgets them).
        raw = autofill_evidence(raw)

        # Direct must be earned: the row or its evidence has to mention the product word of the idea.
        for c in (verify.direct_without_product(raw, idea) if idea else []):
            problems.append(f"{c} is marked Direct but nothing in its row or evidence mentions the product ({', '.join(verify.idea_keywords(idea))}); mark it Adjacent or show a page that proves it sells it")

        # The competitor table must keep all required columns (agents sometimes drop one).
        cols = verify.missing_columns(raw)
        if cols:
            problems.append("the competitor table must keep these columns: Competitor | Direct or adjacent | Location confirmed | Price | Monthly plan | ... ; missing: " + ", ".join(cols))

        # If an evidence quote mentions a subscription, the Monthly plan cell may not say 'Not found'.
        for c in verify.missed_subscriptions(raw):
            problems.append(
                f"the evidence quote for {c} mentions a subscription / monthly or weekly plan, but its Monthly plan cell says Not found; "
                "fill the Monthly plan column from that evidence (describe the plan and add its own EVIDENCE line)"
            )

        # A competitor whose location is not confirmed (cell starts with No) must be removed.
        for c in verify.rows_failing_location(raw):
            problems.append(f"{c} is not confirmed to operate in the target city (Location confirmed = No); remove it from the table and replace it with a local competitor")

        # The Location cell must name the target city, not just a website.
        for c in (verify.rows_without_city(raw, city) if city else []):
            problems.append(f"{c}: the Location confirmed cell must name {city} and the page evidence (e.g. address or delivery area); a website name is not a location")

        # Online shops and product pages (packaged goods) are not meal-delivery competitors.
        for c in verify.retail_rows(raw):
            problems.append(f"{c} is sourced from an online shop or product page (packaged goods), not a meal-delivery business; remove it")

        # Check every numeric price against its cited page (or search snippets); only MISMATCH / UNSUPPORTED are blocking.
        checks = verify.verify_research(raw)
        bad = {c: v for c, v in checks.items() if v[0] in ("mismatch", "unsupported")}
        for c, (st, detail) in bad.items():
            problems.append(
                f"price for {c} is {st.upper()} ({detail}): re-scrape that page and copy a verbatim quote containing the price into an EVIDENCE line, "
                "call price_finder again for that competitor with the exact page URL (try other URLs and hints such as the product name plus price per meal) and copy a returned line; "
                "write Not found only if price_finder returns NO PRICE FOUND for every page you can find"
            )

        # Price problems: a price 'for two', or from a source two or more years old.
        for c, fl in verify.price_flags(raw).items():
            if "for-two" in fl:
                problems.append(f"price for {c} is for two people / a couple, not a single meal; find a per-meal price or write Not found (and do not mark it Direct if it is only a dine-in restaurant)")
            for f in fl:
                if f.startswith("stale-"):
                    problems.append(f"price for {c} comes from a {f[6:]} source, too old to use; find a current price (menu, order page, Swiggy/Zomato) or write Not found")

        # The VERIFICATION block is appended to the research output so later agents can see which prices are verified.
        report = chr(10) + chr(10) + verify.format_report(checks)
        if not problems:
            return True, raw + report

        # Out of retries: accept the output but add a NOTE about what is still unmet (the report must say so).
        if tries["n"] >= MAX_RESEARCH_TRIES:
            return True, raw + report + (
                f"{chr(10)}{chr(10)}NOTE: after {tries['n']} research attempts the research still has {len(rows)} sourced competitor(s), "
                f"{len(direct)} direct; the local market looks thin or poorly documented online. Unmet checks: " + "; ".join(problems)
            )
        return False, "Research incomplete: " + "; ".join(problems) + ". Keep every rule (location evidence, price with unit, source URL)."

    return guardrail


def make_report_guardrail(sink: Optional[dict] = None, city: str = "", idea: str = ""):
    """Return the guardrail for the Report Writer task.

    It rejects a report that is incomplete (missing sections, URLs not in Sources, dropped columns) or that
    contradicts itself or the evidence (arithmetic errors, 'first/only' claims, absence stated as fact, a Direct
    competitor called unfocused, complaints without a real source, pages from another city...). `sink` receives
    the final report text and any unresolved problems so the deck guardrail and run_crew can use them.
    """
    tries = {"n": 0}

    def guardrail(output):
        tries["n"] += 1
        raw = (getattr(output, "raw", None) or str(output)).strip()
        problems = []

        # All six numbered sections must be present.
        for i in range(1, 7):
            if not re.search(rf"^##\s*{i}\.", raw, re.M):
                problems.append(f"section '## {i}.' is missing")
        if not re.search(r"price conflicts", raw, re.I):
            problems.append("a 'Price conflicts' line under the table is missing")

        # Every URL cited in sections 1-5 must also be listed in section 6 (Sources), which needs at least 3 URLs.
        if "## 6" in raw:
            body, sources = raw.split("## 6", 1)
            cited = set(u.rstrip(".,") for u in re.findall(r"https?://[^\s)\]]+", body))
            listed = set(u.rstrip(".,") for u in re.findall(r"https?://[^\s)\]]+", sources))
            missing = sorted(cited - listed)
            if missing:
                problems.append("these cited URLs are not listed in Sources: " + ", ".join(missing[:5]))
            if len(listed) < 3:
                problems.append("Sources lists fewer than 3 URLs")

        # Direct must be earned: the row or its evidence has to mention the product word of the idea.
        for c in (verify.direct_without_product(raw, idea) if idea else []):
            problems.append(f"{c} is marked Direct but its row never mentions the product; mark it Adjacent")

        # Section 3 must not say a Direct competitor is unfocused (then it is Adjacent, not Direct).
        for c in (verify.direct_called_unfocused(raw, idea) if idea else []):
            problems.append(f"section 3 says {c} is not focused on the product, so it must be Adjacent, not Direct; fix the label and the gap statement")

        # 'No contact found' must not appear for a competitor whose row has a phone number or link.
        for c in verify.contact_contradictions(raw):
            problems.append(f"Prices to verify says No contact found for {c}, but its table row has a contact; use that contact in the bullet")

        # Absence ('none offer a subscription') must be worded as 'none found in the sources reviewed'.
        for s in verify.absence_as_fact(raw.split("## 4")[0].split("## 3")[-1] if "## 3" in raw else ""):
            problems.append(f"absence stated as fact ({s!r}); say none was found in the sources reviewed")

        # The competitor table must keep all required columns (agents sometimes drop one).
        cols = verify.missing_columns(raw)
        if cols:
            problems.append("section 1 table dropped required columns (" + ", ".join(cols) + "); keep Competitor | Direct or adjacent | Location confirmed | Price | Monthly plan | Offers | Channel | Contact / order link | Source")

        # Recompute statements such as '219.50 x 30 = 6,570' and reject wrong ones.
        for err in verify.arithmetic_errors(raw):
            problems.append(f"arithmetic error: {err}; recompute and correct every figure that depends on it (positioning, price logic)")

        # Reject uncheckable claims: 'first', 'only', 'growing demand'.
        claims = verify.superlative_claims(raw)
        if claims:
            problems.append("remove uncheckable claims (" + ", ".join(claims[:4]) + "); nothing in the evidence proves first/only/market-size statements")

        # A competitor whose location is not confirmed (cell starts with No) must be removed.
        for c in verify.rows_failing_location(raw):
            problems.append(f"{c} is listed although its Location confirmed is No; remove it from the report")

        # The Location cell must name the target city, not just a website.
        for c in (verify.rows_without_city(raw, city) if city else []):
            problems.append(f"{c}: Location confirmed must name {city} with its evidence; remove the competitor if it cannot be shown")

        # Online shops and product pages (packaged goods) are not meal-delivery competitors.
        for c in verify.retail_rows(raw):
            problems.append(f"{c} is sourced from an online shop or product page (packaged goods); remove it from the competitors")

        # A price per 250 g or per pack is packaged-goods pricing, not a meal price.
        for c in verify.non_meal_unit_prices(raw):
            problems.append(f"{c} is priced per weight or pack (a packaged product), not per meal; remove it from the competitors")

        # A customer complaint needs a real review link (a [n] citation is resolved through Sources).
        for c in verify.unsourced_complaints(raw):
            problems.append(f"section 2 complaint has no real source link ({c}); give the review page URL or write 'No specific complaints found'")

        # A page for another city (e.g. zomato.com/hyderabad/...) cannot support claims about the target city.
        for u in (verify.wrong_city_urls(raw, city) if city else []):
            problems.append(f"{u} is a page for a different city than {city}; reviews or prices from it do not apply, remove every claim that relies on it")

        # A generic social-media popular/search page is not a page about the competitor.
        for u in verify.generic_social_urls(raw):
            problems.append(f"{u} is a generic social-media page, not a page about the competitor; do not cite it")

        # Platform labels such as 'No seating available' are not customer feedback.
        tags = verify.listing_tag_complaints(raw.split("## 3")[0].split("## 2")[-1] if "## 2" in raw else "")
        if tags:
            problems.append("section 2 treats platform listing tags as customer feedback (" + ", ".join(tags) + "); these are not reviews, remove them")

        # If any competitor already has a monthly plan, section 3 must not claim that nobody offers one.
        plans = verify.monthly_plan_competitors(raw)
        if plans:
            sec3 = re.search(r"^##\s*3\..*?(?=^##\s*4\.)", raw, re.M | re.S)
            claim = sec3 and re.search(
                r"(none|no (direct |current |existing )?(competitor|competitors|service|services)|nobody|no one|absence|lack)[^.]{0,120}(subscription|monthly plan|monthly)",
                sec3.group(0), re.I)
            if claim:
                problems.append(
                    f"section 3 claims there is no subscription/monthly plan, but the table lists a monthly plan for {', '.join(plans)}; "
                    "rewrite the gap around what is genuinely missing (e.g. a different focus, price or convenience) and mention the existing plan"
                )
        if re.search(r"\bcustomers (have )?(reported|said|complain)", raw, re.I) and not re.search(r"reviews?", raw, re.I):
            problems.append("section 2 generalises to 'customers' without review counts")

        # Accept when clean, or when out of retries (then remember the leftover problems in the sink).
        if not problems or tries["n"] >= MAX_REPORT_TRIES:
            if sink is not None:
                sink["warnings"] = problems  # unresolved after the last retry
                sink["report"] = raw  # lets the optional pitch deck check its numbers against the report
            return True, raw  # run_crew performs the final hard validation
        return False, "Report rejected: " + "; ".join(problems) + ". Return the COMPLETE corrected report Markdown."

    return guardrail


def build_crew(
    idea: str,
    city: str,
    customer: str,
    known: str = "",
    step_callback: Optional[Callable] = None,
    task_callback: Optional[Callable] = None,
    include_deck: bool = False,
    hierarchical: bool = False,
) -> Crew:
    """Create the agents and tasks and assemble them into a Crew.

    Always: Market Researcher -> Review Analyst -> Strategy Analyst -> Report Writer, in a sequential process.
    include_deck adds a fifth Pitch Deck Designer agent; hierarchical replaces the fixed order with a Manager
    agent that delegates (experimental). step_callback / task_callback let the UI show live progress."""
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Shared dict: the report guardrail stores the final report here so the deck guardrail can compare numbers with it.
    report_sink: dict = {}
    llm = _llm()

    # Tools the agents can use: Google search (Serper), a web page scraper, a file writer and our own price finder.
    search = SerperDevTool()
    scrape = ScrapeWebsiteTool()
    writer_tool = FileWriterTool()
    price_finder = PriceFinderTool()

    # AGENT 1 - Market Researcher: finds competitors and prices. It gets the price_finder tool so prices are copied, not invented.
    researcher = Agent(
        role="Market Researcher",
        goal=f"Find 3 to 5 real competitors for '{idea}' in {city}, with prices, offers and sales channels, each with a source URL.",
        backstory=(
            "You are a meticulous market researcher who spent ten years doing local "
            "competitive intelligence for small businesses in India. You never invent "
            "data: if a price cannot be found you write 'not found' and move on."
        ),
        tools=[search, scrape, price_finder],
        llm=llm,
        verbose=True,
        max_iter=20,
    )

    # AGENT 2 - Review Analyst: finds what customers like and complain about, from third-party review pages only.
    review_analyst = Agent(
        role="Review Analyst",
        goal="Find what customers like and complain about for each competitor, backed by review pages.",
        backstory=(
            "You are a customer-insight analyst who reads Google Maps, Swiggy/Zomato, "
            "Reddit, Instagram and blog reviews to spot recurring patterns rather than "
            "one-off rants. You always cite where a complaint came from."
        ),
        tools=[search, scrape],
        llm=llm,
        verbose=True,
        max_iter=12,
    )

    # AGENT 3 - Strategy Analyst: no tools; reasons only from the research and reviews it is given.
    strategist = Agent(
        role="Strategy Analyst",
        goal="Turn the research and reviews into a comparison matrix, one clear market gap, and a priced positioning.",
        backstory=(
            "You are a startup strategist who has advised dozens of food and consumer "
            "launches. You reason strictly from the evidence handed to you and flag "
            "assumptions explicitly."
        ),
        tools=[],
        llm=llm,
        verbose=True,
    )

    # AGENT 4 - Report Writer: assembles the six-section report (it also has a file writer, but run_crew saves the final text itself).
    report_writer = Agent(
        role="Report Writer",
        goal="Write a clean, founder-friendly Markdown report with all sections and numbered sources, and save it to report.md.",
        backstory=(
            "You are a business writer who turns dense analysis into short, scannable "
            "reports non-experts can act on. You keep every claim traceable to a source."
        ),
        tools=[writer_tool],
        llm=llm,
        verbose=True,
    )

    # The same short brief is placed at the top of the first task so every run is driven only by the form inputs.
    known_txt = known.strip() or "none provided"
    brief = f"Business idea: {idea}\nCity: {city}\nTarget customer: {customer}\nKnown competitors: {known_txt}"

    # TASK 1 - research: the description is a list of strict rules (location, price, evidence, relevance, dates, contact)
    # that the guardrail then enforces; expected_output tells the agent the exact table format to produce.
    t_research = Task(
        description=(
            f"{brief}\n\nSearch the web and scrape pages to find 3 to 5 REAL competitors "
            f"operating in or delivering to {city}. Include the known competitors if any. "
            "STRICT RULES: "
            f"(1) LOCATION: only include a competitor if a page shows it operates in or delivers to {city} (or its immediate area); "
            "The 'Location confirmed' cell MUST read 'Yes, <area or address>, " + city + " (<where you saw it, e.g. Zomato address, Swiggy page, website contact page>)'; "
            "a bare source name such as 'website' or 'Swiggy listing' is not accepted. If you cannot show it, drop the competitor. "
            "ALSO EXCLUDE online shops and product pages (URLs containing /collections/, /products/, /dp/, amazon, flipkart): packaged goods are not competitors. "
            "ONLY include businesses that sell the same KIND of offering as the idea (e.g. prepared meals or a delivery/subscription service). "
            "EXCLUDE packaged retail or e-commerce products (instant mixes, flours, snacks, ingredients) and national brands that do not serve the city. "
            "Discard businesses from other cities or countries, and prices in other currencies (e.g. USD) are a sign to discard. "
            f"Use local searches such as '<idea> {city}', 'best <idea> in {city}', Justdial, Zomato, Swiggy, Google Maps and local news/blog lists to discover candidates. "
            "(2) PRICE: for each competitor you must get a price per unit and, if offered, a subscription/monthly price. "
            "If the first page has no price, run at least one more targeted search/scrape (menu, price list, order page, Swiggy/Zomato listing) before giving up. "
            "Always state the unit the price refers to (per meal, per bottle, per month) AND what it includes (e.g. lunch only vs breakfast+lunch+dinner, meals per day). Say whether it is a 'from/starting' price, and if another page of the same business shows a different price, report both. Prefer competitors that have a findable price; "
            f"You need at least {MIN_COMPETITORS} qualifying competitors; keep searching until you have them. "
            "if a candidate still has none, replace it with another candidate when possible. "
            "(3) Give the exact URL for every fact. Never fabricate; write 'not found' only after the extra search. "
            "(3b) RELEVANCE: for each competitor decide whether it actually sells the SAME product as the idea (e.g. millet-based meals) or is only an adjacent business (e.g. general diet/fitness meals); only mark 'Direct' if a page shows it. "
            "(3c) COMPARABLE PRICES: only report a price for a single unit of the product (one meal, one bottle, one month). Restaurant costs 'for two' / 'for a couple' and dine-in averages are NOT valid; "
            "prices from sources more than 2 years old are NOT valid. Write Not found instead. "
            "(4) DATES: note the publication/last-updated year of each price source when visible (page text, article date, URL). "
            "Mark any price from a source older than about 12 months as '(stale, <year>)' and unknown dates as '(date unknown)'. "
            "(4c) MONTHLY PLANS: put a figure in the Monthly plan column ONLY if price_finder returned a line showing it (it needs its own EVIDENCE line); otherwise write Not found. "
            "(4a) PRICE TOOL: for EVERY competitor you want to give a price for, call the price_finder tool with the competitor name, the exact page URL and a short hint of the product "
            "(e.g. 'millet lunch meal'). Only report prices that the tool returns; if it returns NO PRICE FOUND, try another URL for that competitor (menu, pricing, order page, Swiggy/Zomato listing) "
            "or write Not found. Never type a price from memory. "
            "(4b) EVIDENCE: for EVERY numeric price in the table, add a line directly below the table in exactly this format: "
            "EVIDENCE | <competitor name as in the table> | <exact URL you read the price on> | \"<short verbatim quote copied from that page that contains the price>\". "
            "Copy one EVIDENCE line returned by price_finder unchanged (the quote must stay verbatim); "
            "the system will automatically fetch each URL and reject prices whose quote is not found there. "
            "(5) CONTACT: for every competitor also capture how a customer can reach or order from them, taken only from pages you actually saw: "
            "website order/subscription page URL, phone number, WhatsApp number/link, Instagram/Facebook page, Swiggy/Zomato listing. Never guess a number."
        ),
        expected_output=(
            "A Markdown table with columns: Competitor | Direct or adjacent | Location confirmed | Price (unit, what is included, from/fixed, source year) | Monthly plan | Offers | Channel | Contact / order link | Source URL, "
            "with 3 to 5 rows, followed by a numbered list of every URL used, one EVIDENCE line per numeric price (format above), a Price conflicts line listing any competitor whose price differs "
            "between pages (or none), and a one-line note on any candidates discarded and why."
        ),
        agent=researcher,
        guardrail=make_competitor_guardrail(city, idea),
        guardrail_max_retries=MAX_RESEARCH_TRIES,
    )

    # TASK 2 - reviews: uses the research as context; every like/complaint needs a quote and the URL of that review page.
    t_reviews = Task(
        description=(
            f"Using the competitors found in the previous task, find REAL customer reviews for each in {city}. "
            "For every competitor run searches such as '<name> reviews', '<name> reddit', '<name> google maps reviews', "
            "'<name> zomato/swiggy reviews', then scrape the best review pages. "
            "STRICT RULES: (1) Review evidence must come from third-party pages (Google Maps, Zomato, Swiggy, JustDial, Reddit, "
            "Facebook/Instagram comments, blogs, news). The competitor's own website is NOT a valid source for complaints. "
            "(2) Every like/complaint must include a short verbatim or near-verbatim quote from the page and the exact URL of THAT review page. Also record the number of reviews and the overall rating shown for the competitor on that platform, including positive ratings, and state whether a complaint is a single review or recurring. "
            "(3) If you find no third-party reviews for a competitor, write 'No third-party reviews found' for it. "
            "Never infer or invent complaints."
        ),
        expected_output=(
            "For each competitor: 'Likes' and 'Complaints' bullets (up to 4 each), each formatted as "
            "'- <theme>: \"<quote>\" ([source](URL)) - <N reviews, X rating, single/recurring>', or the line 'No third-party reviews found'. "
            "End with 'Recurring complaints across competitors' listing only themes that appear for 2+ competitors "
            "(or state that evidence is too thin), and a final 'Evidence quality' line: strong, partial or weak."
        ),
        agent=review_analyst,
        context=[t_research],
    )

    # TASK 3 - strategy: comparison matrix, one market gap and a priced positioning derived from the VERIFIED prices only.
    t_strategy = Task(
        description=(
            f"Using the research and review findings, build a strategy for: {idea} in {city}, for {customer}. "
            "Produce a comparison matrix (price, channel, strength, weakness), identify the ONE biggest market gap "
            "nobody serves well, and propose a one-line positioning with a concrete price that fits the evidence. "
            "Only compare prices that cover the same scope; if scopes differ (e.g. 3 meals/day vs lunch only) normalise per meal or exclude and say so. Treat a 'from/starting' price as a lower bound and mention conflicting prices. Mark adjacent (non-direct) competitors as such, and state if no direct competitor exists. "
            "SUBSCRIPTION CHECK: competitors that already show a monthly plan are direct comparables for a subscription offer: never claim nobody offers a subscription, and compare your recommended per-meal or monthly price with their plan prices, saying plainly if you only match them. "
            "PRICE EVIDENCE: the research output ends with a VERIFICATION block. Use ONLY prices marked VERIFIED, VERIFIED-APPROX or VERIFIED-SNIPPET in the price logic; "
            "list UNVERIFIABLE, MISMATCH or UNSUPPORTED prices as excluded and say why. Compute the median properly (middle value of the sorted verified prices), and do not round the final price in a way you do not explain. "
            "RECONCILE: every statement in the market gap and positioning must be checkable against the matrix and review evidence. "
            "Before writing, check each claim (for example nobody offers a subscription, or nobody is millet-focused) against the research table; "
            "if any competitor contradicts it, narrow or drop the claim. Use ALL direct competitor prices in the price logic and show the range "
            "(lowest, highest, median) instead of picking one, and derive the recommended price from that range with a one-sentence rule. "
            "Convert every competitor price to a common per-month figure for a daily subscription where possible, showing the arithmetic (e.g. Rs 80 per bottle x 30 days = Rs 2,400 per month), and carry over any stale/date-unknown flags. "
            "The price must be derived from the sourced competitor prices: list the competitor prices you used (with units) "
            "and explain the logic (e.g. between the cheapest and median). If fewer than 2 comparable prices exist, say the price is "
            "an assumption to validate rather than presenting it as a finding. If any price used is stale or date-unknown, say so next to it. State any other assumptions."
        ),
        expected_output=(
            "Three parts: (1) comparison matrix as a Markdown table: Competitor | Price (with unit) | Channel | Strength | Weakness; "
            "(2) 'Market Gap' paragraph of at most 3 sentences; "
            "(3) 'Recommended Positioning': one quoted line including a price, then a 'Price logic' line showing the normalised per-month arithmetic, "
            "plus 1-2 sentences of justification."
        ),
        agent=strategist,
        context=[t_research, t_reviews],
    )

    # TASK 4 - report: writes the final six-section Markdown; its guardrail is the strictest of the project.
    t_report = Task(
        description=(
            f"Write the final founder-friendly report for '{idea}' in {city}. "
            f"Save it with the file writer tool: directory='{OUTPUT_DIR.as_posix()}', filename='{REPORT_NAME}', overwrite=True. "
            "Reuse the sourced facts from earlier tasks and add a practical week-by-week 30-day launch plan. "
            "In section 2 cite the exact review URL for each complaint (it must appear in Sources) and always include review count and rating; never describe a single review as 'customers'. "
            "PRICE TAGS: in the table price column append a tag taken from the VERIFICATION block: [verified], [verified via search snippet] or [unverifiable - confirm manually]; "
            "never present a MISMATCH or UNSUPPORTED price. Add one line under the table explaining the tags. "
            "CONSISTENCY PASS before you finish: (a) every claim in sections 3 and 4 must agree with the table in section 1; fix or remove any that do not; "
            "(b) add a Price conflicts line under the table carrying over the researcher conflicts (e.g. a business with two different prices on two pages); "
            "(c) in section 2, a complaint backed by one review must be written as one reviewer on <platform> said, with the platform rating and review count, never as customers; "
            "(d) every URL cited anywhere must appear in Sources. "
            "If the research output contains a NOTE about fewer than 3 competitors, say so plainly under section 1. "
            "Do not add facts that are not in earlier outputs. If the review evidence is weak, say so in section 2 "
            "instead of generalising."
        ),
        expected_output=(
            "A Markdown document titled '# Competitor Report: <idea>, <city>' with exactly these sections in order: "
            "'## 1. Key Competitors' (table with a source number per row, followed by a '### Prices to verify' subsection: "
            "for every competitor whose price is 'Not found' or stale, one bullet naming the competitor and the quickest way to get the real price, "
            "using the 'Contact / order link' values from the research (quote the actual phone, WhatsApp number or URL, never invent one), "
            "or 'No contact found, search their name on Instagram/Google Maps' if none was found; omit the subsection only if every price is current), '## 2. What Customers Complain About', "
            "'## 3. Market Gap', '## 4. Recommended Positioning', '## 5. 30-Day Launch Plan' (Week 1 to Week 4), "
            "'## 6. Sources' (numbered list of ONLY the URLs actually cited as [n] in sections 1-5; each table row must cite its number; "
            "put any other pages after a separate line 'Also consulted (not cited):'; exclude pages from discarded competitors). "
            "After saving the file, your final answer MUST be the complete report Markdown itself, not a confirmation message."
        ),
        agent=report_writer,
        guardrail=make_report_guardrail(report_sink, city, idea),
        guardrail_max_retries=MAX_REPORT_TRIES,
        context=[t_research, t_reviews, t_strategy],
    )

    # The order of these two lists is the order of execution; run_crew reads results by position (tasks[3] is the report).
    agents = [researcher, review_analyst, strategist, report_writer]
    tasks = [t_research, t_reviews, t_strategy, t_report]

    # Stretch goal: a fifth agent turns the finished report into exactly 6 slides without adding facts or numbers.
    if include_deck:
        deck_designer = Agent(
            role="Pitch Deck Designer",
            goal="Turn the finished report into a 6-slide pitch deck without adding any new facts or numbers.",
            backstory=(
                "You are a startup pitch coach who compresses long reports into crisp investor-ready slides. "
                "You only use figures that appear in the report and you keep every slide to a few short bullets."
            ),
            tools=[],
            llm=llm,
            verbose=True,
        )
        titles = "; ".join(f"Slide {i}: {t}" for i, t in enumerate(deck.SLIDE_TITLES, 1))
        t_deck = Task(
            description=(
                f"Create a 6-slide pitch deck for '{idea}' in {city} from the final report only. Slide titles in order: {titles}. "
                f"Each slide has at most {deck.MAX_BULLETS} short bullets (under 15 words each). Use only numbers, names and prices that appear in the report; "
                "do not present a price that the report marks unverifiable as a fact. "
                "Do not invent statistics, market sizes or quotes."
            ),
            expected_output=(
                "Markdown with exactly six sections, each starting with a line '## Slide N: <title>' (N from 1 to 6, titles as listed) "
                "followed by 3 to 5 bullet lines starting with '- '. No other text."
            ),
            agent=deck_designer,
            context=[t_report],
            guardrail=deck.make_deck_guardrail(report_sink),
            guardrail_max_retries=deck.MAX_DECK_TRIES,
        )
        agents.append(deck_designer)
        tasks.append(t_deck)

    # Stretch goal: a Manager agent delegates the tasks instead of the fixed sequence (experimental; research quality is lower).
    if hierarchical:
        manager = Agent(
            role="Project Manager",
            goal="Coordinate the research crew so each task is done by the right specialist and the final report meets every quality rule.",
            backstory=(
                "You are an experienced project lead. You delegate each task to the specialist best suited to it, "
                "check their output against the task requirements, and send work back when it falls short."
            ),
            llm=llm,
            allow_delegation=True,
            verbose=True,
        )
        return Crew(
            agents=agents,
            tasks=tasks,
            process=Process.hierarchical,
            manager_agent=manager,
            verbose=True,
            step_callback=step_callback,
            task_callback=task_callback,
        )

    # Default: the plain sequential process.
    return Crew(
        agents=agents,
        tasks=tasks,
        process=Process.sequential,
        verbose=True,
        step_callback=step_callback,
        task_callback=task_callback,
    )


def _standalone_deck(report_text: str, idea: str, city: str) -> str:
    """Build the deck with a single Pitch Deck Designer from the finished report (used when the crew's own deck task gave no slides)."""
    llm = _llm()

    # A dedicated Pitch Deck Designer agent used only for this repair step.
    designer = Agent(
        role="Pitch Deck Designer",
        goal="Turn the finished report into a 6-slide pitch deck without adding any new facts or numbers.",
        backstory="You are a startup pitch coach who compresses reports into crisp slides and only uses figures that appear in the report.",
        tools=[], llm=llm, verbose=True,
    )
    titles = "; ".join(f"Slide {i}: {t}" for i, t in enumerate(deck.SLIDE_TITLES, 1))

    # The report text is placed in the task description because there is no earlier task to read it from.
    task = Task(
        description=(
            f"Create a 6-slide pitch deck for '{idea}' in {city} from this report only. Slide titles in order: {titles}. "
            f"Each slide has at most {deck.MAX_BULLETS} short bullets (under 15 words each). Use only numbers, names and prices in the report; "
            "do not present a price the report marks unverifiable as a fact. Do not invent statistics or quotes." + chr(10) + chr(10) + "REPORT:" + chr(10) + report_text
        ),
        expected_output=(
            "Markdown with exactly six sections, each starting with a line '## Slide N: <title>' (N from 1 to 6, titles as listed) "
            "followed by 3 to 5 bullet lines starting with '- '. No other text."
        ),
        agent=designer,
        guardrail=deck.make_deck_guardrail({"report": report_text}),
        guardrail_max_retries=deck.MAX_DECK_TRIES,
    )

    # A crew of one agent and one task.
    c = Crew(agents=[designer], tasks=[task], process=Process.sequential, verbose=True)
    c.kickoff()
    return (getattr(task.output, "raw", None) or "").strip()


def check_search() -> None:
    """Fail fast if the Serper key is missing/invalid (otherwise agents invent data)."""
    try:
        SerperDevTool().run(search_query="test")
    except Exception as e:
        raise RuntimeError(f"Web search (Serper) is not working, check SERPER_API_KEY in .env: {e}")


# Every run is copied into its own timestamped folder under output/runs/ so nothing is lost when output/ is overwritten.
RUNS_DIR = OUTPUT_DIR / "runs"
RUN_FILES = ("report.md", "deck.md", "deck.pptx", "report_warnings.txt", "deck_warnings.txt", "run.log")


def _record_history(text: str, idea: str, city: str) -> None:
    """Save a price snapshot and write changes.md against the previous snapshot (if any)."""
    changes_path = OUTPUT_DIR / "changes.md"

    # Save today's prices as a snapshot, then look up the one before it.
    snap = history.save_snapshot(text, idea, city)

    # The previous run of the same idea and city (None on the first run).
    prev = history.previous_snapshot(idea, city, before=snap)
    if prev:
        changes_path.write_text(
            history.format_changes(prev, history.snapshot_from_report(text, idea, city)), encoding="utf-8"
        )
    else:
        changes_path.unlink(missing_ok=True)


def _archive_run(idea: str, city: str, result_score: dict, include_deck: bool, hierarchical: bool):
    """Copy this run's files to output/runs/<timestamp>/ with its score, and write output/score.json."""
    import datetime
    import json
    import shutil

    # The timestamp makes the folder name unique and sortable by date.
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    # One folder per run: output/runs/<idea>-<city>__<timestamp>/.
    folder = RUNS_DIR / f"{history.slug(idea, city)}__{stamp}"
    folder.mkdir(parents=True, exist_ok=True)

    # Copy this run's files (the deck files only if a deck was requested).
    for name in RUN_FILES:
        src = OUTPUT_DIR / name
        if src.exists() and (include_deck or not name.startswith("deck")):
            shutil.copy2(src, folder / name)

    # score.json holds the score and its breakdown plus the settings of the run.
    meta = {
        "idea": idea, "city": city, "timestamp": stamp, "hierarchical": hierarchical, "deck": include_deck,
        "score": result_score["score"], "breakdown": result_score["breakdown"],
    }
    (folder / "score.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "score.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return folder


def run_best_of(runs: int, idea, city, customer, known="", step_callback=None, task_callback=None,
                include_deck=False, hierarchical=False) -> str:
    """Run the crew `runs` times, keep the run with the highest quality score and make it the current output.

    Every run is archived under output/runs/. Only the winning run updates the price history. Ties go to the
    later run. A summary of all runs is written to output/best_of.md.
    """
    import json
    import shutil

    # Archive folder of every run that finished successfully.
    folders = []

    # Run the whole crew once per requested run.
    for _ in range(max(1, runs)):
        try:
            run_crew(idea, city, customer, known, step_callback, task_callback, include_deck, hierarchical, record_history=False)
        except Exception as e:  # a failed run counts as no result; keep going
            (OUTPUT_DIR / "best_of_errors.txt").open("a", encoding="utf-8").write(f"{type(e).__name__}: {e}{chr(10)}")
            continue
        folders.append(sorted(RUNS_DIR.glob(f"{history.slug(idea, city)}__*"))[-1])

    # Every run failed: nothing to choose from.
    if not folders:
        raise RuntimeError("All runs failed; see output/best_of_errors.txt")

    # Read each run's score.json back so the runs can be compared.
    metas = [(json.loads((f / "score.json").read_text(encoding="utf-8")), f) for f in folders]

    # Highest score wins; on a tie the later run wins (its index is the second sort key).
    best_meta, best_folder = max(enumerate(metas), key=lambda t: (t[1][0]["score"], t[0]))[1]
    for name in RUN_FILES:  # restore the winner as the current output
        src = best_folder / name
        if src.exists():
            shutil.copy2(src, OUTPUT_DIR / name)
        elif name in ("report_warnings.txt", "deck_warnings.txt"):
            (OUTPUT_DIR / name).unlink(missing_ok=True)
    (OUTPUT_DIR / "score.json").write_text(json.dumps(best_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # best_of.md: a small table listing every run, its score and which one was kept.
    lines = [f"# Best of {len(metas)} run(s)", "", "| Run | Score | Result |", "|---|---|---|"]
    for meta, f in metas:
        lines.append(f"| {meta['timestamp']} | {meta['score']}/100 | {'kept' if f == best_folder else 'archived'} |")
    (OUTPUT_DIR / "best_of.md").write_text(chr(10).join(lines) + chr(10), encoding="utf-8")

    text = (OUTPUT_DIR / REPORT_NAME).read_text(encoding="utf-8")
    _record_history(text, idea, city)
    return text


def run_crew(idea, city, customer, known="", step_callback=None, task_callback=None,
             include_deck=False, hierarchical=False, record_history=True) -> str:
    """Kick off the crew and return the report Markdown, saved to output/report.md.

    Extras (all optional, files go to output/):
      - include_deck: also writes deck.md and deck.pptx (6 slides built only from the report)
      - hierarchical: a Manager agent delegates the tasks instead of the fixed sequential order
    Always: appends every agent step to run.log, saves a price snapshot to output/history/ and, when an earlier
    snapshot exists for the same idea and city, writes the changes to output/changes.md.
    """

    # Fail fast if web search does not work; otherwise the agents would invent data without any sources.
    check_search()
    OUTPUT_DIR.mkdir(exist_ok=True)

    # run.log records every agent step (thoughts and tool calls) so a run can be audited afterwards.
    log_path = OUTPUT_DIR / "run.log"
    log_path.write_text("", encoding="utf-8")

    def logged_step(step):
        text = str(step)
        flag = "ERROR " if re.search(r"error|sorry, something went wrong|failed|unauthorized", text, re.I) else ""
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{flag}{text[:600].replace(chr(10), ' ')}{chr(10)}")
        if step_callback:
            step_callback(step)

    # Build the crew and run all tasks in order; kickoff() blocks until the last agent finishes.
    crew = build_crew(idea, city, customer, known, logged_step, task_callback, include_deck, hierarchical)
    result = crew.kickoff()

    # Helper: the raw text output of task number i (0 = research, 3 = report, 4 = deck).
    def task_text(i):
        out = getattr(crew.tasks[i], "output", None)
        return (getattr(out, "raw", None) or "").strip()

    # Helper: models sometimes wrap the whole answer in a code fence; strip it.
    def unfence(t):
        if t.startswith("```"):
            t = t.split(chr(10), 1)[1].rsplit("```", 1)[0].strip()
        return t

    # The report is the Report Writer's own answer (task 3), saved by us rather than trusting the agent's file write.
    text = unfence(task_text(3) or (getattr(result, "raw", None) or str(result)).strip())

    # Hard stop: a report without at least 3 real source URLs is refused rather than shown as if it were sourced.
    if len(re.findall(r"https?://[^\s)\]]+", text.split("## 6")[-1])) < 3:
        raise RuntimeError("Report has fewer than 3 real source URLs; refusing to present unsourced data.")
    if "## 6" not in text:
        raise RuntimeError("Report Writer did not produce a complete report.")
    (OUTPUT_DIR / REPORT_NAME).write_text(text, encoding="utf-8")

    # final hard check: guardrails give up after a few retries, so list anything still wrong
    # Final hard check of the finished report. The guardrails give up after a few retries, so every
    # check is run once more here; anything still wrong is written to report_warnings.txt and scored.
    unresolved = (
        [f"table is missing required column: {c}" for c in verify.missing_columns(text)]
        + [f"{c} is marked Direct without evidence it sells the product" for c in verify.direct_without_product(text, idea)]
        + [f"{c} is Direct but section 3 says it is not focused on the product" for c in verify.direct_called_unfocused(text, idea)]
        + [f"contact contradiction: {c}" for c in verify.contact_contradictions(text)]
        + [f"arithmetic: {e}" for e in verify.arithmetic_errors(text)]
        + [f"{c}: location not shown for {city}" for c in verify.rows_without_city(text, city)]
        + [f"{c}: sourced from an online shop / product page" for c in verify.retail_rows(text)]
        + [f"complaint without a real source link: {c}" for c in verify.unsourced_complaints(text)]
        + [f"generic social-media page cited: {u}" for u in verify.generic_social_urls(text)]
        + [f"page for a different city cited: {u}" for u in verify.wrong_city_urls(text, city)]
        + [f"listing tag used as a complaint: {t}" for t in verify.listing_tag_complaints(text.split("## 3")[0].split("## 2")[-1] if "## 2" in text else "")]
        + [f"uncheckable claim: {c}" for c in verify.superlative_claims(text)]
        + [f"{c}: price is per weight or pack, not a meal price" for c in verify.non_meal_unit_prices(text)]
        + [f"{c}: a platform summary sentence is shown as a customer quote" for c in verify.boilerplate_review_quotes(text)]
        + [f"market gap claims no dedicated product focus, but {c} is a Direct competitor" for c in verify.gap_contradicts_direct(text, idea)]
        + [f"{c} is listed but not confirmed in the target city" for c in verify.rows_failing_location(text)]
    )

    # Anything still wrong after the guardrail retries is saved to report_warnings.txt and shown in the app.
    warn_path = OUTPUT_DIR / "report_warnings.txt"
    if unresolved:
        warn_path.write_text(chr(10).join(unresolved), encoding="utf-8")
    else:
        warn_path.unlink(missing_ok=True)

    # Save a price snapshot and write changes.md (price moves since the previous run of the same idea and city).
    if record_history:
        _record_history(text, idea, city)

    # Problems found in the deck (stay empty when no deck was requested).
    deck_problems = []

    # stretch goal: pitch deck
    if include_deck:
        # The deck is the fifth task's output (index 4).
        deck_md = unfence(task_text(4))

        # In hierarchical mode the Manager's note can end up here instead of slides, so rebuild the deck from the report.
        if len(deck.parse_deck(deck_md)) != 6:  # e.g. hierarchical mode saved the Manager's delegation note instead of slides
            deck_md = unfence(_standalone_deck(text, idea, city)) or deck_md
        problems = deck.validate_deck(deck_md, text)
        deck_problems = problems
        (OUTPUT_DIR / "deck.md").write_text(deck_md, encoding="utf-8")
        warn = OUTPUT_DIR / "deck_warnings.txt"
        if problems:
            warn.write_text("; ".join(problems), encoding="utf-8")
        else:
            warn.unlink(missing_ok=True)

        # Write the PowerPoint file next to deck.md.
        deck.export_pptx(deck_md, OUTPUT_DIR / "deck.pptx")

    # Score the run from the automatic checks, then archive all of its files under output/runs/.
    result_score = scoring.score_run(text, idea, city, unresolved, deck_problems)

    # Copy this run's files to output/runs/ and write score.json.
    _archive_run(idea, city, result_score, include_deck, hierarchical)
    return text
