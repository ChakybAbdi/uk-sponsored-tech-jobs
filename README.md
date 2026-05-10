# UK Sponsored Tech Jobs Pipeline

A daily-refreshed, ranked feed of UK AI/ML/SDE roles **at companies legally able to sponsor a Skilled Worker visa**, surfaced through a public Streamlit dashboard.

> Built to solve a concrete problem: 75% of international applicants waste their job hunt on employers who can't sponsor them. This pipeline removes that 75% from the search space before the first application is written.

**Live dashboard:** _add Streamlit URL after deploy_
**Source:** `pipeline/run.py`

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  GITHUB ACTIONS (scheduled daily, 06:00 UTC, free)          │
│                                                             │
│  pipeline/run.py                                            │
│  ├─ 1. Scrape GOV.UK to find latest sponsor CSV URL         │
│  ├─ 2. Download + filter to A-rated Skilled Worker sponsors │
│  ├─ 3. Hit Adzuna API × 6 queries × 3 pages = ~900 jobs     │
│  ├─ 4. Fuzzy-join (rapidfuzz, token_set_ratio ≥ 88)         │
│  ├─ 5. Score each job (seniority filter + stack + tier)     │
│  └─ 6. Commit data/jobs.json back to repo                   │
└──────────────────────┬──────────────────────────────────────┘
                       │ raw GitHub URL
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  STREAMLIT COMMUNITY CLOUD (free tier)                      │
│                                                             │
│  dashboard/app.py — reads data/jobs.json and renders a      │
│  filterable ranked table with fit-score sliders, role and   │
│  company filters, and a job-detail panel.                   │
└─────────────────────────────────────────────────────────────┘
```

**Total monthly cost: £0.** No paid services, no third-party scrapers, no API rentals.

## Why this design

| Decision | Reasoning |
|---|---|
| **GitHub Actions, not a paid scheduler** | Free for public repos. 2,000 minutes/month included; a daily 5-minute run uses ~150 mins/month. |
| **Adzuna API, not LinkedIn scraping** | Adzuna's REST API is free, lawful, structured, and aggregates from many sources. LinkedIn scrapers are brittle and frequently paid-rental. |
| **GOV.UK CSV scraped at runtime** | The CSV's URL is content-addressed (hash changes per update). We scrape the listing page to discover the latest URL each run. |
| **A-rated Skilled Worker only** | B-rated sponsors cannot assign new Certificates of Sponsorship — dead end for fresh applicants. Filtering at source removes ~20% noise. |
| **Fuzzy join on normalized names** | "Amazon UK Services Ltd" (sponsor register) ≠ "Amazon" (Adzuna). Custom suffix-stripping + `rapidfuzz.token_set_ratio ≥ 88`. |
| **Score, don't filter** | Seniority is the only hard filter. Stack match, tier-1 boost, graduate keywords are additive — preserves recall, ranking surfaces precision. |
| **JSON file as state, not a DB** | Each run overwrites `data/jobs.json`. Tracked in git → free history, free hosting, zero infra. |

## Repository layout

```
.
├── .github/workflows/refresh.yml   # GitHub Actions cron + commit
├── pipeline/
│   ├── run.py                      # The pipeline (single file, ~250 LOC)
│   └── requirements.txt
├── dashboard/
│   ├── app.py                      # Streamlit Cloud app
│   └── requirements.txt
├── data/
│   └── jobs.json                   # Refreshed daily by Actions
├── .gitignore
└── README.md
```

## Setup (one-time)

### 1. Get an Adzuna API key (free, 30 seconds)

1. Register at <https://developer.adzuna.com/signup>
2. After login, your `app_id` and `app_key` are on the dashboard
3. Free tier: 250 calls/day — plenty for 6 queries × 3 pages = 18 calls/day

### 2. Add secrets to GitHub

In your repo on GitHub: **Settings → Secrets and variables → Actions → New repository secret**

- `ADZUNA_APP_ID` = your Adzuna app id
- `ADZUNA_APP_KEY` = your Adzuna app key

### 3. Trigger the first run

GitHub: **Actions tab → "Refresh UK Sponsored Jobs" → Run workflow → main → Run workflow**

After ~3 minutes the action commits `data/jobs.json`.

### 4. Deploy the dashboard

1. Go to <https://share.streamlit.io>
2. **New app** → connect this GitHub repo
3. Main file path: `dashboard/app.py`
4. Deploy. No secrets needed — the dashboard reads the committed JSON file.

## Local development

```bash
git clone https://github.com/ChakybAbdi/uk-sponsored-tech-jobs.git
cd uk-sponsored-tech-jobs

# Pipeline — one-shot manual run
pip install -r pipeline/requirements.txt
export ADZUNA_APP_ID=your_id
export ADZUNA_APP_KEY=your_key
python pipeline/run.py

# Dashboard
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

## Compliance

- The Home Office sponsor register is **public data, explicitly published for employer verification by job-seekers.**
- Adzuna's API has clear terms permitting redistribution of search results with attribution to source jobs.
- **No personal data collected.** No emails, no recruiter profiles. Only job postings + employer metadata.
- Applications themselves remain manual / user-driven.

## Roadmap

- [ ] Add the DWP Find a Job API as a second source
- [ ] Salary normalization (Adzuna provides ranges; could enrich missing rows from histograms)
- [ ] Per-company application tracker (state in browser localStorage on the dashboard)
- [ ] RSS feed output for personal use

---

**Author:** Mehdy Chakyb Abdi · MSc Artificial Intelligence, Brunel University London (graduating January 2027)
