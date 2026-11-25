"""Re-run LaunchScout for the same idea and city and print what changed since the last run.

Example (run from the project folder):
    .venv\\Scripts\\python.exe weekly_rerun.py --idea "Healthy millet meal boxes delivered for lunch" ^
        --city Coimbatore --customer "Office workers, age 25 to 40" --deck

Each run saves a price snapshot to output/history/ and writes output/changes.md (price moves, new and
dropped competitors) when an earlier run exists. To run it weekly on Windows, create a Task Scheduler
task (trigger: weekly) whose action starts .venv\\Scripts\\python.exe with this script and the arguments above.
"""
import argparse
import sys
from pathlib import Path

from crew import OUTPUT_DIR, run_best_of, run_crew


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--idea", required=True)
    ap.add_argument("--city", required=True)
    ap.add_argument("--customer", required=True)
    ap.add_argument("--known", default="", help="optional known competitors")
    ap.add_argument("--deck", action="store_true", help="also build the 6-slide pitch deck")
    ap.add_argument("--runs", type=int, default=1, help="run N times and keep the best-scoring run (N times the cost)")
    ap.add_argument("--hierarchical", action="store_true", help="use the Manager agent (experimental)")
    a = ap.parse_args()

    if a.runs > 1:
        run_best_of(a.runs, a.idea, a.city, a.customer, a.known, include_deck=a.deck, hierarchical=a.hierarchical)
        print((OUTPUT_DIR / "best_of.md").read_text(encoding="utf-8"))
    else:
        run_crew(a.idea, a.city, a.customer, a.known, include_deck=a.deck, hierarchical=a.hierarchical)
    score = OUTPUT_DIR / "score.json"
    if score.exists():
        import json
        print("Quality score:", json.loads(score.read_text(encoding="utf-8"))["score"], "/ 100")
    print(f"\nReport: {OUTPUT_DIR / 'report.md'}")
    changes = Path(OUTPUT_DIR / "changes.md")
    if changes.exists():
        print("\n" + changes.read_text(encoding="utf-8"))
    else:
        print("\nFirst run for this idea and city: nothing to compare yet. Run again later to see price changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
