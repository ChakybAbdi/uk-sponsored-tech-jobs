"""
UK Sponsored Tech Jobs — Pipeline (v2, serverless)
==================================================

Runs on GitHub Actions (free, public repos), pulls UK tech jobs from
Adzuna's free API, cross-references with the Home Office sponsor list,
and commits a ranked JSON file back to the repo for the dashboard.

Pipeline:
    1. Scrape GOV.UK to find latest sponsor CSV URL
    2. Download + filter to A-rated Skilled Worker sponsors
    3. Hit Adzuna API for AI/ML/SDE × UK (multiple queries, paginated)
    4. Fuzzy-join jobs against sponsor list
    5. Score each match for fit
    6. Write ranked feed to data/jobs.json (committed by the workflow)

Author: Mehdy Chakyb Abdi
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process


# ──────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

GOV_UK_REGISTER_PAGE = (
    "https://www.gov.uk/government/publications/"
    "register-of-licensed-sponsors-workers"
)
ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/gb/search"

# Search queries — broad net (AI/ML + SDE), all UK
SEARCH_QUERIES: list[dict[str, str]] = [
    {"what": "machine learning engineer"},
    {"what": "data scientist"},
    {"what": "ai engineer"},
    {"what": "software engineer graduate"},
    {"what": "junior software engineer"},
    {"what": "research engineer"},
]

# Adzuna pagination — pages 1–3 × 50/page = 150 jobs per query × 6 = 900/day
PAGES_PER_QUERY = 3
RESULTS_PER_PAGE = 50

# Tokens that disqualify a posting for a new-grad applicant
SENIORITY_BLOCKLIST = re.compile(
    r"\b(senior|staff|principal|lead|head\s+of|director|vp|"
    r"manager|architect|10\+\s*years|7\+\s*years|"
    r"5\+\s*years\s+of\s+experience)\b",
    re.IGNORECASE,
)

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

def fetch_latest_sponsor_csv_url(client: httpx.Client) -> str:
    log.info("Fetching GOV.UK register page to locate latest CSV…")
    resp = client.get(GOV_UK_REGISTER_PAGE, timeout=30.0)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "assets.publishing.service.gov.uk/media" in href and href.endswith(".csv"):
            log.info("Found sponsor CSV: %s", href)
            return href
    raise RuntimeError("Could not locate sponsor CSV link on GOV.UK page")


def download_and_filter_sponsors(client: httpx.Client, csv_url: str) -> pd.DataFrame:
    log.info("Downloading sponsor CSV (~10 MB)…")
    resp = client.get(csv_url, timeout=120.0)
    resp.raise_for_status()

    df = pd.read_csv(BytesIO(resp.content))
    df.columns = [c.strip() for c in df.columns]
    log.info("Sponsor CSV loaded: %d rows, columns=%s", len(df), list(df.columns))

    name_col   = next(c for c in df.columns if "organisation" in c.lower())
    route_col  = next(c for c in df.columns if "route" in c.lower())
    rating_col = next(c for c in df.columns if "rating" in c.lower())

    # Filter: Skilled Worker route, A-rated only.
    # 'Type & Rating' contains values like 'Worker (A rating)'.
    filtered = df[
        df[route_col].astype(str).str.contains("Skilled Worker", case=False, na=False)
        & df[rating_col].astype(str).str.contains(r"\(A\s*rating\)", case=False, na=False, regex=True)
    ].copy()

    filtered = filtered.rename(columns={
        name_col: "company_name",
        route_col: "route",
        rating_col: "rating",
    })
    filtered["company_name_norm"] = filtered["company_name"].apply(normalize_company)
    filtered = filtered.drop_duplicates(subset=["company_name_norm"])

    log.info("Filtered to %d A-rated Skilled Worker sponsors", len(filtered))
    return filtered[["company_name", "company_name_norm", "route", "rating"]]


# ──────────────────────────────────────────────────────────────────────
# Company name normalization
# ──────────────────────────────────────────────────────────────────────

CORP_SUFFIXES = re.compile(
    r"\b(ltd|limited|llp|plc|inc|incorporated|corp|corporation|"
    r"gmbh|sa|ag|bv|nv|llc|company|co|holdings|group|"
    r"uk|gb|services|technologies|tech|solutions)\b",
    re.IGNORECASE,
)
NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_company(name: str) -> str:
    if not isinstance(name, str):
        return ""
    s = name.lower()
    s = CORP_SUFFIXES.sub(" ", s)
    s = NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


# ──────────────────────────────────────────────────────────────────────
# Adzuna API client
# ──────────────────────────────────────────────────────────────────────

def fetch_adzuna_jobs(
    client: httpx.Client, app_id: str, app_key: str
) -> list[dict[str, Any]]:
    """Hit Adzuna's UK jobs API for each search query, paginated."""
    all_jobs: list[dict[str, Any]] = []

    for q in SEARCH_QUERIES:
        log.info("Querying Adzuna: %s", q["what"])
        for page in range(1, PAGES_PER_QUERY + 1):
            params = {
                "app_id":           app_id,
                "app_key":          app_key,
                "results_per_page": RESULTS_PER_PAGE,
                "what":             q["what"],
                "max_days_old":     7,
                "content-type":     "application/json",
            }
            url = f"{ADZUNA_BASE}/{page}?{urlencode(params)}"
            try:
                resp = client.get(url, timeout=30.0)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                log.warning("  page %d failed: %s", page, e)
                break

            results = data.get("results", [])
            if not results:
                break
            for r in results:
                r["_query"] = q["what"]
            all_jobs.extend(results)
            log.info("  page %d: +%d jobs (total %d)", page, len(results), len(all_jobs))
            time.sleep(0.5)  # polite pacing — Adzuna allows ~25 req/s, we go gentle

    # Dedupe on Adzuna's stable id
    seen, deduped = set(), []
    for j in all_jobs:
        jid = j.get("id")
        if jid and jid not in seen:
            seen.add(jid)
            deduped.append(j)
    log.info("Total deduplicated jobs: %d", len(deduped))
    return deduped


# ──────────────────────────────────────────────────────────────────────
# Match + score
# ──────────────────────────────────────────────────────────────────────

def match_jobs_to_sponsors(
    jobs: list[dict[str, Any]],
    sponsors: pd.DataFrame,
    threshold: int = 88,
) -> list[dict[str, Any]]:
    sponsor_lookup = sponsors.set_index("company_name_norm")["company_name"].to_dict()
    sponsor_keys = list(sponsor_lookup.keys())

    matched = []
    for job in jobs:
        company_obj = job.get("company") or {}
        raw_co = company_obj.get("display_name") if isinstance(company_obj, dict) else str(company_obj)
        norm_co = normalize_company(raw_co or "")
        if not norm_co:
            continue

        if norm_co in sponsor_lookup:
            job["_sponsor_canonical"] = sponsor_lookup[norm_co]
            job["_match_score"] = 100
            matched.append(job)
            continue

        result = process.extractOne(
            norm_co, sponsor_keys, scorer=fuzz.token_set_ratio
        )
        if result and result[1] >= threshold:
            matched_key = result[0]
            job["_sponsor_canonical"] = sponsor_lookup[matched_key]
            job["_match_score"] = int(result[1])
            matched.append(job)

    log.info("Matched %d / %d jobs to sponsors", len(matched), len(jobs))
    return matched


def score_job(job: dict[str, Any]) -> int:
    title = (job.get("title") or "").lower()
    description = (job.get("description") or "").lower()
    company = (job.get("_sponsor_canonical") or "").lower()
    text = f"{title} {description}"

    if SENIORITY_BLOCKLIST.search(title):
        return -1

    score = 0
    stack_hits = 0
    for token, weight in STACK_TOKENS.items():
        if token in text:
            score += weight
            stack_hits += 1
        if stack_hits >= 8:
            break

    for tier1 in TIER_1_EMPLOYERS:
        if tier1 in company:
            score += 10
            break

    if any(t in title for t in ["graduate", "junior", "entry", "new grad", "associate"]):
        score += 5
    if any(t in title for t in ["machine learning", "ml engineer", "ai engineer", "data scientist", "research engineer"]):
        score += 4

    return score


def shape_for_output(job: dict[str, Any], score: int) -> dict[str, Any]:
    location_obj = job.get("location") or {}
    company_obj = job.get("company") or {}
    return {
        "title":             job.get("title"),
        "company_listed":    company_obj.get("display_name") if isinstance(company_obj, dict) else None,
        "company_sponsor":   job.get("_sponsor_canonical"),
        "match_score":       job.get("_match_score"),
        "fit_score":         score,
        "location":          location_obj.get("display_name") if isinstance(location_obj, dict) else None,
        "salary_min":        job.get("salary_min"),
        "salary_max":        job.get("salary_max"),
        "contract_type":     job.get("contract_type"),
        "posted_at":         job.get("created"),
        "url":               job.get("redirect_url"),
        "query_origin":      job.get("_query"),
        "description_excerpt": (job.get("description") or "")[:400],
    }


# ──────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────

def main() -> int:
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        log.error("ADZUNA_APP_ID and ADZUNA_APP_KEY env vars are required")
        return 1

    log.info("=== UK Sponsored Tech Jobs pipeline starting ===")

    with httpx.Client(
        headers={"User-Agent": "uk-sponsored-jobs-pipeline/2.0"},
        follow_redirects=True,
    ) as client:
        csv_url = fetch_latest_sponsor_csv_url(client)
        sponsors = download_and_filter_sponsors(client, csv_url)
        jobs = fetch_adzuna_jobs(client, app_id, app_key)

    matched = match_jobs_to_sponsors(jobs, sponsors)

    scored: list[dict[str, Any]] = []
    for job in matched:
        s = score_job(job)
        if s < 0:
            continue
        scored.append(shape_for_output(job, s))

    scored.sort(key=lambda r: r["fit_score"], reverse=True)

    output = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "sponsors_used": len(sponsors),
        "jobs_scraped":  len(jobs),
        "jobs_matched":  len(matched),
        "jobs_ranked":   len(scored),
        "jobs":          scored,
    }

    out_path = Path("data/jobs.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, default=str))
    log.info("Wrote %d ranked jobs to %s", len(scored), out_path)

    # Helpful smoke summary
    if scored:
        log.info("Top 5: %s", [r["title"] for r in scored[:5]])

    log.info("=== Done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
