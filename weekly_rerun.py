"""Re-run LaunchScout for the same idea and city and print what changed since the last run.

Example (run from the project folder):
    .venv\\Scripts\\python.exe weekly_rerun.py --idea "Healthy millet meal boxes delivered for lunch" ^
        --city Coimbatore --customer "Office workers, age 25 to 40" --deck

Each run saves a price snapshot to output/history/ and writes output/changes.md (price moves, new and
dropped competitors) when an earlier run exists. To run it weekly on Windows, create a Task Scheduler
task (trigger: weekly) whose action starts .venv\\Scripts\\python.exe with this script and the arguments above.

Why a separate script: the Streamlit app needs someone to click "Run". This command-line entry point calls
the same functions (run_crew / run_best_of in crew.py) without a browser, so it can be scheduled and its
output (report, deck, score, changes) lands in the same output/ folder the app reads.
"""
import argparse
import sys
from pathlib import Path

# OUTPUT_DIR is where every run writes its files; run_crew runs the four-agent crew once;
# run_best_of runs it N times and keeps the highest-scoring result.
from crew import OUTPUT_DIR, run_best_of, run_crew


def main() -> int:
    """Parse the command line, run the crew (once or best-of-N) and print the score and the changes."""

    # RawDescriptionHelpFormatter keeps the example above readable in `--help`.
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    # The three required inputs mirror the three required fields of the Streamlit form.
    ap.add_argument("--idea", required=True)
    ap.add_argument("--city", required=True)
    ap.add_argument("--customer", required=True)

    # Optional inputs and switches for the extras (stretch goals).
    ap.add_argument("--known", default="", help="optional known competitors")
    ap.add_argument("--deck", action="store_true", help="also build the 6-slide pitch deck")
    ap.add_argument("--runs", type=int, default=1, help="run N times and keep the best-scoring run (N times the cost)")
    ap.add_argument("--hierarchical", action="store_true", help="use the Manager agent (experimental)")
    a = ap.parse_args()

    if a.runs > 1:
        # Best-of-N: each run is archived, the winner becomes the current output and best_of.md lists all scores.
        run_best_of(a.runs, a.idea, a.city, a.customer, a.known, include_deck=a.deck, hierarchical=a.hierarchical)
        print((OUTPUT_DIR / "best_of.md").read_text(encoding="utf-8"))
    else:
        # Single run: writes report.md (and deck files when --deck is given), updates history and score.json.
        run_crew(a.idea, a.city, a.customer, a.known, include_deck=a.deck, hierarchical=a.hierarchical)

    # score.json is written by crew._archive_run after every run.
    score = OUTPUT_DIR / "score.json"
    if score.exists():
        import json
        print("Quality score:", json.loads(score.read_text(encoding="utf-8"))["score"], "/ 100")
    print(f"\nReport: {OUTPUT_DIR / 'report.md'}")

    # changes.md exists only when an earlier snapshot of the same idea and city was found (see history.py).
    changes = Path(OUTPUT_DIR / "changes.md")
    if changes.exists():
        print("\n" + changes.read_text(encoding="utf-8"))
    else:
        print("\nFirst run for this idea and city: nothing to compare yet. Run again later to see price changes.")
    return 0


if __name__ == "__main__":
    # The return value becomes the process exit code (0 = success), which Task Scheduler can read.
    sys.exit(main())
