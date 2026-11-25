"""Price history: save a snapshot after every run and report what changed since the previous one."""
import datetime
import json
import re
from pathlib import Path

import verify

HISTORY_DIR = Path(__file__).parent / "output" / "history"


def slug(idea: str, city: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{idea} {city}".lower()).strip("-")[:70]


def _first_amount(cell: str):
    """First price figure in a table cell, as a float (₹/commas ignored); ranges use the lower bound."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", cell.replace("₹", " "))
    return float(m.group(0).replace(",", "")) if m else None


def _tag(cell: str) -> str:
    c = cell.lower()
    for t in ("verified via search snippet", "unverifiable", "verified"):
        if t in c:
            return t
    return ""


def snapshot_from_report(report: str, idea: str, city: str) -> dict:
    competitors = {}
    for header, cells in verify._table_rows(report):
        row = dict(zip([h.lower() for h in header], cells))
        name = cells[0]
        price_cell = next((v for k, v in row.items() if k.startswith("price")), "")
        competitors[name] = {
            "direct": next((v for k, v in row.items() if k.startswith("direct")), ""),
            "price_text": price_cell,
            "price": _first_amount(price_cell),
            "tag": _tag(price_cell),
        }
    return {"date": datetime.date.today().isoformat(), "idea": idea, "city": city, "competitors": competitors}


def save_snapshot(report: str, idea: str, city: str) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    snap = snapshot_from_report(report, idea, city)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = HISTORY_DIR / f"{slug(idea, city)}__{stamp}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def previous_snapshot(idea: str, city: str, before: Path = None):
    """The newest saved snapshot for this idea+city (excluding `before`, the one just saved)."""
    files = sorted(HISTORY_DIR.glob(f"{slug(idea, city)}__*.json")) if HISTORY_DIR.exists() else []
    files = [f for f in files if f != before]
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def _key(name: str) -> str:
    return verify.normalize(name)


def diff(prev: dict, new: dict) -> dict:
    p = {_key(k): (k, v) for k, v in prev["competitors"].items()}
    n = {_key(k): (k, v) for k, v in new["competitors"].items()}
    changes = {"price_changes": [], "new": [], "dropped": [], "now_found": [], "no_longer_found": []}
    for k, (name, v) in n.items():
        if k not in p:
            changes["new"].append((name, v["price_text"]))
            continue
        old = p[k][1]
        if old["price"] is not None and v["price"] is not None and old["price"] != v["price"]:
            pct = (v["price"] - old["price"]) / old["price"] * 100
            changes["price_changes"].append((name, old["price"], v["price"], pct))
        elif old["price"] is None and v["price"] is not None:
            changes["now_found"].append((name, v["price"]))
        elif old["price"] is not None and v["price"] is None:
            changes["no_longer_found"].append((name, old["price"]))
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
    lines.append("_Prices are compared only when both runs found a figure; a different competitor set can also reflect search variation, not a market change._")
    return "\n".join(lines) + "\n"
