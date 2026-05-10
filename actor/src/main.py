"""
UK Sponsored Tech Jobs Intelligence Pipeline
============================================

Apify Actor that runs daily and produces a ranked dataset of UK tech jobs
at companies legally able to sponsor a Skilled Worker visa.

Pipeline:
    1. Scrape GOV.UK to find the latest Worker sponsor CSV URL
    2. Download + filter to A-rated Skilled Worker sponsors
    3. Call LinkedIn Jobs scraper actor for AI/ML/SDE × UK
    4. Fuzzy-join jobs against sponsor list (company name normalization)
    5. Score each match for fit (seniority, stack, channel)
    6. Push top results to Apify Dataset for the dashboard to consume

Author: Mehdy Chakyb Abdi
"""

from __future__ import annotations

import asyncio
import re
import io
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from apify import Actor
from rapidfuzz import fuzz, process
from bs4 import BeautifulSoup


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

GOV_UK_REGISTER_PAGE = (
    "https://www.gov.uk/government/publications/"
    "register-of-licensed-sponsors-workers"
)

# LinkedIn Jobs scraper actor on Apify (cookieless, public-API-based)
LINKEDIN_JOBS_ACTOR_ID = "bebity/linkedin-jobs-scraper"

# Search queries — broad net (AI/ML + SDE), all UK
SEARCH_QUERIES: list[dict[str, str]] = [
    {"title": "Machine Learning Engineer", "location": "United Kingdom"},
    {"title": "Data Scientist",            "location": "United Kingdom"},
    {"title": "AI Engineer",               "location": "United Kingdom"},
    {"title": "Software Engineer Graduate", "location": "United Kingdom"},
    {"title": "Junior Software Engineer",  "location": "United Kingdom"},
    {"title": "Research Engineer",         "location": "United Kingdom"},
]

# Tokens that disqualify a posting for a new-grad applicant
SENIORITY_BLOCKLIST = re.compile(
    r"\b(senior|staff|principal|lead|head\s+of|director|vp|"
    r"manager|architect|10\+\s*years|7\+\s*years|"
    r"5\+\s*years\s+of\s+experience)\b",
    re.IGNORECASE,
)

# Stack tokens we reward (Chakyb's profile)
STACK_TOKENS = {
    "python":     3,
    "pytorch":    3,
    "tensorflow": 3,
    "deep learning": 3,
    "machine learning": 2,
    "ml":         2,
    "nlp":        2,
    "computer vision": 2,
    "lstm":       2,
    "gru":        2,
    "transformer": 2,
    "tableau":    2,
    "sql":        1,
    "aws":        1,
    "gcp":        1,
    "docker":     1,
    "kubernetes": 1,
    "r":          1,
}

# Tier-1 employers — boost these (by canonical name fragment)
TIER_1_EMPLOYERS = {
    "amazon", "google", "deepmind", "meta", "microsoft", "apple",
    "anthropic", "openai", "nvidia", "stripe", "spotify", "netflix",
    "bytedance", "tiktok", "uber", "airbnb", "palantir",
    "jane street", "two sigma", "citadel", "g-research",
    "arm", "graphcore", "wayve", "isomorphic labs",
}


# ──────────────────────────────────────────────────────────────────────
# Sponsor list pipeline
# ──────────────────────────────────────────────────────────────────────

async def fetch_latest_sponsor_csv_url(client: httpx.AsyncClient) -> str:
    """Scrape the GOV.UK register page to find the current CSV URL.

    The CSV is content-addressed, so the URL changes every update. We
    must derive it from the live page rather than hardcoding it.
    """
    Actor.log.info("Fetching GOV.UK register page to locate latest CSV…")
    resp = await client.get(GOV_UK_REGISTER_PAGE, timeout=30.0)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    # Anchor pattern: assets.publishing.service.gov.uk/media/<hash>/...csv
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "assets.publishing.service.gov.uk/media" in href and href.endswith(".csv"):
            Actor.log.info("Found sponsor CSV: %s", href)
            return href

    raise RuntimeError("Could not locate sponsor CSV link on GOV.UK page")


async def download_and_filter_sponsors(
    client: httpx.AsyncClient, csv_url: str
) -> pd.DataFrame:
    """Download the sponsor CSV and filter to A-rated Skilled Worker sponsors."""
    Actor.log.info("Downloading sponsor CSV (~10 MB)…")
    resp = await client.get(csv_url, timeout=120.0)
    resp.raise_for_status()

    # The Home Office CSV has a 1-row preamble in some versions; pandas
    # auto-detects, but we use skip_blank_lines and normalize columns.
    df = pd.read_csv(io.BytesIO(resp.content))
    df.columns = [c.strip() for c in df.columns]

    # Column names have varied historically; resolve them defensively.
    name_col   = next(c for c in df.columns if "organisation" in c.lower() or "name" in c.lower())
    route_col  = next(c for c in df.columns if "route" in c.lower())
    rating_col = next(c for c in df.columns if "rating" in c.lower())

    Actor.log.info("Sponsor CSV loaded: %d rows, columns=%s", len(df), list(df.columns))

    # Filter: Skilled Worker route, A-rated only.
    filtered = df[
        df[route_col].astype(str).str.contains("Skilled Worker", case=False, na=False)
        & df[rating_col].astype(str).str.contains(r"\bA\b", case=False, na=False, regex=True)
    ].copy()

    filtered = filtered.rename(columns={
        name_col: "company_name",
        route_col: "route",
        rating_col: "rating",
    })
    filtered["company_name_norm"] = filtered["company_name"].apply(normalize_company)
    filtered = filtered.drop_duplicates(subset=["company_name_norm"])

    Actor.log.info("Filtered to %d A-rated Skilled Worker sponsors", len(filtered))
    return filtered[["company_name", "company_name_norm", "route", "rating"]]


# ──────────────────────────────────────────────────────────────────────
# Company name normalization (the hard part of the join)
# ──────────────────────────────────────────────────────────────────────

CORP_SUFFIXES = re.compile(
    r"\b(ltd|limited|llp|plc|inc|incorporated|corp|corporation|"
    r"gmbh|sa|ag|bv|nv|llc|company|co|holdings|group|"
    r"uk|gb|services|technologies|tech|solutions)\b",
    re.IGNORECASE,
)
NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_company(name: str) -> str:
    """Strip corporate suffixes and noise so 'Amazon UK Services Ltd' ≈ 'Amazon'."""
    if not isinstance(name, str):
        return ""
    s = name.lower()
    s = CORP_SUFFIXES.sub(" ", s)
    s = NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


# ──────────────────────────────────────────────────────────────────────
# Job scraping (delegate to bebity/linkedin-jobs-scraper)
# ──────────────────────────────────────────────────────────────────────

async def scrape_jobs() -> list[dict[str, Any]]:
    """Call the LinkedIn Jobs scraper actor for each search query."""
    all_jobs: list[dict[str, Any]] = []

    for q in SEARCH_QUERIES:
        Actor.log.info("Scraping LinkedIn for: %(title)s in %(location)s", q)
        run_input = {
            "title":    q["title"],
            "location": q["location"],
            "rows":     200,             # ~200 per query × 6 queries = ~1200 jobs
            "publishedAt": "r604800",    # last 7 days
        }
        try:
            run = await Actor.call(actor_id=LINKEDIN_JOBS_ACTOR_ID, run_input=run_input)
            if run is None:
                Actor.log.warning("LinkedIn scraper returned None for query %s", q)
                continue
            dataset_id = run["defaultDatasetId"]
            items = await Actor.apify_client.dataset(dataset_id).list_items()
            jobs = items.items if hasattr(items, "items") else items.get("items", [])
            Actor.log.info("  → %d jobs", len(jobs))
            for j in jobs:
                j["_query"] = q["title"]
            all_jobs.extend(jobs)
        except Exception as e:
            Actor.log.exception("LinkedIn scrape failed for %s: %s", q, e)

    # Deduplicate on job URL
    seen, deduped = set(), []
    for j in all_jobs:
        url = j.get("link") or j.get("url") or j.get("jobUrl")
        if url and url not in seen:
            seen.add(url)
            deduped.append(j)
    Actor.log.info("Total deduplicated jobs: %d", len(deduped))
    return deduped


# ──────────────────────────────────────────────────────────────────────
# Match + score
# ──────────────────────────────────────────────────────────────────────

def match_jobs_to_sponsors(
    jobs: list[dict[str, Any]],
    sponsors: pd.DataFrame,
    threshold: int = 88,
) -> list[dict[str, Any]]:
    """Fuzzy-join each job's company against the sponsor list.

    Returns only jobs where the posting company matches an A-rated
    Skilled Worker sponsor with rapidfuzz score ≥ threshold.
    """
    sponsor_lookup = sponsors.set_index("company_name_norm")["company_name"].to_dict()
    sponsor_keys = list(sponsor_lookup.keys())

    matched = []
    for job in jobs:
        raw_co = job.get("companyName") or job.get("company") or ""
        norm_co = normalize_company(raw_co)
        if not norm_co:
            continue

        # Exact-normalized hit first (cheap), then fuzzy fallback.
        if norm_co in sponsor_lookup:
            job["_sponsor_canonical"] = sponsor_lookup[norm_co]
            job["_match_score"] = 100
            matched.append(job)
            continue

        match = process.extractOne(
            norm_co, sponsor_keys, scorer=fuzz.token_set_ratio
        )
        if match and match[1] >= threshold:
            matched_key = match[0]
            job["_sponsor_canonical"] = sponsor_lookup[matched_key]
            job["_match_score"] = match[1]
            matched.append(job)

    Actor.log.info("Matched %d / %d jobs to sponsors", len(matched), len(jobs))
    return matched


def score_job(job: dict[str, Any]) -> int:
    """Score a job for fit. Higher = better. Negative = blocked."""
    title = (job.get("title") or "").lower()
    description = (job.get("description") or "").lower()
    company = (job.get("_sponsor_canonical") or "").lower()
    text = f"{title} {description}"

    # Seniority block — return -1 to filter out entirely
    if SENIORITY_BLOCKLIST.search(title):
        return -1

    score = 0

    # Stack matches (capped — avoid keyword-stuffed JDs gaming the score)
    stack_hits = 0
    for token, weight in STACK_TOKENS.items():
        if token in text:
            score += weight
            stack_hits += 1
        if stack_hits >= 8:
            break

    # Tier-1 employer boost
    for tier1 in TIER_1_EMPLOYERS:
        if tier1 in company:
            score += 10
            break

    # Graduate-friendly title boosts
    if any(t in title for t in ["graduate", "junior", "entry", "new grad", "associate"]):
        score += 5

    # ML/AI title direct match (Chakyb's primary target)
    if any(t in title for t in ["machine learning", "ml engineer", "ai engineer", "data scientist", "research engineer"]):
        score += 4

    return score


# ──────────────────────────────────────────────────────────────────────
# Output shaping
# ──────────────────────────────────────────────────────────────────────

def shape_for_output(job: dict[str, Any], score: int) -> dict[str, Any]:
    """Reduce a raw scraped job to the dashboard's schema."""
    return {
        "scraped_at":      datetime.now(timezone.utc).isoformat(),
        "title":           job.get("title"),
        "company_listed":  job.get("companyName") or job.get("company"),
        "company_sponsor": job.get("_sponsor_canonical"),
        "match_score":     job.get("_match_score"),
        "fit_score":       score,
        "location":        job.get("location"),
        "posted_at":       job.get("postedAt") or job.get("publishedAt"),
        "url":             job.get("link") or job.get("url") or job.get("jobUrl"),
        "query_origin":    job.get("_query"),
        "description_excerpt": (job.get("description") or "")[:400],
    }


# ──────────────────────────────────────────────────────────────────────
# Actor entry point
# ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    async with Actor:
        Actor.log.info("=== UK Sponsored Tech Jobs pipeline starting ===")

        async with httpx.AsyncClient(
            headers={"User-Agent": "uk-sponsored-jobs-actor/1.0"}
        ) as client:
            csv_url = await fetch_latest_sponsor_csv_url(client)
            sponsors = await download_and_filter_sponsors(client, csv_url)

        jobs = await scrape_jobs()
        matched = match_jobs_to_sponsors(jobs, sponsors)

        scored: list[dict[str, Any]] = []
        for job in matched:
            s = score_job(job)
            if s < 0:
                continue
            scored.append(shape_for_output(job, s))

        scored.sort(key=lambda r: r["fit_score"], reverse=True)

        Actor.log.info(
            "Pushing %d ranked jobs to dataset (top score=%s)",
            len(scored),
            scored[0]["fit_score"] if scored else "N/A",
        )
        await Actor.push_data(scored)

        # Summary as actor output
        await Actor.set_value("OUTPUT", {
            "run_at":        datetime.now(timezone.utc).isoformat(),
            "sponsors_used": len(sponsors),
            "jobs_scraped":  len(jobs),
            "jobs_matched":  len(matched),
            "jobs_ranked":   len(scored),
            "top_5_titles":  [r["title"] for r in scored[:5]],
        })

        Actor.log.info("=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
