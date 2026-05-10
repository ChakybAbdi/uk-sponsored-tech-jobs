"""
UK Sponsored Tech Jobs — Streamlit Dashboard (v2)
==================================================

Reads data/jobs.json from the same repo, refreshed daily by GitHub Actions.
Deploy: Streamlit Community Cloud (free) → public URL goes on the CV.
No secrets needed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st


# ──────────────────────────────────────────────────────────────────────
# Page config
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

@st.cache_data(ttl=900)  # 15-minute cache; pipeline runs daily anyway
def load_jobs() -> tuple[pd.DataFrame, dict]:
    """Read jobs.json from the local repo (works on Streamlit Cloud)."""
    json_path = Path(__file__).parent.parent / "data" / "jobs.json"
    if not json_path.exists():
        return pd.DataFrame(), {}

    payload = json.loads(json_path.read_text())
    df = pd.DataFrame(payload.get("jobs", []))
    if not df.empty:
        df["fit_score"] = pd.to_numeric(df["fit_score"], errors="coerce").fillna(0).astype(int)
        df["match_score"] = pd.to_numeric(df["match_score"], errors="coerce").fillna(0).astype(int)
    return df, payload


# ──────────────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────────────

st.title("🎯 UK Sponsored Tech Jobs")
st.caption(
    "Live feed of UK AI/ML/SDE roles at companies with active "
    "Skilled Worker sponsor licences (A-rated). "
    "Refreshed daily via GitHub Actions."
)

df, meta = load_jobs()

if df.empty:
    st.warning(
        "No data yet — the pipeline hasn't run successfully. "
        "Check the Actions tab on GitHub."
    )
    st.stop()

# Header metrics
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total ranked jobs",  f"{len(df):,}")
c2.metric("Unique sponsors",    f"{df['company_sponsor'].nunique():,}")
c3.metric("Top fit score",      int(df["fit_score"].max()) if len(df) else 0)
generated_at = meta.get("generated_at")
if generated_at:
    age = datetime.now(timezone.utc) - pd.to_datetime(generated_at, utc=True).to_pydatetime()
    hours = int(age.total_seconds() // 3600)
    c4.metric("Data age", f"{hours}h ago")

# Sidebar filters
st.sidebar.header("Filters")

max_score = int(df["fit_score"].max()) if len(df) else 1
min_score = st.sidebar.slider(
    "Minimum fit score", 0, max_score, min(5, max_score),
    help="Higher = better match (Python, ML/DL, graduate-level).",
)

role_options = sorted(df["query_origin"].dropna().unique().tolist())
selected_roles = st.sidebar.multiselect(
    "Role types", role_options, default=role_options,
)

top_companies = df["company_sponsor"].value_counts().head(50).index.tolist()
selected_companies = st.sidebar.multiselect(
    "Companies (top 50 by job count)", top_companies,
)

search_text = st.sidebar.text_input("Title contains", "")

# Apply filters
filtered = df[df["fit_score"] >= min_score]
if selected_roles:
    filtered = filtered[filtered["query_origin"].isin(selected_roles)]
if selected_companies:
    filtered = filtered[filtered["company_sponsor"].isin(selected_companies)]
if search_text:
    filtered = filtered[filtered["title"].str.contains(search_text, case=False, na=False)]

filtered = filtered.sort_values("fit_score", ascending=False).reset_index(drop=True)

st.markdown(f"### {len(filtered)} matching roles")

display_cols = [
    "fit_score", "title", "company_sponsor", "location",
    "salary_min", "salary_max", "posted_at", "query_origin", "url",
]
display_cols = [c for c in display_cols if c in filtered.columns]

st.dataframe(
    filtered[display_cols],
    use_container_width=True,
    hide_index=True,
    column_config={
        "fit_score":       st.column_config.NumberColumn("Score", width="small"),
        "title":           st.column_config.TextColumn("Role", width="large"),
        "company_sponsor": st.column_config.TextColumn("Sponsor", width="medium"),
        "location":        st.column_config.TextColumn("Location", width="medium"),
        "salary_min":      st.column_config.NumberColumn("Min salary £", format="%.0f"),
        "salary_max":      st.column_config.NumberColumn("Max salary £", format="%.0f"),
        "posted_at":       st.column_config.TextColumn("Posted", width="small"),
        "query_origin":    st.column_config.TextColumn("Source query", width="medium"),
        "url":             st.column_config.LinkColumn("Apply", display_text="Open ↗"),
    },
    height=600,
)

# Detail panel
st.markdown("---")
st.markdown("### Job detail")
if len(filtered) > 0:
    options = filtered.apply(
        lambda r: f"[{r['fit_score']}] {r['title']} — {r['company_sponsor']}",
        axis=1,
    ).tolist()
    pick = st.selectbox("Select a role:", options)
    idx = options.index(pick)
    row = filtered.iloc[idx]
    st.markdown(f"**[{row['title']}]({row['url']})** at *{row['company_sponsor']}*")
    salary = ""
    if row.get("salary_min") and row.get("salary_max"):
        salary = f" · £{int(row['salary_min']):,}–£{int(row['salary_max']):,}"
    st.caption(
        f"Location: {row['location']} · Posted: {row['posted_at']}"
        f" · Match score: {row['match_score']}{salary}"
    )
    st.write(row.get("description_excerpt", ""))

# Footer
st.markdown("---")
st.caption(
    "Built by Mehdy Chakyb Abdi · MSc AI, Brunel University London · "
    "Pipeline: GOV.UK sponsor register → Adzuna API → fuzzy join → "
    "ranked JSON → GitHub Actions (daily) → Streamlit"
)
