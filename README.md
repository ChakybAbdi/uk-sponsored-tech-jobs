# UK Sponsored Tech Jobs Pipeline

A daily-refreshed, ranked feed of UK AI/ML/SDE roles **at companies legally able to sponsor a Skilled Worker visa**, surfaced through a public Streamlit dashboard.

> Built to solve a concrete problem: 75% of international applicants waste their job hunt on employers who can't sponsor them. This pipeline removes that 75% from the search space before the first application is written.

**Live dashboard:** _add Streamlit URL after deploy_
**Actor:** _add Apify Store URL after publish_

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  APIFY (scheduled daily, 06:00 UTC)                         │
│                                                             │
│  Custom Python actor: uk-sponsored-tech-jobs                │
│  ├─ 1. Scrape GOV.UK to find latest sponsor CSV URL         │
│  ├─ 2. Download + filter to A-rated Skilled Worker sponsors │
│  ├─ 3. Call bebity/linkedin-jobs-scraper × 6 queries        │
│  ├─ 4. Fuzzy-join (rapidfuzz, token_set_ratio ≥ 88)         │
│  ├─ 5. Score each job (seniority filter + stack + tier)     │
│  └─ 6. Push ranked records to default dataset               │
└──────────────────────┬──────────────────────────────────────┘
                       │ Apify Dataset (REST API)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  STREAMLIT COMMUNITY CLOUD (free tier)                      │
│                                                             │
│  app.py — reads last_run.dataset(), renders filterable      │
│  ranked table with fit-score sliders, role/company filters, │
│  job-detail panel.                                          │
└─────────────────────────────────────────────────────────────┘
```

## Why this design

| Decision | Reasoning |
|---|---|
| **Scrape GOV.UK page, don't hardcode CSV URL** | The CSV is content-addressed — its URL changes every update (~daily). Hardcoding breaks within 24 h. |
| **A-rated only, Skilled Worker only** | B-rated sponsors cannot assign new Certificates of Sponsorship — they're a dead end for a fresh applicant. Filtering them out at source removes ~20% noise. |
| **Fuzzy join on normalized company names** | "Amazon UK Services Ltd" (sponsor register) ≠ "Amazon" (LinkedIn). Custom normalization strips corporate suffixes, then `rapidfuzz.token_set_ratio ≥ 88` handles the residual variance. |
| **Score, don't filter** | Seniority is the only hard filter (new-grad targeting). Stack match, tier-1 employer boost, and graduate-friendly title bonuses are additive — preserves recall while ranking surfaces precision. |
| **Apify Dataset as state, not a DB** | Each run = one dataset. Free tier handles it; no infra to manage; dashboard reads the latest succeeded run via `actor.last_run(status="SUCCEEDED")`. |
| **Streamlit on Community Cloud** | Free public URL → CV-ready link. Deploy is `git push`. |

## Repository layout

```
.
├── actor/                          # Apify actor (Python)
│   ├── .actor/
│   │   ├── actor.json              # Apify metadata + dataset views
│   │   └── input_schema.json       # Run-time inputs
│   ├── src/
│   │   └── main.py                 # Pipeline implementation
│   ├── Dockerfile                  # apify/actor-python:3.12 base
│   └── requirements.txt
├── dashboard/                      # Streamlit Cloud app
│   ├── .streamlit/
│   │   └── secrets.toml.example
│   ├── app.py
│   └── requirements.txt
└── README.md
```

## Local development

### Actor

```bash
cd actor
pip install -r requirements.txt
pip install apify-cli
apify run --purge
```

Set `APIFY_TOKEN` env var; the actor uses `Actor.call()` to invoke the LinkedIn scraper, which counts against your Apify usage.

### Dashboard

```bash
cd dashboard
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edit with your APIFY_TOKEN and APIFY_ACTOR_ID
streamlit run app.py
```

## Deployment

### Actor → Apify Console

```bash
cd actor
apify login
apify push
```

Then in the Apify console: **Actor → Schedule → Daily 06:00 UTC**.

### Dashboard → Streamlit Cloud

1. Push this repo to GitHub
2. <https://share.streamlit.io> → **New app** → point to `dashboard/app.py`
3. **Settings → Secrets** → paste the contents of `secrets.toml.example` with real values

## Cost

| Component | Cost (May 2026 pricing) |
|---|---|
| GOV.UK CSV | £0 (public data) |
| Apify free tier | $5 platform credits / month — enough for daily runs |
| `bebity/linkedin-jobs-scraper` | $0.40 / 1K jobs — daily scrape ≈ 6 × 200 = 1,200 jobs ≈ $0.48/day ≈ $14/month |
| Streamlit Community Cloud | £0 |

> The LinkedIn scraper cost is the only meaningful variable. Lowering `rows` per query or running every 2–3 days instead of daily brings it under the Apify free tier comfortably.

## Compliance & ethics

- Sponsor register data is **public and explicitly published for this purpose** (employer verification by job-seekers).
- LinkedIn job postings are scraped via a cookieless actor that uses LinkedIn's public guest API — the same data Google's crawler indexes.
- **No personal data collected.** No emails, no recruiter profiles, no candidate tracking. Just job postings + employer metadata.
- Applications themselves remain manual / user-driven. This pipeline is intelligence, not automation of outbound contact.

## Roadmap

- [ ] Add Indeed UK scraper as a second job source
- [ ] Direct careers-page detection (Greenhouse / Lever / Workday URLs) — higher conversion than LinkedIn Easy Apply
- [ ] Salary normalization across postings
- [ ] Per-company application-tracker view (state in Supabase)
- [ ] Email digest via SendGrid (opt-in, self only)

---

**Author:** Mehdy Chakyb Abdi · MSc Artificial Intelligence, Brunel University London (graduating Jan 2027)
