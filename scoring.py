"""Quality score for one run, built only from the automatic checks (no extra LLM calls).

The score is a transparent heuristic for choosing between runs, not a measure of truth: it cannot tell whether a
price on a page is the right price, only whether the report passes the structural and consistency checks.

How it works: start at 100 and subtract points for every problem found. Each deduction is recorded together with
its reason, so the "Quality score" tab in the app can show exactly why a run lost points. The score is used by
`crew.run_best_of` to pick the best of several runs and is saved to output/score.json after every run.
"""
import re

import verify


def _priced_rows(report: str):
    """(competitor, price cell) for every row whose price cell has a number."""
    return list(verify.table_prices(report).items())


def score_run(report: str, idea: str, city: str, report_warnings: list, deck_warnings: list) -> dict:
    """Return {'score': 0-100, 'breakdown': [(points, reason), ...]}.

    report_warnings / deck_warnings are the problems the guardrails could NOT fix after their retries
    (written to report_warnings.txt / deck_warnings.txt by crew.run_crew).
    """
    parts = []  # list of (points, reason); points are negative

    def add(points: int, reason: str):
        """Record one deduction."""
        parts.append((points, reason))

    # --- unresolved problems the guardrails could not fix ---
    # The first three each cost 15 points; any further ones are lumped into a single 15-point line so a very
    # broken report is not penalised without limit (the final score is clamped to 0 anyway).
    for w in report_warnings[:3]:
        add(-15, f"unresolved report problem: {w[:90]}")
    if len(report_warnings) > 3:
        add(-15, f"{len(report_warnings) - 3} more unresolved report problem(s)")
    for w in deck_warnings[:2]:
        add(-10, f"deck problem: {w[:90]}")

    # --- coverage: counted from the table itself ---
    # (sources may be cited as [1], [2] with the URLs listed in section 6, so we do not count "http" in the rows)
    table_rows = list(verify._table_rows(report))
    if len(table_rows) < 3:
        add(-20, f"only {len(table_rows)} competitor row(s) in the table (need 3)")

    # A row is "Direct" when its cell under a "Direct or adjacent" header starts with the word Direct.
    direct = [c for h, c in table_rows if any(x.lower().startswith("direct") and i < len(c) and re.match(r"\W*direct", c[i], re.I) for i, x in enumerate(h))]
    if len(direct) < 2:
        add(-15, f"only {len(direct)} Direct competitor(s) (need 2)")

    # Section 6 must list at least 3 different source URLs.
    sources = report.split("## 6")[-1] if "## 6" in report else ""
    if len(set(re.findall(r"https?://[^\s)\]]+", sources))) < 3:
        add(-15, "fewer than 3 distinct source URLs in the Sources section")

    # --- prices and their verification ---
    priced = _priced_rows(report)
    if len(priced) < 2:
        add(-15, f"only {len(priced)} competitor(s) with a numeric price (need 2)")
    for name, cell in priced:
        if "unverifiable" in cell.lower():
            add(-5, f"{name}: price is unverifiable")

    # Pull the first number out of each price cell so we can compare prices with each other.
    amounts = {}
    for name, cell in priced:
        m = re.search(r"\d[\d,]*(?:\.\d+)?", cell.replace("₹", " "))

        # Monthly-plan prices are a different unit (per month) and would distort the per-meal comparison.
        if m and not name.endswith("[monthly plan]"):
            amounts[name] = float(m.group(0).replace(",", ""))
    if len(amounts) >= 3:
        # A price far below the highest one is usually a side item or snack, not a meal.
        top = max(amounts.values())
        for name, val in amounts.items():
            if val < 0.4 * top:
                add(-10, f"{name}: price {val:g} is under 40% of the highest competitor price ({top:g}); possibly a side item or snack, not a meal")

    # Prices per 250g / per pack are packaged-goods prices, not meal prices.
    for name in verify.non_meal_unit_prices(report):
        add(-10, f"{name}: price is per weight/pack, not a meal or unit price")

    # --- review evidence ---
    # A platform's own summary sentence ("holds an average rating of 4.2") is not a customer quote.
    for name in verify.boilerplate_review_quotes(report):
        add(-10, f"{name}: platform summary sentence presented as a customer quote")

    # --- gap logic ---
    # Section 3 must not claim "nobody focuses on <product>" when the table lists a Direct competitor that does.
    contradicted = verify.gap_contradicts_direct(report, idea)
    if contradicted:
        add(-20, "market gap claims no dedicated product focus, but " + ", ".join(contradicted) + " is a Direct competitor")

    # Clamp to 0..100 so the score always reads as a percentage.
    score = max(0, min(100, 100 + sum(p for p, _ in parts)))
    return {"score": score, "breakdown": parts}


def format_score(result: dict) -> str:
    """Render a score result as Markdown for the app's "Quality score" tab."""
    lines = [f"**Quality score: {result['score']}/100** (automatic checks only; not a measure of whether prices are right)", ""]
    if not result["breakdown"]:
        lines.append("- No deductions.")
    for points, reason in result["breakdown"]:
        lines.append(f"- {points:+d}: {reason}")  # {points:+d} always shows the sign, e.g. -15
    return "\n".join(lines)
