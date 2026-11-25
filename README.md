# LaunchScout: Competitor Research Crew

Four CrewAI agents (Market Researcher → Review Analyst → Strategy Analyst → Report Writer) run sequentially and produce `output/report.md`, shown in a Streamlit app with a live step log and Markdown/PDF download.

## Setup
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env     # add SERPER_API_KEY and an LLM key
streamlit run app.py
```
Works for any idea/city via the form; no code changes needed.

## Extras (stretch goals)
- **Pitch deck**: tick "Also create a 6-slide pitch deck" (or `--deck`). A Pitch Deck Designer agent turns the finished report into exactly 6 slides; a guardrail rejects decks with other slide counts, more than 5 bullets per slide, or numbers that are not in the report. Output: `output/deck.md` and `output/deck.pptx`.
- **Price tracking / weekly re-run**: every run saves a snapshot to `output/history/`. Re-running the same idea and city writes `output/changes.md` (price moves, new and dropped competitors) and shows it in the app tab "Changes since last run". For a weekly run:
  `.venv\Scripts\python.exe weekly_rerun.py --idea "..." --city "..." --customer "..." [--deck]` and schedule it with Windows Task Scheduler. Search results vary between runs, so a competitor appearing or disappearing is not always a market change.
- **Hierarchical mode (experimental)**: tick the box (or `--hierarchical`) to let a Manager agent delegate the tasks instead of the fixed sequential order. In testing it produced weaker research than the sequential process, so keep sequential as the default.

## Quality checks built in
Price quotes are verified against the cited pages (`verify.py`); the `price_finder` tool (`tools.py`) supplies the evidence; guardrails in `crew.py` retry research or report writing when competitors, evidence or consistency checks fail. Prices tagged "unverifiable" need manual confirmation.
