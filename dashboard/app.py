"""
UK Sponsored Tech Jobs — Streamlit Dashboard
============================================

Reads the latest run of the `uk-sponsored-tech-jobs` Apify actor and
renders a filterable, ranked table of jobs at A-rated UK sponsors.

Deploy: Streamlit Community Cloud (free) → public URL goes on the CV.
Secrets: APIFY_TOKEN, APIFY_ACTOR_ID set in Streamlit Cloud's secrets manager.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from apify_client import ApifyClient


# ──────────────────────────────────────────────────────────────────────
# Page config + theming
# ──────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="UK Sponsored Tech Jobs",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ──────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=900)  # 15-minute cache — actor runs daily anyway
def load_jobs() -> tuple[pd.DataFrame, datetime | None]:
    """Pull the latest run's dataset from Apify."""
    token = st.secrets.get("APIFY_TOKEN") or os.environ.get("APIFY_TOKEN")
    actor_id = st.secrets.get("APIFY_ACTOR_ID") or os.environ.get("APIFY_ACTOR_ID")

    if not token or not actor_id:
        st.error("APIFY_TOKEN and APIFY_ACTOR_ID must be set in Streamlit secrets.")
        st.stop()

    client = ApifyClient(token)
    last_run = client.actor(actor_id).last_run(status="SUCCEEDED")
    if last_run is None:
        return pd.DataFrame(), None

    run_info = last_run.get()
    finished_at = run_info.get("finishedAt") if run_info else None
    items = list(last_run.dataset().iterate_items())
    df = pd.DataFrame(items)

    if not df.empty:
        df["fit_score"] = pd.to_numeric(df["fit_score"], errors="coerce").fillna(0).astype(int)
        df["match_score"] = pd.to_numeric(df["match_score"], errors="coerce").fillna(0).astype(int)

    return df, finished_at


# ──────────────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────────────

st.title("🎯 UK Sponsored Tech Jobs")
st.caption(
    "Live feed of UK AI/ML/SDE roles at companies with active "
    "Skilled Worker sponsor licences (A-rated). "
    "Updated daily from the Home Office register + LinkedIn."
)

with st.spinner("Loading latest run from Apify…"):
    df, finished_at = load_jobs()

if df.empty:
    st.warning("No data yet — has the actor run successfully?")
    st.stop()

# Header metrics row
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total ranked jobs", f"{len(df):,}")
c2.metric("Unique sponsors",   f"{df['company_sponsor'].nunique():,}")
c3.metric("Top fit score",     int(df["fit_score"].max()))
if finished_at:
    age = datetime.now(timezone.utc) - pd.to_datetime(finished_at, utc=True).to_pydatetime()
    hours = int(age.total_seconds() // 3600)
    c4.metric("Data age",       f"{hours}h ago")

# Sidebar filters
st.sidebar.header("Filters")

min_score = st.sidebar.slider(
    "Minimum fit score", 0, int(df["fit_score"].max()), 5,
    help="Higher = better match for Chakyb's profile (Python, ML/DL, graduate-level).",
)

role_options = sorted(df["query_origin"].dropna().unique().tolist())
selected_roles = st.sidebar.multiselect(
    "Role types", role_options, default=role_options,
)

top_companies = (
    df["company_sponsor"].value_counts().head(50).index.tolist()
)
selected_companies = st.sidebar.multiselect(
    "Companies (top 50 by job count)", top_companies,
)

search_text = st.sidebar.text_input(
    "Title contains", "",
    help="Substring filter on job title (case-insensitive).",
)

# Apply filters
filtered = df[df["fit_score"] >= min_score]
if selected_roles:
    filtered = filtered[filtered["query_origin"].isin(selected_roles)]
if selected_companies:
    filtered = filtered[filtered["company_sponsor"].isin(selected_companies)]
if search_text:
    filtered = filtered[
        filtered["title"].str.contains(search_text, case=False, na=False)
    ]

filtered = filtered.sort_values("fit_score", ascending=False).reset_index(drop=True)

st.markdown(f"### {len(filtered)} matching roles")

# Main table
display_cols = [
    "fit_score", "title", "company_sponsor", "location",
    "posted_at", "query_origin", "url",
]
st.dataframe(
    filtered[display_cols],
    use_container_width=True,
    hide_index=True,
    column_config={
        "fit_score":       st.column_config.NumberColumn("Score", width="small"),
        "title":           st.column_config.TextColumn("Role", width="large"),
        "company_sponsor": st.column_config.TextColumn("Sponsor", width="medium"),
        "location":        st.column_config.TextColumn("Location", width="medium"),
        "posted_at":       st.column_config.TextColumn("Posted", width="small"),
        "query_origin":    st.column_config.TextColumn("Source query", width="medium"),
        "url":             st.column_config.LinkColumn("Apply", display_text="Open ↗"),
    },
    height=600,
)

# Detail expander for selected job
st.markdown("---")
st.markdown("### Job detail")
if len(filtered) > 0:
    options = filtered.apply(
        lambda r: f"[{r['fit_score']}] {r['title']} — {r['company_sponsor']}",
        axis=1,
    ).tolist()
    pick = st.selectbox("Select a role to see the description excerpt:", options)
    idx = options.index(pick)
    row = filtered.iloc[idx]
    st.markdown(f"**[{row['title']}]({row['url']})** at *{row['company_sponsor']}*")
    st.caption(f"Location: {row['location']} · Posted: {row['posted_at']} · Match score: {row['match_score']}")
    st.write(row.get("description_excerpt", ""))

# Footer — portfolio attribution
st.markdown("---")
st.caption(
    "Built by Mehdy Chakyb Abdi · MSc AI, Brunel University London · "
    "Pipeline: GOV.UK sponsor register → Apify (LinkedIn jobs) → "
    "fuzzy join → ranked dataset → Streamlit"
)
