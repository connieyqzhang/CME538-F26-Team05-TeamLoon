"""Bus delay EDA (pre-2025): interactive dashboard of TTC bus delays 2014–2024.

Page of the multi-page app started by streamlit_app.py. Reads data/eda_delays.csv.gz and
data/eda_delays_geo.csv.gz, which prepare_bus_eda_data.py makes from the pre-2025 CSVs.
"""
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

DATA = Path(__file__).resolve().parent / "data"
DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NOTEBOOK_INCIDENTS = ["General Delay", "Diversion", "Operations"]  # subset used in the EDA notebook
TORONTO = dict(lat=43.70, lon=-79.40)
MAP_POINTS = 25_000  # map is sampled so it stays responsive


# ---------------------------------------------------------------- data
@st.cache_data(show_spinner="Loading delay data…")
def load(name: str) -> pd.DataFrame:
    d = pd.read_csv(DATA / name, dtype={"Route": str}, low_memory=False)
    d["Datetime"] = pd.to_datetime(d["Datetime"])
    d["Year"] = d["Datetime"].dt.year
    d["Hour"] = d["Datetime"].dt.hour
    d["Day"] = pd.Categorical(d["Datetime"].dt.day_name(), categories=DAY_ORDER)
    d["YearMonth"] = d["Datetime"].dt.to_period("M").dt.to_timestamp()
    d["Incident"] = d["Incident"].astype("category")
    return d


def filter_frame(d, years, incidents, days, routes, max_delay):
    m = (d["Year"].between(*years)
         & d["Incident"].isin(incidents)
         & d["Day"].isin(days)
         & d["Min Delay"].between(0, max_delay))  # drops negative / absurd delays
    if routes:
        m &= d["Route"].isin(routes)
    return d[m]


def route_key(r: str):
    return (0, int(r), "") if r.isdigit() else (1, 0, r)


if not (DATA / "eda_delays.csv.gz").exists() or not (DATA / "eda_delays_geo.csv.gz").exists():
    st.title("🚌 TTC bus delays, 2014–2024")
    st.info("This page needs `data/eda_delays.csv.gz` and `data/eda_delays_geo.csv.gz`. Make them with "
            "`python prepare_bus_eda_data.py <folder with the pre-2025 CSVs>` and upload them to the `data` folder.")
    st.stop()

df = load("eda_delays.csv.gz")
geo = load("eda_delays_geo.csv.gz")
ALL_INCIDENTS = df["Incident"].value_counts().index.tolist()
ALL_ROUTES = sorted(df["Route"].dropna().unique(), key=route_key)
Y0, Y1 = int(df["Year"].min()), int(df["Year"].max())

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Filters")
    years = st.slider("Years", Y0, Y1, (Y0, Y1), key="eda_years")
    preset = st.radio("Incident types", ["All", "Notebook subset", "Choose…"], horizontal=True,
                      help="Notebook subset = General Delay, Diversion, Operations", key="eda_preset")
    if preset == "All":
        incidents = ALL_INCIDENTS
    elif preset == "Notebook subset":
        incidents = [i for i in NOTEBOOK_INCIDENTS if i in ALL_INCIDENTS]
    else:
        incidents = st.multiselect("Pick incident types", ALL_INCIDENTS, default=ALL_INCIDENTS[:5], key="eda_pick")
    days = st.multiselect("Days of week", DAY_ORDER, default=DAY_ORDER, key="eda_days")
    routes = st.multiselect("Routes", ALL_ROUTES, placeholder="All routes", key="eda_routes")
    max_delay = st.slider("Max delay (minutes)", 10, 1440, 240, step=10,
                          help="Drops data-entry outliers (some rows record delays of 100,000+ minutes).",
                          key="eda_maxdelay")
    st.caption("Source: City of Toronto Open Data — TTC Bus Delay Data, 2014–2024.")

args = (years, incidents, days, routes, max_delay)
f = filter_frame(df, *args)

# ---------------------------------------------------------------- header + KPIs
st.title("🚌 TTC bus delays, 2014–2024")
if f.empty:
    st.warning("No incidents match these filters.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Incidents", f"{len(f):,}")
c2.metric("Total delay", f"{f['Min Delay'].sum() / 60:,.0f} h")
c3.metric("Median delay", f"{f['Min Delay'].median():.0f} min")
c4.metric("Delays ≥ 30 min", f"{(f['Min Delay'] >= 30).mean():.1%}")


def tidy(fig, height):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=50, b=10))
    return fig


t_trend, t_inc, t_time, t_route, t_map, t_data = st.tabs(
    ["📈 Trends", "🏷️ Incident types", "🕒 Time of day", "🚏 Routes & stations", "🗺️ Map", "📄 Data"])

# ---------------------------------------------------------------- trends
with t_trend:
    m = f.groupby("YearMonth")["Min Delay"].agg(incidents="size", median_delay="median").reset_index()
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=m["YearMonth"], y=m["incidents"], name="Incidents", marker_color="#4C78A8"),
                  secondary_y=False)
    fig.add_trace(go.Scatter(x=m["YearMonth"], y=m["median_delay"], name="Median delay (min)", mode="lines",
                             line=dict(color="#E45756", width=2)), secondary_y=True)
    fig.update_yaxes(title_text="Incidents per month", secondary_y=False)
    fig.update_yaxes(title_text="Median delay (min)", secondary_y=True)
    fig.update_layout(title="Monthly incidents and median delay", hovermode="x unified",
                      legend=dict(orientation="h", y=1.08))
    st.plotly_chart(tidy(fig, 450))

# ---------------------------------------------------------------- incident types
with t_inc:
    b = (f.groupby("Incident", observed=True)["Min Delay"]
           .agg(incidents="size", median_delay="median", total_hours=lambda s: s.sum() / 60)
           .reset_index().sort_values("incidents"))
    fig = px.bar(b, x="incidents", y="Incident", orientation="h", color="median_delay",
                 color_continuous_scale="YlOrRd", hover_data={"total_hours": ":,.0f"},
                 labels={"incidents": "Incidents", "median_delay": "Median delay (min)",
                         "total_hours": "Total delay (h)", "Incident": ""},
                 title="Incidents by type (colour = median delay)")
    st.plotly_chart(tidy(fig, max(350, 28 * len(b))))

# ---------------------------------------------------------------- time of day
with t_time:
    h = (f.pivot_table(index="Day", columns="Hour", values="Min Delay", aggfunc="size", observed=False)
           .reindex(index=DAY_ORDER, columns=range(24)).fillna(0))
    fig = px.imshow(h, aspect="auto", color_continuous_scale="YlOrRd",
                    labels=dict(x="Hour of day", y="", color="Incidents"),
                    title="When incidents happen (day × hour)")
    fig.update_xaxes(dtick=1)
    st.plotly_chart(tidy(fig, 420))

# ---------------------------------------------------------------- routes & stations
with t_route:
    n = st.slider("How many to show", 5, 30, 15, key="eda_topn")
    left, right = st.columns(2)
    r = (f.groupby("Route")["Min Delay"]
           .agg(incidents="size", median_delay="median", total_hours=lambda s: s.sum() / 60)
           .nlargest(n, "incidents").reset_index().sort_values("incidents"))
    r["Route"] = "Route " + r["Route"]
    fig = px.bar(r, x="incidents", y="Route", orientation="h", color="median_delay",
                 color_continuous_scale="YlOrRd", hover_data={"total_hours": ":,.0f"},
                 labels={"incidents": "Incidents", "median_delay": "Median delay (min)",
                         "total_hours": "Total delay (h)", "Route": ""},
                 title=f"Top {n} routes by incidents")
    left.plotly_chart(tidy(fig, 30 * n + 120))

    s = (f.groupby("Station")["Min Delay"].agg(incidents="size", median_delay="median")
           .nlargest(n, "incidents").reset_index().sort_values("incidents"))
    fig = px.bar(s, x="incidents", y="Station", orientation="h", color="median_delay",
                 color_continuous_scale="YlOrRd",
                 labels={"incidents": "Incidents", "median_delay": "Median delay (min)", "Station": ""},
                 title=f"Top {n} locations by incidents")
    right.plotly_chart(tidy(fig, 30 * n + 120))

# ---------------------------------------------------------------- map
with t_map:
    g = filter_frame(geo, *args)
    if g.empty:
        st.info("No geocoded incidents match these filters.")
    else:
        pts = g.sample(min(len(g), MAP_POINTS), random_state=0)
        st.caption(f"Showing {len(pts):,} of {len(g):,} geocoded incidents (random sample).")
        kw = dict(lat="Latitude", lon="Longitude", radius=7, center=TORONTO, zoom=9.6,
                  hover_data={"Route": True, "Station": True, "Incident": True, "Min Delay": True,
                              "Latitude": False, "Longitude": False})
        if hasattr(px, "density_map"):  # plotly >= 5.24 (MapLibre, no token needed)
            fig = px.density_map(pts, map_style="open-street-map", **kw)
        else:
            fig = px.density_mapbox(pts, mapbox_style="open-street-map", **kw)
        st.plotly_chart(tidy(fig, 640))

# ---------------------------------------------------------------- data
with t_data:
    cols = ["Datetime", "Route", "Station", "Incident", "Min Delay"]
    st.dataframe(f[cols].sort_values("Datetime", ascending=False).head(1000), hide_index=True)
    st.caption(f"Newest {min(len(f), 1000):,} of {len(f):,} matching rows.")
    if st.button("Prepare CSV download", key="eda_prep_csv"):
        st.download_button("Download filtered data (CSV)", f[cols].to_csv(index=False).encode(),
                           "ttc_bus_delays_filtered.csv", "text/csv")
