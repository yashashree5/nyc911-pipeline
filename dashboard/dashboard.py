"""
NYC 911 — Real-Time Emergency Response Intelligence Dashboard
=============================================================
Run:
    streamlit run dashboard.py -- --scored /tmp/nyc911_scored
    streamlit run dashboard.py -- --demo   (synthetic data, no Kafka needed)
"""

import argparse
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import os

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NYC 911 Live Risk Dashboard",
    page_icon="🚨",
    layout="wide",
    initial_sidebar_state="collapsed",
)

REFRESH_SECS = 20
BORO_ORDER   = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]

# Centroid (lat, lon) for text labels on the map
BORO_CENTROIDS = {
    "BRONX":         (40.844,  -73.865),
    "BROOKLYN":      (40.650,  -73.950),
    "MANHATTAN":     (40.775,  -73.971),
    "QUEENS":        (40.728,  -73.795),
    "STATEN ISLAND": (40.580,  -74.150),
}

# Simplified outline polygons — (lon, lat) — good enough to show borough shapes
BORO_POLYGONS = {
    "BRONX": [
        (-73.933,40.796),(-73.910,40.800),(-73.896,40.813),
        (-73.867,40.837),(-73.833,40.873),(-73.829,40.898),
        (-73.863,40.900),(-73.910,40.878),(-73.933,40.853),
        (-73.942,40.830),(-73.933,40.796),
    ],
    "BROOKLYN": [
        (-74.042,40.679),(-74.015,40.640),(-73.978,40.608),
        (-73.940,40.577),(-73.910,40.577),(-73.886,40.595),
        (-73.862,40.620),(-73.862,40.650),(-73.880,40.682),
        (-73.915,40.695),(-73.960,40.710),(-74.001,40.718),
        (-74.042,40.679),
    ],
    "MANHATTAN": [
        (-74.020,40.700),(-73.973,40.703),(-73.935,40.730),
        (-73.927,40.774),(-73.935,40.820),(-73.948,40.857),
        (-73.934,40.880),(-73.912,40.878),(-73.910,40.855),
        (-73.920,40.810),(-73.912,40.762),(-73.930,40.720),
        (-73.975,40.697),(-74.020,40.700),
    ],
    "QUEENS": [
        (-73.962,40.728),(-73.920,40.695),(-73.860,40.660),
        (-73.790,40.640),(-73.730,40.660),(-73.700,40.700),
        (-73.700,40.760),(-73.730,40.790),(-73.800,40.790),
        (-73.850,40.770),(-73.910,40.750),(-73.962,40.728),
    ],
    "STATEN ISLAND": [
        (-74.260,40.496),(-74.195,40.510),(-74.055,40.570),
        (-74.055,40.620),(-74.120,40.648),(-74.180,40.637),
        (-74.249,40.601),(-74.260,40.550),(-74.260,40.496),
    ],
}

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600;700&display=swap');
html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
.metric-card {
    background:#0d1b2a; border:1px solid #1e3a5f; border-radius:8px;
    padding:16px 20px; text-align:center; margin-bottom:4px;
}
.metric-value { font-family:'IBM Plex Mono',monospace; font-size:2rem; font-weight:600; color:#f0a500; line-height:1; }
.metric-label { font-size:.72rem; color:#7a9cbf; text-transform:uppercase; letter-spacing:.08em; margin-top:6px; }
.section-header { font-size:.68rem; color:#4a7fa5; text-transform:uppercase; letter-spacing:.12em;
    border-bottom:1px solid #1e3a5f; padding-bottom:5px; margin-bottom:6px; margin-top:2px; }
.chart-caption { font-size:.76rem; color:#6b8da8; font-style:italic; margin-top:2px; margin-bottom:8px; }
.alert-box { background:#2a0a0a; border-left:4px solid #e63946; border-radius:0 6px 6px 0;
    padding:10px 14px; margin:6px 0; font-size:.83rem; color:#ffb3b3; }
.ok-box { background:#0a1f0a; border-left:4px solid #2dc653; border-radius:0 6px 6px 0;
    padding:10px 14px; margin:6px 0; font-size:.83rem; color:#b3ffcc; }
.warn-box { background:#1a1200; border-left:4px solid #f0a500; border-radius:0 6px 6px 0;
    padding:10px 14px; margin:6px 0; font-size:.83rem; color:#ffe0a0; }
</style>
""", unsafe_allow_html=True)


# ── helpers ───────────────────────────────────────────────────────────────────
def risk_colour(pct: float) -> str:
    if pct >= 50: return "#e63946"
    if pct >= 40: return "#f4a261"
    return "#2dc653"

def risk_label(pct: float) -> str:
    if pct >= 50: return "🔴 HIGH"
    if pct >= 40: return "🟠 MEDIUM"
    return "🟢 LOW"

def dark_layout(**extra) -> dict:
    base = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans", color="#c8d8e8", size=12),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    base.update(extra)
    return base


# ── demo data ─────────────────────────────────────────────────────────────────
def make_demo_data() -> pd.DataFrame:
    import numpy as np
    rng   = np.random.default_rng(int(time.time()) % 9999)
    n     = 800
    boros = rng.choice(BORO_ORDER, n, p=[0.22, 0.28, 0.20, 0.22, 0.08])
    base  = {"BRONX":0.54,"BROOKLYN":0.51,"QUEENS":0.50,"STATEN ISLAND":0.49,"MANHATTAN":0.36}
    p_slow = np.array([base[b] + rng.normal(0, 0.09) for b in boros]).clip(0.05, 0.95)
    types  = rng.choice(
        ["ASSAULT","ROBBERY","AMBULANCE CASE EDP","SHOTS FIRED","AUTO ACCIDENT",
         "UNCONSCIOUS","BURGLARY","DISPUTE","DOMESTIC DISPUTE","OTHER"],
        n, p=[.12,.09,.11,.08,.10,.09,.08,.12,.11,.10]
    )
    return pd.DataFrame({
        "BORO_NM": boros, "TYP_DESC": types,
        "hour_of_day": rng.integers(0, 24, n),
        "response_time_mins": rng.normal(8, 3, n).clip(1, 30),
        "slow_response": (p_slow > 0.5).astype(int),
        "p_slow": p_slow,
    })


# ── data loader ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=REFRESH_SECS)
def load_scored(scored_dir: str) -> pd.DataFrame:
    p = Path(scored_dir)
    if not p.exists():
        return pd.DataFrame()
    try:
        frames = []
        for folder in sorted(p.glob("BORO_NM=*")):
            boro = folder.name.split("=", 1)[1].upper().strip()
            if not list(folder.glob("*.parquet")):
                continue
            chunk = pd.read_parquet(folder)
            chunk["BORO_NM"] = boro
            frames.append(chunk)
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df["BORO_NM"] = df["BORO_NM"].str.upper().str.strip()
        return df
    except Exception as e:
        st.warning(f"Data load error: {e}")
        return pd.DataFrame()


# ── MAP — hero chart, full width ──────────────────────────────────────────────
# ── MAP — replace your entire chart_map function with this ────────────────────
def chart_map(boro_df: pd.DataFrame):

    st.markdown('<div class="section-header">🗺️ NYC Borough Risk Map — Live Predictions</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="chart-caption">'
        'Each borough shaded by predicted slow-response risk. '
        '<span style="color:#e63946">■ Red ≥50% — High</span> &nbsp;'
        '<span style="color:#f4a261">■ Orange 40–50% — Medium</span> &nbsp;'
        '<span style="color:#2dc653">■ Green &lt;40% — Low</span>. '
        'Hover for details.'
        '</div>', unsafe_allow_html=True
    )

    # Embedded NYC borough GeoJSON — no network call needed
    NYC_GEOJSON = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"boro_nm": "MANHATTAN"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [-74.0479,40.6829],[-74.0200,40.6996],[-73.9744,40.7026],
                    [-73.9715,40.7081],[-73.9438,40.7178],[-73.9335,40.7298],
                    [-73.9271,40.7402],[-73.9236,40.7508],[-73.9334,40.7826],
                    [-73.9395,40.8029],[-73.9472,40.8265],[-73.9344,40.8573],
                    [-73.9111,40.8780],[-73.9126,40.8682],[-73.9191,40.8585],
                    [-73.9321,40.8502],[-73.9248,40.8210],[-73.9325,40.7720],
                    [-73.9750,40.6960],[-74.0200,40.6996],[-74.0479,40.6829],
                ]]}
            },
            {
                "type": "Feature",
                "properties": {"boro_nm": "BROOKLYN"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [-74.0420,40.6791],[-74.0200,40.6996],[-73.9744,40.7026],
                    [-73.9620,40.7140],[-73.9536,40.7072],[-73.9313,40.6950],
                    [-73.9100,40.6956],[-73.8842,40.6734],[-73.8826,40.6494],
                    [-73.8891,40.6265],[-73.9044,40.5982],[-73.9312,40.5773],
                    [-73.9558,40.5773],[-73.9840,40.5930],[-74.0150,40.6395],
                    [-74.0420,40.6791],
                ]]}
            },
            {
                "type": "Feature",
                "properties": {"boro_nm": "QUEENS"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [-73.9620,40.7140],[-73.9536,40.7072],[-73.9313,40.6950],
                    [-73.9100,40.6956],[-73.8842,40.6734],[-73.8626,40.6526],
                    [-73.8330,40.6598],[-73.7700,40.5900],[-73.7000,40.5900],
                    [-73.7000,40.7900],[-73.7300,40.8100],[-73.7900,40.7950],
                    [-73.8500,40.7820],[-73.9100,40.7600],[-73.9535,40.7500],
                    [-73.9620,40.7140],
                ]]}
            },
            {
                "type": "Feature",
                "properties": {"boro_nm": "BRONX"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [-73.9111,40.8780],[-73.9126,40.8682],[-73.9191,40.8585],
                    [-73.9321,40.8502],[-73.9344,40.8573],[-73.9472,40.8265],
                    [-73.9395,40.8029],[-73.9335,40.7826],[-73.9100,40.7600],
                    [-73.8500,40.7820],[-73.8330,40.8000],[-73.8100,40.8400],
                    [-73.7800,40.8760],[-73.7950,40.8780],[-73.8290,40.8980],
                    [-73.8450,40.9000],[-73.8680,40.9000],[-73.8950,40.8900],
                    [-73.9111,40.8780],
                ]]}
            },
            {
                "type": "Feature",
                "properties": {"boro_nm": "STATEN ISLAND"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [-74.2591,40.4960],[-74.1950,40.5100],[-74.0550,40.5700],
                    [-74.0400,40.5900],[-74.0550,40.6500],[-74.1200,40.6480],
                    [-74.1800,40.6370],[-74.2490,40.6010],[-74.2591,40.5500],
                    [-74.2591,40.4960],
                ]]}
            },
        ]
    }

    risk_lookup = {
        row["BORO_NM"]: {
            "pct":    round(row["avg_p_slow"] * 100, 1),
            "actual": round(row["actual_slow"] * 100, 1),
            "calls":  int(row["calls"]),
        }
        for _, row in boro_df.iterrows()
    }

    boros      = [f["properties"]["boro_nm"] for f in NYC_GEOJSON["features"]]
    z_vals     = [risk_lookup.get(b, {"pct": 0})["pct"] for b in boros]
    customdata = [
        [risk_lookup.get(b, {"pct":0,"actual":0,"calls":0})[k] for k in ["pct","actual","calls"]]
        for b in boros
    ]

    fig = go.Figure(go.Choroplethmapbox(
        geojson=NYC_GEOJSON,
        locations=boros,
        featureidkey="properties.boro_nm",
        z=z_vals,
        colorscale=[
            [0.00, "rgba(45,198,83,0.75)"],
            [0.39, "rgba(45,198,83,0.75)"],
            [0.40, "rgba(244,162,97,0.75)"],
            [0.49, "rgba(244,162,97,0.75)"],
            [0.50, "rgba(230,57,70,0.75)"],
            [1.00, "rgba(230,57,70,0.75)"],
        ],
        zmin=0, zmax=100,
        marker_opacity=0.80,
        marker_line_width=2,
        marker_line_color="#ffffff",
        colorbar=dict(
            title=dict(text="Slow %", font=dict(color="#c8d8e8")),
            tickvals=[10, 40, 50, 80],
            ticktext=["Low","40%","50%","High"],
            tickfont=dict(color="#c8d8e8"),
            bgcolor="rgba(13,27,42,0.85)",
            bordercolor="#1e3a5f",
            borderwidth=1,
            len=0.6,
        ),
        customdata=customdata,
        hovertemplate=(
            "<b>%{location}</b><br>"
            "Predicted slow: <b>%{customdata[0]}%</b><br>"
            "Actual slow: %{customdata[1]}%<br>"
            "Calls scored: %{customdata[2]}"
            "<extra></extra>"
        ),
    ))

    fig.update_layout(
        mapbox=dict(
            style="carto-darkmatter",
            center=dict(lat=40.660, lon=-73.944),
            zoom=9.8,
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=0, b=0),
        height=520,
    )
    st.plotly_chart(fig, use_container_width=True)


# ── bar chart — predicted vs actual ──────────────────────────────────────────
def chart_borough_risk(boro_df: pd.DataFrame):
    st.markdown('<div class="section-header">📊 Predicted vs Actual Slow-Response Rate</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="chart-caption">'
        'Bars = model prediction. ◆ Diamond = real observed rate. '
        'A big gap means the model is over or under-estimating for that borough.'
        '</div>', unsafe_allow_html=True
    )
    boros   = boro_df["BORO_NM"].tolist()
    preds   = (boro_df["avg_p_slow"] * 100).round(1).tolist()
    actual  = (boro_df["actual_slow"] * 100).round(1).tolist()
    colours = [risk_colour(p) for p in preds]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=boros, x=preds, orientation="h",
        name="Predicted slow %",
        marker_color=colours,
        text=[f"{p}%" for p in preds], textposition="outside",
        textfont=dict(color="#f0a500", size=12, family="IBM Plex Mono"),
    ))
    fig.add_trace(go.Scatter(
        y=boros, x=actual, mode="markers",
        name="Actual slow %",
        marker=dict(symbol="diamond", size=12, color="#7ec8e3",
                    line=dict(width=1.5, color="#ffffff")),
    ))
    fig.update_layout(**dark_layout(
        height=280, barmode="overlay",
        legend=dict(orientation="h", y=1.08, x=0),
        xaxis=dict(title="% of calls", gridcolor="#1e3a5f", range=[0, 80]),
        yaxis=dict(gridcolor="#1e3a5f", categoryorder="total ascending"),
    ))
    st.plotly_chart(fig, use_container_width=True)


# ── incident types ────────────────────────────────────────────────────────────
def chart_incident_types(df: pd.DataFrame):
    st.markdown('<div class="section-header">🚔 Top Incident Types Right Now</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="chart-caption">'
        'What 911 calls are coming in live. High-acuity types at night '
        '(SHOTS FIRED, ASSAULT) tend to have the slowest responses.'
        '</div>', unsafe_allow_html=True
    )
    top = df["TYP_DESC"].value_counts().head(10)
    fig = go.Figure(go.Bar(
        x=top.values, y=top.index, orientation="h",
        marker=dict(color=list(range(len(top))),
                    colorscale=[[0,"#1e3a5f"],[1,"#f0a500"]], showscale=False),
        text=top.values, textposition="outside",
        textfont=dict(color="#c8d8e8", size=11),
    ))
    fig.update_layout(**dark_layout(
        height=280,
        xaxis=dict(title="Incidents", gridcolor="#1e3a5f"),
        yaxis=dict(gridcolor="rgba(0,0,0,0)"),
    ))
    st.plotly_chart(fig, use_container_width=True)


# ── hourly volume ─────────────────────────────────────────────────────────────
def chart_hourly_volume(df: pd.DataFrame):
    st.markdown('<div class="section-header">🕐 Call Volume by Hour of Day</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="chart-caption">'
        'When calls peak. Red shading = night hours when fewer units are staffed — '
        'these hours have higher slow-response rates.'
        '</div>', unsafe_allow_html=True
    )
    hourly = df.groupby("hour_of_day").size().reindex(range(24), fill_value=0)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(hourly.index), y=hourly.values,
        fill="tozeroy",
        line=dict(color="#f0a500", width=2),
        fillcolor="rgba(240,165,0,0.12)",
        mode="lines+markers",
        marker=dict(size=4, color="#f0a500"),
    ))
    for x0, x1 in [(0, 6), (22, 24)]:
        fig.add_vrect(x0=x0, x1=x1, fillcolor="rgba(230,57,70,0.12)",
                      layer="below", line_width=0)
    fig.update_layout(**dark_layout(
        height=220,
        xaxis=dict(title="Hour (0=midnight, 12=noon)", gridcolor="#1e3a5f", dtick=2),
        yaxis=dict(title="Incidents", gridcolor="#1e3a5f"),
    ))
    st.plotly_chart(fig, use_container_width=True)


# ── alerts ────────────────────────────────────────────────────────────────────
def render_alerts(boro_df: pd.DataFrame):
    st.markdown('<div class="section-header">⚡ Live Dispatch Alerts</div>', unsafe_allow_html=True)
    high = boro_df[boro_df["avg_p_slow"] * 100 >= 50]
    med  = boro_df[(boro_df["avg_p_slow"] * 100 >= 40) & (boro_df["avg_p_slow"] * 100 < 50)]
    if high.empty and med.empty:
        st.markdown('<div class="ok-box">✅ All boroughs below 40% risk. System normal.</div>', unsafe_allow_html=True)
    for _, r in high.iterrows():
        pct = round(r["avg_p_slow"]*100,1); act = round(r["actual_slow"]*100,1)
        st.markdown(
            f'<div class="alert-box">⚠️ <b>{r["BORO_NM"]}</b><br>'
            f'Predicted slow: <b>{pct}%</b> · Actual: {act}%<br>'
            f'<small>→ Pre-position units in this borough</small></div>',
            unsafe_allow_html=True
        )
    for _, r in med.iterrows():
        pct = round(r["avg_p_slow"]*100,1)
        st.markdown(
            f'<div class="warn-box">⚡ <b>{r["BORO_NM"]}</b> — elevated at {pct}%. Monitor.</div>',
            unsafe_allow_html=True
        )


# ── main ──────────────────────────────────────────────────────────────────────
def main(scored_dir: str, demo: bool):

    c1, c2 = st.columns([5, 1])
    with c1:
        st.markdown("# 🚨 NYC 911 Live Risk Dashboard")
        st.markdown("*Which borough will have the slowest emergency response? Updated every 10 seconds.*")
    with c2:
        st.markdown(f"<br><small style='color:#4a7fa5'>🔄 Auto-refresh {REFRESH_SECS}s</small>",
                    unsafe_allow_html=True)

    if demo:
        df = make_demo_data()
        st.info("📊 DEMO MODE — synthetic data. Use `--scored /tmp/nyc911_scored` for live data.")
    else:
        df = load_scored(scored_dir)

    if df.empty:
        st.warning(
            "⏳ No scored data yet. Steps to fix:\n"
            "1. Make sure your streaming job is writing Parquet files\n"
            "2. If using Docker, mount a shared volume and set `--output /scored` in the streaming consumer\n"
            "3. Point the dashboard at the same folder using `--scored /scored`"
        )
        time.sleep(REFRESH_SECS)
        st.rerun()
        return

    if len(df) < 200:
        st.markdown(
            f'<div class="warn-box">⚠️ Only <b>{len(df)} rows</b> — predictions may cluster together. '
            f'Let more data accumulate or restart the consumer with <code>startingOffsets: earliest</code>.'
            f'</div>', unsafe_allow_html=True
        )

    boro_df = (
        df.groupby("BORO_NM")
        .agg(calls=("BORO_NM","count"), avg_p_slow=("p_slow","mean"),
             actual_slow=("slow_response","mean"), avg_resp=("response_time_mins","mean"))
        .reset_index()
        .sort_values("avg_p_slow", ascending=False)
    )
    boro_df["risk_label"] = (boro_df["avg_p_slow"] * 100).apply(risk_label)

    # KPIs
    total = len(df)
    avg_rk = df["p_slow"].mean() * 100
    avg_rt = df["response_time_mins"].mean() if "response_time_mins" in df.columns else float("nan")
    bronx  = boro_df.loc[boro_df["BORO_NM"] == "BRONX", "avg_p_slow"].values
    bronx_pct = f"{round(bronx[0]*100,1)}%" if len(bronx) else "N/A"

    k1, k2, k3, k4 = st.columns(4)
    for col, val, lbl in zip(
        [k1,k2,k3,k4],
        [f"{total:,}", f"{avg_rk:.1f}%",
         f"{avg_rt:.1f} min" if not pd.isna(avg_rt) else "N/A", bronx_pct],
        ["Incidents Scored","Avg Predicted Risk","Avg Response Time","Bronx Risk"],
    ):
        with col:
            st.markdown(
                f'<div class="metric-card"><div class="metric-value">{val}</div>'
                f'<div class="metric-label">{lbl}</div></div>',
                unsafe_allow_html=True
            )

    st.markdown("---")

    # MAP — full width hero
    chart_map(boro_df)

    st.markdown("---")

    # Table + alerts side by side
    t_col, a_col = st.columns([3, 2])
    with t_col:
        st.markdown('<div class="section-header">📋 Borough Summary</div>', unsafe_allow_html=True)
        st.markdown('<div class="chart-caption">Predicted = model output · Actual = real observed rate · Gap = model error</div>', unsafe_allow_html=True)
        disp = boro_df[["BORO_NM","calls","avg_p_slow","actual_slow","avg_resp","risk_label"]].copy()
        disp.columns = ["Borough","Calls","Predicted Slow %","Actual Slow %","Avg Resp (min)","Risk"]
        disp["Predicted Slow %"] = (disp["Predicted Slow %"]*100).round(1)
        disp["Actual Slow %"]    = (disp["Actual Slow %"]*100).round(1)
        disp["Avg Resp (min)"]   = disp["Avg Resp (min)"].round(1)
        st.dataframe(disp, use_container_width=True, hide_index=True)
    with a_col:
        render_alerts(boro_df)

    st.markdown("---")

    b1, b2 = st.columns(2)
    with b1:
        chart_borough_risk(boro_df)
    with b2:
        chart_incident_types(df)

    st.markdown("---")
    chart_hourly_volume(df)

    st.markdown("---")
    st.markdown('<div class="section-header">🔧 Pipeline Status</div>', unsafe_allow_html=True)
    p1,p2,p3,p4,p5 = st.columns(5)
    for col, lbl, detail in zip(
        [p1,p2,p3,p4,p5],
        ["📥 Kafka","⚡ Spark Stream","🤖 GBT Model","💾 Parquet Sink","📊 Dashboard"],
        ["nyc911-incidents","local[2] · 10s","AUC-ROC 0.567","by BORO_NM",f"refresh {REFRESH_SECS}s"],
    ):
        col.success(f"**{lbl}**\n\n{detail}")

    time.sleep(REFRESH_SECS)
    st.rerun()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--scored", default=os.environ.get("SCORED_DIR", "/scored"))
    p.add_argument("--demo",   action="store_true")
    args = p.parse_args()
    main(args.scored, args.demo)
