# LaunchScout: Competitor Research Crew

LaunchScout is a team of AI agents that researches the market for a new business idea in a given city and delivers a
founder-friendly competitor report: who the competitors are, what they charge, what their customers complain about,
the one gap worth targeting, a recommended price and a 30-day launch plan. Every claim in the report is tied to a
source link, and the prices are checked automatically against the pages they came from.

It works for **any idea and any city** through a form in a web app. No code changes are needed.

> **Solution guide with diagrams:** [`ARTEFACTS.html`](ARTEFACTS.html). When GitHub Pages is on, it is published at
> https://vssairam.github.io/LaunchScout_CRC/
>
> Sample run used while building it: *"Healthy millet meal boxes delivered for lunch"* in *Coimbatore*, for
> *"Office workers, age 25 to 40"*. A sample result is in `output/report.md`, `output/deck.md` and `output/deck.pptx`.

---

## Contents
1. [How it works](#how-it-works)
2. [The agents](#the-agents)
3. [The report](#the-report)
4. [Setup](#setup)
5. [Using the app](#using-the-app)
6. [Running from the command line (weekly re-runs)](#running-from-the-command-line-weekly-re-runs)
7. [Extras (stretch goals)](#extras-stretch-goals)
8. [Quality checks built in](#quality-checks-built-in)
9. [The quality score](#the-quality-score)
10. [Project structure](#project-structure)
11. [Output files](#output-files)
12. [Known limits](#known-limits)
13. [Troubleshooting](#troubleshooting)

---

## How it works

```
 form (idea, city, customer)
          |
          v
 [1] Market Researcher  --->  competitor table: prices, offers, channel, contact, source URL, EVIDENCE quotes
          |                     (guardrail: >= 3 competitors, >= 2 Direct, location shown, prices verified)
          v
 [2] Review Analyst     --->  what customers like and complain about, from third-party review pages
          |
          v
 [3] Strategy Analyst   --->  comparison matrix, one market gap, priced positioning
          |
          v
 [4] Report Writer      --->  six-section Markdown report   (guardrail: consistency and sourcing checks)
          |
          v
 [5] Pitch Deck Designer (optional) ---> 6 slides built only from the report
          |
          v
 output/report.md  (+ deck.md, deck.pptx, score.json, changes.md, run.log)
```

The agents run **one after another** (CrewAI sequential process). Each agent receives the previous agents' output.
After the agents finish, the program saves the report, scores the run, archives it and records a price snapshot so
the next run can be compared with this one.

The important design idea is that **language models are not trusted with facts**. Instead of asking the model to
"find a price", the researcher uses a `price_finder` tool that returns real quotes from the competitor's page; the
program then re-checks each quote against the page and sends the agent back to try again if a price cannot be
confirmed.

## The agents

| # | Agent | What it does | Tools |
|---|-------|--------------|-------|
| 1 | **Market Researcher** | Finds 3 to 5 real competitors in the city with price, what the price includes, offers, sales channel, contact and source URL. | Serper web search, web page scraper, `price_finder` |
| 2 | **Review Analyst** | Finds what customers like and complain about for each competitor, with a quote, the review page link, review count and rating. | Serper web search, web page scraper |
| 3 | **Strategy Analyst** | Builds the comparison matrix, picks the one biggest market gap, and proposes a positioning with a price derived from the verified competitor prices. | none (reasons only from the evidence it is given) |
| 4 | **Report Writer** | Writes the final six-section report with numbered sources and a week-by-week launch plan. | file writer |
| 5 | **Pitch Deck Designer** *(optional)* | Turns the finished report into exactly 6 slides without adding new facts or numbers. | none |
| - | **Project Manager** *(optional, hierarchical mode)* | Delegates the tasks instead of the fixed order. Experimental. | delegation |

## The report

`output/report.md` always has these sections:

1. **Key Competitors** - a table (competitor, Direct or adjacent, location confirmed, price with unit and what is included,
   monthly plan, offers, channel, contact / order link, source), a "Prices to verify" list and a "Price conflicts" line.
2. **What Customers Complain About** - complaints with quote, review count and rating, each linked to its review page.
3. **Market Gap** - the one opportunity, worded as "none found in the sources reviewed" rather than as a fact about the market.
4. **Recommended Positioning** - a one-line positioning with a price and the logic that produced it.
5. **30-Day Launch Plan** - week-by-week steps.
6. **Sources** - numbered list of every URL cited.

Price tags in the table tell you how much to trust a price: `[verified]` (quote found on the page),
`[verified via search snippet]` (the page needs JavaScript, so a Google snippet of that page was used) and
`[unverifiable - confirm manually]`.

---

## Setup

Requirements: Python 3.10 or newer (developed and tested on 3.13), a [Serper](https://serper.dev) API key (web search) and an
OpenAI API key (or another LLM provider, see below).

```
python -m venv .venv
.venv\Scripts\activate            # PowerShell may block this, see Troubleshooting
pip install -r requirements.txt
copy .env.example .env            # then open .env and fill in your keys
```

`.env` settings:

| Variable | Meaning |
|----------|---------|
| `SERPER_API_KEY` | Key for Serper (Google search results). Required. |
| `OPENAI_API_KEY` | Key for the language model. Required for `openai/...` models. |
| `LLM_MODEL` | Any LiteLLM model string, for example `openai/gpt-4o` (used in testing) or `openai/gpt-4o-mini`. To use another provider (for example `anthropic/claude-sonnet-4-5`) set that provider's key instead. |

`.env` is private and is excluded from git by `.gitignore`. Never commit it.

## Using the app

```
.venv\Scripts\streamlit.exe run app.py
```

Open the address Streamlit prints (normally http://localhost:8501).

1. Fill in the **business idea**, **city** and **target customer**. *Known competitors* is optional.
2. Optional: tick **Also create a 6-slide pitch deck**, set **Runs** (1 to 3: runs the crew several times and keeps the
   best-scoring one, at the cost of several runs), or tick **Hierarchical mode** (experimental).
3. Press **Run research crew**. A row of badges shows which agent is working now (waiting, working, finished) and a log
   shows each step. A run usually takes a few minutes and uses your Serper and OpenAI credits.
4. When it finishes, the results appear in tabs:
   - **Report** - the report, any quality problems the crew could not fix, and **Download Markdown / PDF** buttons.
   - **Changes since last run** - price moves, new and dropped competitors compared with the previous run of the same idea and city.
   - **Quality score** - the score and exactly why points were deducted.
   - **Pitch deck** - the slides and a **Download PowerPoint** button (only when the deck was requested).

When the page is opened without running anything, it shows the last saved run from the `output/` folder, so the
downloads work without spending credits.

## Running from the command line (weekly re-runs)

`weekly_rerun.py` does the same work without a browser, so it can be scheduled:

```
.venv\Scripts\python.exe weekly_rerun.py --idea "Healthy millet meal boxes delivered for lunch" ^
    --city Coimbatore --customer "Office workers, age 25 to 40" --deck
```

Options: `--known "A, B"`, `--deck`, `--runs N` (best of N), `--hierarchical`. It prints the quality score, the report
location and the changes since the previous run.

To run it every week on Windows, create a **Task Scheduler** task with a weekly trigger whose action starts
`.venv\Scripts\python.exe` with `weekly_rerun.py` and the arguments above (set "Start in" to the project folder).

---

## Extras (stretch goals)

- **Pitch deck** - an extra Pitch Deck Designer agent turns the finished report into exactly 6 slides
  ("The opportunity", "Who we compete with", "What customers say", "The market gap", "Our positioning and price",
  "30-day launch plan"). A guardrail rejects decks with a wrong slide count, more than 5 bullets on a slide, numbers
  that are not in the report, uncheckable claims or absence stated as fact. If the deck still has no slides (for example in
  hierarchical mode) it is rebuilt from the finished report. Output: `output/deck.md` and `output/deck.pptx`.
- **Price tracking / weekly re-run** - every run saves a snapshot to `output/history/`. Running the same idea and city
  again writes `output/changes.md` (price moves, new and dropped competitors), shown in the app tab
  "Changes since last run". Search results vary between runs, so a competitor appearing or disappearing is not
  always a real market change.
- **Hierarchical mode (experimental)** - a Manager agent delegates the tasks instead of the fixed order. In testing it
  produced weaker research than the sequential process, so sequential is the default.
- **Best of N runs** - runs the crew up to three times, scores each run and keeps the best one. All runs stay archived.

## Quality checks built in

AI models invent plausible-looking facts, so LaunchScout checks its own output. The checks live in `verify.py` and are
applied by the guardrails in `crew.py`, which send an agent back to retry with specific feedback when a check fails.

**On the research (agent 1):**
- at least 3 competitors with a source URL and at least 2 marked Direct;
- every competitor's location is shown and names the target city;
- no online shops or product pages (packaged goods are not meal-delivery competitors);
- no prices "for two" or from sources more than two years old;
- every numeric price needs an `EVIDENCE | competitor | url | "quote"` line; the quote is checked against the live page
  (or Google snippets for JavaScript pages) and prices that do not match are rejected;
- subscriptions or monthly plans that the evidence mentions must be filled in the "Monthly plan" column.

**On the report (agent 4):**
- all six sections present, every cited URL listed in Sources, table columns intact;
- arithmetic such as "219.50 x 30 = 6,570" is recomputed;
- no uncheckable claims ("first", "only", "growing demand"), and absence ("no one offers a subscription") must be
  worded as "none found in the sources reviewed";
- a Direct competitor must really sell the idea's product, and the market-gap section may not contradict the table;
- complaints need a real source link; generic social-media search pages and pages for a different city are rejected;
- packaged-goods prices (per 250 g, per pack) are rejected as meal prices.

If a guardrail cannot get the agent to fix a problem after three tries, the run still completes, but the remaining
problems are saved to `output/report_warnings.txt`, shown in the app above the report and deducted from the score.

## The quality score

`scoring.py` gives every run a score from 0 to 100 using only the automatic checks above (no extra AI calls). It starts
at 100 and deducts points, for example -15 for each unresolved report problem, -20 for fewer than 3 competitors,
-15 for fewer than 2 Direct competitors or fewer than 3 distinct source URLs, -5 per unverifiable price and -10 for
a pack price shown as a meal price. The "Quality score" tab lists every deduction. The score is a transparent way to
compare runs, **not proof that the prices are right**. A person should still check the prices before relying on them.

---

## Project structure

| File | Purpose |
|------|---------|
| `app.py` | Streamlit web app: form, live agent badges and log, result tabs, Markdown/PDF/PowerPoint downloads. |
| `crew.py` | The agents, tasks and guardrails; `run_crew` (one run), `run_best_of` (best of N) and saving/archiving of every run. |
| `tools.py` | `PriceFinderTool`: fetches a competitor page (or Google snippets of it) and returns real price quotes as EVIDENCE lines. |
| `verify.py` | Fact checks: price-quote verification against pages, plus all the report-quality checks used by the guardrails. |
| `scoring.py` | The 0-100 quality score and its breakdown. |
| `deck.py` | Pitch deck parsing, guardrail, validation and PowerPoint export. |
| `history.py` | Price snapshots and the "changes since last run" comparison. |
| `weekly_rerun.py` | Command-line entry point for scheduled re-runs. |
| `requirements.txt` | Python packages. |
| `.env.example` | Template for `.env`. |
| `LaunchScout_CRC.md` | The original assignment brief. |

## Output files

Everything is written to `output/` (only the final sample report and deck are kept in git):

| File / folder | Content |
|---------------|---------|
| `report.md` | The final report. |
| `deck.md`, `deck.pptx` | The pitch deck (when requested). |
| `report_warnings.txt`, `deck_warnings.txt` | Problems the guardrails could not fix (absent when everything passed). |
| `score.json` | The quality score and its breakdown. |
| `changes.md` | Changes since the previous run of the same idea and city. |
| `best_of.md` | Table of all runs when "Runs" was more than 1. |
| `run.log` | Every agent step of the last run, for auditing. |
| `history/` | One price snapshot per run. |
| `runs/` | A full copy of every run in its own timestamped folder. |

## Known limits

- **Search results vary.** Google returns different pages from run to run, so the competitor list, the reviews and even
  the number of competitors found can change between runs. Thinly covered local markets can end up with fewer than 3
  competitors; the report then says so.
- **The checks look at structure and evidence, not relevance.** They cannot know whether a review is truly about the
  right outlet or whether a price is the current one. Read the report before sharing it.
- **JavaScript-only sites** (Swiggy, Zomato) cannot be read directly, so their prices are confirmed from search
  snippets and tagged `[verified via search snippet]`. Snippets can be out of date or show a different menu item.
- **Hierarchical mode** is experimental and produces weaker research.
- **PDF export** replaces the rupee sign with "Rs " because the built-in PDF font has no rupee glyph.
- Each run costs Serper search credits and OpenAI tokens.

## Troubleshooting

- **"Web search (Serper) is not working ... Not enough credits"** - the Serper account has no credits left. Top up at
  serper.dev (credits belong to the account, so a new key for the same account does not help).
- **"... 403 / invalid key"** - check `SERPER_API_KEY` in `.env`: no quotes, no spaces, and the file is named `.env`
  (not `.env.example`).
- **PowerShell will not activate the virtual environment** (execution policy) - skip activation and call the programs
  directly: `.venv\Scripts\streamlit.exe run app.py` and `.venv\Scripts\python.exe weekly_rerun.py ...`.
- **The app shows the last saved run, not a blank page** - that is intentional; press **Run research crew** for a new one.
- **A run is slow** - the researcher may retry several times until the checks pass; each retry searches again.
