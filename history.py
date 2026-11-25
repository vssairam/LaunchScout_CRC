"""Price history: save a snapshot after every run and report what changed since the previous one.

A "snapshot" is a small JSON file (output/history/<idea>-<city>__<timestamp>.json) holding, for every competitor
in the report table, its price text, the first numeric price and its verification tag. Comparing the newest
snapshot with the one before it gives the "Changes since last run" tab and output/changes.md, which is the
weekly price-tracking stretch goal.
"""
import datetime
import json
import re
from pathlib import Path

import verify

# Snapshots live next to the other run outputs.
HISTORY_DIR = Path(__file__).parent / "output" / "history"


def slug(idea: str, city: str) -> str:
    """File-name-safe key for an idea + city pair, e.g. 'healthy-millet-meal-boxes-delivered-for-lunch-coimbatore'.

    Snapshots (and archived runs) are grouped by this key, so the same idea and city are always compared with each other.
    """
    return re.sub(r"[^a-z0-9]+", "-", f"{idea} {city}".lower()).strip("-")[:70]


def _first_amount(cell: str):
    """First price figure in a table cell, as a float (₹/commas ignored); ranges use the lower bound."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", cell.replace("₹", " "))
    return float(m.group(0).replace(",", "")) if m else None


def _tag(cell: str) -> str:
    """The verification tag in a price cell: 'verified via search snippet', 'unverifiable', 'verified' or ''."""
    c = cell.lower()

    # Order matters: the longer phrase must be tested before the plain word 'verified' that it contains.
    for t in ("verified via search snippet", "unverifiable", "verified"):
        if t in c:
            return t
    return ""


def snapshot_from_report(report: str, idea: str, city: str) -> dict:
    """Build a snapshot dictionary from the Markdown report's competitor table."""

    # name -> {direct, price_text, price, tag} for every row of the competitor table.
    competitors = {}
    for header, cells in verify._table_rows(report):
        # Pair each header with its cell so we can find columns by name instead of by position.
        row = dict(zip([h.lower() for h in header], cells))
        name = cells[0]

        # The price column's header starts with "price" ("Price (unit, what is included, ...)").
        price_cell = next((v for k, v in row.items() if k.startswith("price")), "")
        competitors[name] = {
            "direct": next((v for k, v in row.items() if k.startswith("direct")), ""),
            "price_text": price_cell,
            "price": _first_amount(price_cell),
            "tag": _tag(price_cell),
        }
    return {"date": datetime.date.today().isoformat(), "idea": idea, "city": city, "competitors": competitors}


def save_snapshot(report: str, idea: str, city: str) -> Path:
    """Write a snapshot of this report to output/history/ and return the file path."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    snap = snapshot_from_report(report, idea, city)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")   # timestamp keeps names unique and sortable
    path = HISTORY_DIR / f"{slug(idea, city)}__{stamp}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def previous_snapshot(idea: str, city: str, before: Path = None):
    """The newest saved snapshot for this idea+city (excluding `before`, the one just saved)."""

    # File names sort chronologically because they end with a YYYYMMDD-HHMMSS timestamp.
    files = sorted(HISTORY_DIR.glob(f"{slug(idea, city)}__*.json")) if HISTORY_DIR.exists() else []
    files = [f for f in files if f != before]
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def _key(name: str) -> str:
    """Normalised competitor name, so 'Millet Life' and 'millet-life' count as the same competitor."""
    return verify.normalize(name)


def diff(prev: dict, new: dict) -> dict:
    """Compare two snapshots and group the differences.

    Returns a dict with lists: price_changes, new, dropped, now_found, no_longer_found.
    """
    p = {_key(k): (k, v) for k, v in prev["competitors"].items()}
    n = {_key(k): (k, v) for k, v in new["competitors"].items()}

    # Five groups of differences: price moved, new, dropped, price now found, price no longer found.
    changes = {"price_changes": [], "new": [], "dropped": [], "now_found": [], "no_longer_found": []}
    for k, (name, v) in n.items():
        if k not in p:
            # In the new run but not the old one.
            changes["new"].append((name, v["price_text"]))
            continue
        old = p[k][1]
        if old["price"] is not None and v["price"] is not None and old["price"] != v["price"]:
            # Both runs found a price and the number moved: record old, new and the percentage change.
            pct = (v["price"] - old["price"]) / old["price"] * 100
            changes["price_changes"].append((name, old["price"], v["price"], pct))
        elif old["price"] is None and v["price"] is not None:
            changes["now_found"].append((name, v["price"]))
        elif old["price"] is not None and v["price"] is None:
            changes["no_longer_found"].append((name, old["price"]))

    # In the old run but missing from the new one.
    for k, (name, v) in p.items():
        if k not in n:
            changes["dropped"].append((name, v["price_text"]))
    return changes


def format_changes(prev: dict, new: dict) -> str:
    """Markdown summary of what changed between two snapshots."""
    c = diff(prev, new)
    lines = [f"## Changes since {prev['date']}", ""]
    if not any(c.values()):
        return "\n".join(lines + ["No price or competitor changes detected.", ""])
    for name, old, cur, pct in c["price_changes"]:
        arrow = "UP" if pct > 0 else "DOWN"
        lines.append(f"- **{name}**: price {arrow} from {old:,.0f} to {cur:,.0f} ({pct:+.0f}%)")
    for name, price in c["now_found"]:
        lines.append(f"- **{name}**: price now found ({price:,.0f})")
    for name, price in c["no_longer_found"]:
        lines.append(f"- **{name}**: price no longer found (was {price:,.0f})")
    for name, text in c["new"]:
        lines.append(f"- **New competitor**: {name} ({text or 'no price found'})")
    for name, text in c["dropped"]:
        lines.append(f"- **Not found this time**: {name} (was {text or 'no price'})")
    lines.append("")

    # Honest caveat: search results differ from run to run, so a change is not always real.
    lines.append("_Prices are compared only when both runs found a figure; a different competitor set can also reflect search variation, not a market change._")
    return "\n".join(lines) + "\n"
