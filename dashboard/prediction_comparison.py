"""
Predicted vs actual weather-related bus delays - page of the app started by streamlit_app.py.

Compares the model's per-station, per-day delay predictions (data/predictions.csv) with the delays that
actually happened, day by day on the precipitation map, and reports how accurate and precise the model is.

data/predictions.csv columns: Station, Date, geometry ("POINT (lon lat)"), Min Delay (actual minutes),
Predicted Delay (predicted minutes). "Hour Delay" may also be present and is ignored.
"""
import json
from html import escape
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

DATA = Path(__file__).resolve().parent / "data"
WINTER_MONTHS = [11, 12, 1, 2, 3]
LAT0 = 43.7
M_LAT = 110574.0
M_LON = 111320.0 * np.cos(np.radians(LAT0))
PRECIP_RAMP = ["#edf8b1", "#c7e9b4", "#7fcdbb", "#41b6c4", "#1d91c0", "#225ea8", "#0c2c84"]
ACTUAL = "#e8590c"      # same orange as the delay circles on the precipitation page
PREDICTED = "#7b3fbf"   # purple, distinct from the precipitation blues
UNDER, OVER = "#c0392b", "#2c6fbb"
PRECIP_GROUP_BINS = [-np.inf, 0.2, 2, 5, 10, 25, np.inf]
PRECIP_GROUP_LABELS = ["Dry (under 0.2 mm)", "0.2–2 mm", "2–5 mm", "5–10 mm", "10–25 mm", "25 mm or more"]
SIZE_CAP = 600  # minutes at which a circle reaches full size (same rule as the precipitation page)
TORONTO_BOUNDS = [[43.585, -79.635], [43.855, -79.115]]


# --------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------
@st.cache_data
def load_predictions():
    p = pd.read_csv(DATA / "predictions.csv")
    xy = p["geometry"].astype(str).str.extract(r"POINT\s*\(\s*([-\d.eE]+)\s+([-\d.eE]+)\s*\)").astype(float)
    out = pd.DataFrame({
        "date": pd.to_datetime(p["Date"]),
        "station": p["Station"].astype(str).str.strip(),
        "longitude": xy[0], "latitude": xy[1],
        "actual": pd.to_numeric(p["Min Delay"], errors="coerce"),
        "predicted": pd.to_numeric(p["Predicted Delay"], errors="coerce"),
    }).dropna()
    out["error"] = out["predicted"] - out["actual"]
    return out.reset_index(drop=True)


@st.cache_data
def load_precip():
    precip = pd.read_csv(DATA / "daily_precipitation.csv", parse_dates=["date"], index_col="date").sort_index()
    gauges = pd.read_csv(DATA / "rain_gauges.csv")
    precip = precip.reindex(columns=gauges["name"])
    grid = pd.read_csv(DATA / "city_grid.csv")
    neighbourhoods = json.loads((DATA / "neighbourhoods.json").read_text(encoding="utf-8"))
    return precip, gauges, grid, neighbourhoods


def apply_season_rule(precip, gauges, winter_rule):
    counted = precip.copy()
    if winter_rule:
        winter = counted.index.month.isin(WINTER_MONTHS)
        counted.loc[winter, gauges.loc[gauges["season"] == "3-season", "name"]] = np.nan
    return counted


def to_xy(lat, lon):
    return np.column_stack([np.asarray(lon) * M_LON, np.asarray(lat) * M_LAT])


def idw_estimate(distances, values, power=2.0):
    values = np.asarray(values, dtype=float)
    observed = ~np.isnan(values)
    if not observed.any():
        return np.full(distances.shape[0], np.nan)
    d, v = distances[:, observed], values[observed]
    with np.errstate(divide="ignore", invalid="ignore"):
        w = 1.0 / np.power(d, power)
        estimate = (w @ v) / w.sum(axis=1)
    on_gauge = d < 1.0
    hit = on_gauge.any(axis=1)
    estimate[hit] = v[on_gauge.argmax(axis=1)[hit]]
    return estimate


@st.cache_resource
def build_geometry():
    _, gauges, grid, neighbourhoods = load_precip()
    cell_xy = to_xy(grid["latitude"], grid["longitude"])
    gauge_xy = to_xy(gauges["latitude"], gauges["longitude"])
    cell_features = []
    for c in grid.itertuples(index=False):
        s, n = round(c.latitude - c.half_height_deg, 5), round(c.latitude + c.half_height_deg, 5)
        w, e = round(c.longitude - c.half_width_deg, 5), round(c.longitude + c.half_width_deg, 5)
        cell_features.append({"type": "Feature", "properties": {"neighbourhood": c.neighbourhood},
                              "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}})
    nb_features = [{"type": "Feature", "properties": {"neighbourhood": n["neighbourhood"]},
                    "geometry": {"type": "Polygon", "coordinates": n["rings"]}} for n in neighbourhoods]
    cell_to_gauge = np.sqrt(((cell_xy[:, None, :] - gauge_xy[None, :, :]) ** 2).sum(axis=2))
    return {"cell_to_gauge": cell_to_gauge, "cell_features": cell_features, "nb_features": nb_features}


# --------------------------------------------------------------------------------------
# Accuracy statistics
# --------------------------------------------------------------------------------------
def regression_stats(actual, predicted, tolerance):
    a, p = np.asarray(actual, float), np.asarray(predicted, float)
    e = p - a
    n = len(e)
    if n == 0:
        return None
    ss_tot = ((a - a.mean()) ** 2).sum()
    return {
        "n": n,
        "mae": np.abs(e).mean(),
        "rmse": np.sqrt((e ** 2).mean()),
        "median_ae": np.median(np.abs(e)),
        "bias": e.mean(),
        "r2": 1 - (e ** 2).sum() / ss_tot if ss_tot > 0 else np.nan,
        "r": np.corrcoef(a, p)[0, 1] if n > 1 and a.std() > 0 and p.std() > 0 else np.nan,
        "sd": e.std(ddof=1) if n > 1 else np.nan,
        "p80": np.percentile(np.abs(e), 80),
        "within": (np.abs(e) <= tolerance).mean(),
        "total_actual": a.sum(), "total_predicted": p.sum(),
    }


def classification_stats(actual, predicted, threshold):
    yes_actual = np.asarray(actual) > 0
    yes_pred = np.asarray(predicted) >= threshold
    tp = int((yes_actual & yes_pred).sum()); fp = int((~yes_actual & yes_pred).sum())
    fn = int((yes_actual & ~yes_pred).sum()); tn = int((~yes_actual & ~yes_pred).sum())
    precision = tp / (tp + fp) if tp + fp else np.nan
    recall = tp / (tp + fn) if tp + fn else np.nan
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else np.nan
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "accuracy": (tp + tn) / max(tp + fp + fn + tn, 1),
            "precision": precision, "recall": recall, "f1": f1}


def fmt_min(v, signed=False):
    if v is None or not np.isfinite(v):
        return "—"
    return f"{v:+,.0f} min" if signed else f"{v:,.0f} min"


def fmt_pct(v):
    return "—" if v is None or not np.isfinite(v) else f"{v:.0%}"


# --------------------------------------------------------------------------------------
# Maps
# --------------------------------------------------------------------------------------
def radius(minutes):
    return float(3 + 11 * np.sqrt(min(max(minutes, 0), SIZE_CAP) / SIZE_CAP))


def add_surface(fmap, geo, estimates, colours, low, high):
    import folium
    from folium.features import GeoJsonTooltip

    def fill(v):
        return "#8c8c8c" if v is None or not np.isfinite(v) else colours(min(max(v, low), high))

    cells = [{**f, "properties": {**f["properties"],
                                  "precipitation_mm": None if not np.isfinite(v) else round(float(v), 1)}}
             for f, v in zip(geo["cell_features"], estimates)]
    folium.GeoJson({"type": "FeatureCollection", "features": cells}, name="IDW precipitation surface",
                   style_function=lambda f: {"fillColor": fill(f["properties"]["precipitation_mm"]), "color": None,
                                             "weight": 0, "fillOpacity": 0.55},
                   tooltip=GeoJsonTooltip(fields=["neighbourhood", "precipitation_mm"],
                                          aliases=["Neighbourhood", "IDW precipitation (mm)"])).add_to(fmap)
    folium.GeoJson({"type": "FeatureCollection", "features": geo["nb_features"]}, name="Neighbourhood outlines",
                   style_function=lambda f: {"fillOpacity": 0, "color": "#555555", "weight": 0.5}).add_to(fmap)


def popup_html(r):
    return (f"<b>{escape(r.station)}</b><br>Actual delay: {r.actual:,.0f} min<br>"
            f"Predicted delay: {r.predicted:,.0f} min<br>Error (predicted − actual): {r.error:+,.0f} min")


def add_delay_circles(layer, day_rows, column, colour):
    import folium
    for r in day_rows.sort_values(column, ascending=False).itertuples():
        v = getattr(r, column)
        tip = f"{r.station}: actual {r.actual:,.0f} · predicted {r.predicted:,.0f} min"
        if v <= 0:  # station in the data but 0 minutes on this side: small grey dot
            folium.CircleMarker([r.latitude, r.longitude], radius=2.5, color="#666666", weight=1, fill=True,
                                fill_color="#ffffff", fill_opacity=1, tooltip=tip,
                                popup=folium.Popup(popup_html(r), max_width=260)).add_to(layer)
        else:
            folium.CircleMarker([r.latitude, r.longitude], radius=radius(v), color="white", weight=1, fill=True,
                                fill_color=colour, fill_opacity=0.85, tooltip=tip,
                                popup=folium.Popup(popup_html(r), max_width=260)).add_to(layer)


def precip_colours(estimates, fit_to_day, fixed_max):
    from branca.colormap import LinearColormap
    finite = estimates[np.isfinite(estimates)]
    if fit_to_day and finite.size:
        low, high = float(finite.min()), float(finite.max())
        caption = "Precipitation, mm (fit to day)"
    else:
        low, high = 0.0, float(fixed_max)
        caption = f"Precipitation, mm (darkest = {fixed_max:g}+)"
    if high <= low:
        high = low + 0.1
    colours = LinearColormap(PRECIP_RAMP, vmin=low, vmax=high)
    colours.caption = caption
    return colours, low, high


def side_by_side_map(day_rows, geo, estimates, fit_to_day, fixed_max):
    import folium
    from folium.plugins import DualMap, Fullscreen

    colours, low, high = precip_colours(estimates, fit_to_day, fixed_max)
    m = DualMap(location=[43.72, -79.38], zoom_start=11, tiles="OpenStreetMap", control_scale=True, zoom_snap=0.25)
    for side, column, colour, label in [(m.m1, "actual", ACTUAL, "Actual delays"),
                                        (m.m2, "predicted", PREDICTED, "Predicted delays")]:
        add_surface(side, geo, estimates, colours, low, high)
        layer = folium.FeatureGroup(name=label).add_to(side)
        add_delay_circles(layer, day_rows, column, colour)
        Fullscreen().add_to(side)
        side.fit_bounds(TORONTO_BOUNDS)
    colours.add_to(m.m1)
    folium.LayerControl(collapsed=True).add_to(m)
    return m


def error_map(day_rows, geo, estimates, fit_to_day, fixed_max):
    import folium
    from branca.colormap import LinearColormap
    from folium.plugins import Fullscreen

    colours, low, high = precip_colours(estimates, fit_to_day, fixed_max)
    m = folium.Map(location=[43.72, -79.38], zoom_start=11, tiles="OpenStreetMap", control_scale=True, zoom_snap=0.25)
    Fullscreen().add_to(m)
    add_surface(m, geo, estimates, colours, low, high)
    span = max(30.0, float(np.percentile(np.abs(day_rows["error"]), 90))) if len(day_rows) else 60.0
    err_colours = LinearColormap([UNDER, "#f2f2f2", OVER], vmin=-span, vmax=span)
    err_colours.caption = "Error, min (red = too low, blue = too high)"
    layer = folium.FeatureGroup(name="Prediction error").add_to(m)
    for r in day_rows.reindex(day_rows["error"].abs().sort_values(ascending=False).index).itertuples():
        folium.CircleMarker([r.latitude, r.longitude], radius=radius(abs(r.error)), color="#333333", weight=0.8,
                            fill=True, fill_color=err_colours(min(max(r.error, -span), span)), fill_opacity=0.9,
                            tooltip=f"{r.station}: {r.error:+,.0f} min",
                            popup=folium.Popup(popup_html(r), max_width=260)).add_to(layer)
    colours.add_to(m)
    err_colours.add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    m.fit_bounds(TORONTO_BOUNDS)
    return m


def show_html_map(fmap, height=600):
    html = fmap.get_root().render()
    if hasattr(st, "iframe"):           # Streamlit 1.53+
        st.iframe(html, height=height)
    else:
        import streamlit.components.v1 as components
        components.html(html, height=height)


# --------------------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------------------
st.markdown("""
<style>
.block-container { padding-top: 2rem; }
div[data-testid="stMetric"] { background: var(--secondary-background-color); border-radius: 8px; padding: 0.8rem 1rem; }
div[data-testid="stMetricValue"] { font-family: Georgia, serif; font-weight: 400; }
.rd-note { opacity: 0.7; font-size: 0.9rem; margin-top: -0.4rem; }
.pc-key { display:inline-block; width:0.8rem; height:0.8rem; border-radius:50%; margin:0 0.3rem -0.1rem 0.6rem; }
</style>""", unsafe_allow_html=True)

st.title("Predicted vs actual weather-related delays")
if not (DATA / "predictions.csv").exists():
    st.info("This page needs `data/predictions.csv` (columns Station, Date, geometry, Min Delay, Predicted Delay).")
    st.stop()

preds = load_predictions()
precip, gauges, _, _ = load_precip()
geo = build_geometry()

with st.sidebar:
    st.header("Advanced settings")
    winter_rule = st.checkbox("Nov–Mar: count only the 4-season gauges", value=True, key="pc_winter",
                              help="Project assumption: the 3-season gauges are off in winter.")
    power = st.slider("IDW power (p)", 0.5, 5.0, 2.0, 0.5, key="pc_power", help="Higher = nearby gauges count more.")
    fixed_max = st.slider("Fixed scale maximum (mm)", 5, 60, 25, 5, key="pc_fixed_max")

counted = apply_season_rule(precip, gauges, winter_rule)
city_precip = counted.mean(axis=1)

daily = (preds.groupby("date")
         .agg(stations=("station", "size"), actual=("actual", "sum"), predicted=("predicted", "sum"),
              mae=("error", lambda e: e.abs().mean()))
         .reset_index())
daily["error"] = daily["predicted"] - daily["actual"]
daily["precip_mm"] = daily["date"].map(city_precip).round(1)
days = [d.date() for d in daily["date"]]

overall_station = regression_stats(preds["actual"], preds["predicted"], 15)
overall_daily = regression_stats(daily["actual"], daily["predicted"], 60)
st.markdown(
    f"The model's station-by-day predictions for **{len(preds):,} station-days** on **{len(daily):,} days** "
    f"({daily['date'].min():%b %Y} – {daily['date'].max():%b %Y}), compared with the weather-related delays that "
    f"actually happened. Daily totals track the actual delays with a correlation of **{overall_daily['r']:.2f}**, "
    f"and the model predicts **{overall_daily['total_predicted'] / overall_daily['total_actual']:.1f}×** the actual "
    f"delay minutes overall."
)


# ---- selected day ---------------------------------------------------------------------
def set_day(d):
    d = pd.Timestamp(d).date()
    if d not in days:  # snap to the nearest day with predictions
        d = min(days, key=lambda x: abs((pd.Timestamp(x) - pd.Timestamp(d)).days))
    st.session_state.pc_day = d


if st.session_state.get("pc_day") not in days:
    set_day(daily.loc[daily["actual"].idxmax(), "date"])


def step(direction):
    i = days.index(st.session_state.pc_day) + direction
    if 0 <= i < len(days):
        st.session_state.pc_day = days[i]


actual_by_day = dict(zip(days, daily["actual"]))
try:
    cols = st.columns([3, 1.2, 1.2, 2.2, 2], vertical_alignment="bottom")
except TypeError:
    cols = st.columns([3, 1.2, 1.2, 2.2, 2])
c_day, c_prev, c_next, c_view, c_scale = cols
c_day.selectbox("Day", days, key="pc_day",
                format_func=lambda d: f"{d:%a %b} {d.day}, {d.year}  ·  {actual_by_day[d]:,.0f} min actual")
c_prev.button("← Previous", on_click=step, args=(-1,), key="pc_prev")
c_next.button("Next →", on_click=step, args=(1,), key="pc_next")
view = c_view.radio("Map", ["Side by side", "Prediction error"], horizontal=True, key="pc_view")
scale = c_scale.radio("Precipitation scale", ["Fixed scale", "Fit to day"], horizontal=True, key="pc_scale")

day = pd.Timestamp(st.session_state.pc_day)
day_rows = preds[preds["date"] == day]
d = daily[daily["date"] == day].iloc[0]
values = counted.loc[day] if day in counted.index else pd.Series(np.nan, index=gauges["name"])
estimates = idw_estimate(geo["cell_to_gauge"], values.to_numpy(), power)
day_stats = regression_stats(day_rows["actual"], day_rows["predicted"], 15)

st.markdown(f'<p class="rd-note">Showing <b>{day:%A, %B} {day.day}, {day.year}</b></p>', unsafe_allow_html=True)
t1, t2, t3, t4, t5 = st.columns(5)
t1.metric("Actual delay minutes", f"{d['actual']:,.0f}", help="Sum over the stations that day")
t2.metric("Predicted delay minutes", f"{d['predicted']:,.0f}",
          f"{d['error']:+,.0f} min vs actual", delta_color="off")
t3.metric("Stations with a delay", f"{(day_rows['actual'] > 0).sum()} actual",
          f"{(day_rows['predicted'] >= 1).sum()} predicted", delta_color="off",
          help="Predicted = at least 1 minute predicted")
t4.metric("Mean error per station", fmt_min(day_stats["mae"]), help="Mean absolute error over that day's stations")
t5.metric("City average precipitation", "—" if pd.isna(d["precip_mm"]) else f"{d['precip_mm']:.1f} mm",
          help="Mean of the counted gauges (no rain gauge data before June 2015)")

with st.container(border=True):
    if view == "Side by side":
        st.markdown("#### Actual (left) and predicted (right) delays")
        st.markdown(f'<p class="rd-note">Both maps show the IDW precipitation surface for the day and move together. '
                    f'Circles are sized by delay minutes on the same scale '
                    f'(<span class="pc-key" style="background:{ACTUAL}"></span>actual, '
                    f'<span class="pc-key" style="background:{PREDICTED}"></span>predicted); small white dots are '
                    f"stations with 0 minutes on that side. Click a circle for both values.</p>", unsafe_allow_html=True)
        show_html_map(side_by_side_map(day_rows, geo, estimates, scale == "Fit to day", fixed_max))
    else:
        st.markdown("#### Prediction error by station")
        st.markdown('<p class="rd-note">Predicted minus actual delay at each station, on the day\'s precipitation surface. '
                    "Red = the model predicted too little, blue = too much; bigger circles = bigger errors.</p>",
                    unsafe_allow_html=True)
        show_html_map(error_map(day_rows, geo, estimates, scale == "Fit to day", fixed_max))


# ---- timeline -------------------------------------------------------------------------
def pick_from_timeline():
    event = st.session_state.get("pc_timeline")
    selection = getattr(event, "selection", None) or {}
    picks = list(selection.get("pc_pick") or [])
    if picks and "date" in picks[-1]:
        v = picks[-1]["date"]
        set_day(pd.to_datetime(v, unit="ms") if isinstance(v, (int, float)) else pd.to_datetime(v))


with st.container(border=True):
    st.markdown("#### Daily totals: actual vs predicted")
    st.markdown('<p class="rd-note">Total delay minutes on each day with predictions: bars are actual, dots are predicted. '
                "Click a bar to map that day.</p>",
                unsafe_allow_html=True)
    year_options = ["All years"] + sorted({d.year for d in days}, reverse=True)
    year = st.selectbox("Year", year_options, index=1 if len(year_options) > 1 else 0, key="pc_year",
                        label_visibility="collapsed")
    shown = daily if year == "All years" else daily[daily["date"].dt.year == year]
    long = shown.melt(id_vars=["date", "precip_mm", "stations"], value_vars=["actual", "predicted"],
                      var_name="series", value_name="minutes")
    long["series"] = long["series"].map({"actual": "Actual", "predicted": "Predicted"})
    pick = alt.selection_point(name="pc_pick", fields=["date"], on="click")
    base = alt.Chart(long).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("minutes:Q", title="Delay minutes"),
        color=alt.Color("series:N", scale=alt.Scale(domain=["Actual", "Predicted"], range=[ACTUAL, PREDICTED]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("date:T", title="Day", format="%a %b %d, %Y"), alt.Tooltip("series:N", title=""),
                 alt.Tooltip("minutes:Q", title="Minutes", format=",.0f"),
                 alt.Tooltip("precip_mm:Q", title="Avg precipitation (mm)", format=".1f"),
                 alt.Tooltip("stations:Q", title="Stations")])
    bars = base.transform_filter(alt.datum.series == "Actual").mark_bar(size=4 if year == "All years" else 7,
                                                                       opacity=0.9).add_params(pick)
    dots = base.transform_filter(alt.datum.series == "Predicted").mark_point(filled=True, size=45, opacity=0.95)
    marker = alt.Chart(pd.DataFrame({"date": [day]})).mark_rule(color="#888", strokeDash=[4, 3]).encode(x="date:T")
    timeline = (bars + dots + marker).properties(height=260)
    try:
        st.altair_chart(timeline, key="pc_timeline", on_select=pick_from_timeline)
    except TypeError:
        st.altair_chart(timeline)

# ---- model accuracy -------------------------------------------------------------------
st.subheader("How accurate and precise is the model?")
try:
    a_cols = st.columns([2, 2, 2], vertical_alignment="bottom")
except TypeError:
    a_cols = st.columns([2, 2, 2])
level = a_cols[0].radio("Compare", ["Station by day", "Daily totals"], horizontal=True, key="pc_level",
                        help="Station by day = each row of the predictions file. Daily totals = all stations summed per day.")
default_tol = 15 if level == "Station by day" else 60
tolerance = a_cols[1].number_input("Close enough if within (min)", 1, 1000, default_tol, 5, key=f"pc_tol_{level}")
threshold = a_cols[2].number_input("Count a prediction as a delay from (min)", 0.0, 120.0, 1.0, 1.0, key="pc_threshold",
                                   help="Used for precision, recall and the confusion matrix.")

frame = preds if level == "Station by day" else daily
s = regression_stats(frame["actual"], frame["predicted"], tolerance)
c = classification_stats(frame["actual"], frame["predicted"], threshold)
unit = "station-days" if level == "Station by day" else "days"

with st.container(border=True):
    st.markdown("#### Accuracy: how close the predicted minutes are")
    st.markdown(f'<p class="rd-note">Over all {s["n"]:,} {unit}. Error = predicted − actual.</p>', unsafe_allow_html=True)
    r1 = st.columns(5)
    r1[0].metric("Mean absolute error", fmt_min(s["mae"]), help="Average size of the error, ignoring direction")
    r1[1].metric("Root mean squared error", fmt_min(s["rmse"]), help="Like MAE but punishes big misses more")
    r1[2].metric("Median absolute error", fmt_min(s["median_ae"]), help="Half the errors are smaller than this")
    r1[3].metric("Bias (mean error)", fmt_min(s["bias"], signed=True),
                 "over-predicts" if s["bias"] > 0 else "under-predicts", delta_color="off",
                 help="Positive = the model predicts more delay than happened, on average")
    r1[4].metric("R²", "—" if not np.isfinite(s["r2"]) else f"{s['r2']:.2f}",
                 help="Share of the variation in actual delays the predictions explain (1 = perfect, "
                      "0 = no better than always predicting the average, below 0 = worse)")

with st.container(border=True):
    st.markdown("#### Precision: how consistent the errors are, and how often a predicted delay is real")
    st.markdown(f'<p class="rd-note">A delay "happened" when the actual minutes are above 0; the model "predicts" one when it '
                f"predicts at least {threshold:g} min.</p>", unsafe_allow_html=True)
    r2 = st.columns(5)
    r2[0].metric("Error spread (SD)", fmt_min(s["sd"]), help="Standard deviation of the errors: lower = more consistent")
    r2[1].metric(f"Within ±{tolerance:g} min", fmt_pct(s["within"]), help=f"Share of {unit} predicted this closely")
    r2[2].metric("80% of errors under", fmt_min(s["p80"]), help="80th percentile of the absolute error")
    r2[3].metric("Precision", fmt_pct(c["precision"]),
                 help="Of the predicted delays, the share where a delay really happened")
    r2[4].metric("Recall", fmt_pct(c["recall"]), help="Of the real delays, the share the model predicted")
    left, right = st.columns([1, 2])
    with left:
        st.markdown("##### Confusion matrix")
        cm = pd.DataFrame({"Predicted delay": [c["tp"], c["fp"]], "Predicted none": [c["fn"], c["tn"]]},
                          index=["Actual delay", "Actual none"])
        st.dataframe(cm)
        f1_text = "—" if not np.isfinite(c["f1"]) else f"{c['f1']:.2f}"
        r_text = "—" if not np.isfinite(s["r"]) else f"{s['r']:.2f}"
        st.caption(f"Accuracy {fmt_pct(c['accuracy'])} · F1 {f1_text} · correlation {r_text}")
    with right:
        st.markdown("##### Error distribution")
        span = float(np.percentile(np.abs(frame["predicted"] - frame["actual"]), 95)) or 1.0
        errs = pd.DataFrame({"error": (frame["predicted"] - frame["actual"]).clip(-span, span)})
        st.altair_chart(alt.Chart(errs).mark_bar(color=PREDICTED, opacity=0.85).encode(
            x=alt.X("error:Q", bin=alt.Bin(maxbins=40), title=f"Predicted − actual (min, clipped at ±{span:,.0f})"),
            y=alt.Y("count():Q", title=unit.capitalize()),
            tooltip=[alt.Tooltip("error:Q", bin=True, title="Error (min)"), alt.Tooltip("count():Q", title="Count")],
        ).properties(height=220))

left, right = st.columns(2)
with left.container(border=True):
    st.markdown("#### Predicted vs actual")
    st.markdown(f'<p class="rd-note">Each point is one of the {s["n"]:,} {unit}; points on the dashed line are perfect. '
                "Both axes are compressed (symlog) so small and large delays both show. "
                "The selected day is outlined.</p>", unsafe_allow_html=True)
    pts = frame.assign(selected=frame["date"] == day)
    if level == "Station by day":
        pts = pts.assign(label=pts["station"])
    else:
        pts = pts.assign(label=pts["date"].dt.strftime("%b %d, %Y"))
    top = float(max(pts["actual"].max(), pts["predicted"].max(), 1))
    sym = alt.Scale(type="symlog", constant=10, domain=[0, top])
    dots = alt.Chart(pts).mark_circle(size=40).encode(
        x=alt.X("actual:Q", title="Actual delay (min)", scale=sym),
        y=alt.Y("predicted:Q", title="Predicted delay (min)", scale=sym),
        color=alt.condition("datum.selected", alt.value("#111111"), alt.value(PREDICTED)),
        opacity=alt.condition("datum.selected", alt.value(1.0), alt.value(0.45)),
        tooltip=[alt.Tooltip("label:N", title=""), alt.Tooltip("date:T", title="Day", format="%b %d, %Y"),
                 alt.Tooltip("actual:Q", title="Actual (min)", format=",.0f"),
                 alt.Tooltip("predicted:Q", title="Predicted (min)", format=",.0f")])
    line = alt.Chart(pd.DataFrame({"v": np.linspace(0, top, 200)})).mark_line(color="#888", strokeDash=[5, 4]).encode(
        x=alt.X("v:Q", scale=sym), y=alt.Y("v:Q", scale=sym))
    st.altair_chart((line + dots).properties(height=360))

with right.container(border=True):
    st.markdown("#### Error by precipitation")
    st.markdown('<p class="rd-note">Mean absolute error and bias, grouping each day by its city average precipitation '
                "(days before the gauge record are left out).</p>", unsafe_allow_html=True)
    g = frame.assign(precip=frame["date"].map(city_precip)).dropna(subset=["precip"])
    g["group"] = pd.cut(g["precip"], PRECIP_GROUP_BINS, labels=PRECIP_GROUP_LABELS, right=False).astype(str)
    by_group = (g.assign(abs_error=(g["predicted"] - g["actual"]).abs(), err=g["predicted"] - g["actual"])
                 .groupby("group").agg(n=("err", "size"), mae=("abs_error", "mean"), bias=("err", "mean"))
                 .reindex(PRECIP_GROUP_LABELS).dropna().reset_index())
    by_group_long = by_group.melt(id_vars=["group", "n"], value_vars=["mae", "bias"], var_name="measure", value_name="min")
    by_group_long["measure"] = by_group_long["measure"].map({"mae": "Mean absolute error", "bias": "Bias"})
    st.altair_chart(alt.Chart(by_group_long).mark_bar(cornerRadiusEnd=3).encode(
        y=alt.Y("group:N", sort=PRECIP_GROUP_LABELS, title=None),
        yOffset=alt.YOffset("measure:N"),
        x=alt.X("min:Q", title="Minutes"),
        color=alt.Color("measure:N", scale=alt.Scale(domain=["Mean absolute error", "Bias"], range=[PREDICTED, "#bbbbbb"]),
                        legend=alt.Legend(title=None, orient="bottom")),
        tooltip=[alt.Tooltip("group:N", title="Precipitation"), alt.Tooltip("measure:N", title=""),
                 alt.Tooltip("min:Q", title="Minutes", format="+,.0f"), alt.Tooltip("n:Q", title=unit.capitalize())],
    ).properties(height=360))

# ---- selected day table ---------------------------------------------------------------
with st.container(border=True):
    st.markdown(f"#### Stations on {day:%B} {day.day}, {day.year}")
    table = (day_rows.assign(abs_error=day_rows["error"].abs()).sort_values("abs_error", ascending=False)
             [["station", "actual", "predicted", "error"]]
             .rename(columns={"station": "Station", "actual": "Actual (min)", "predicted": "Predicted (min)",
                              "error": "Error (min)"}).round(1))
    st.dataframe(table, hide_index=True, height=min(38 + 35 * len(table), 400))
    st.download_button("Download this day's comparison (CSV)", table.to_csv(index=False).encode("utf-8"),
                       file_name=f"predicted_vs_actual_{day.date()}.csv", mime="text/csv", key="pc_download")

with st.expander("How the statistics are calculated"):
    st.markdown(f"""
**Data** — `data/predictions.csv`: one row per station and day, with the actual delay minutes (`Min Delay`) and the
model's predicted minutes (`Predicted Delay`). Rows with 0 actual minutes are stations the model was asked about
that had no delay that day. *Daily totals* add up every station on a day.

**Accuracy** (how close the minutes are) — *mean absolute error* is the average size of the miss;
*root mean squared error* weights big misses more; *bias* is the average of predicted − actual, so a positive value
means the model predicts too much delay on average; *R²* = 1 − Σ(error²) / Σ(actual − mean)², where 1 is perfect and
0 is no better than predicting the average every time.

**Precision** (how consistent and trustworthy the predictions are) — *error spread* is the standard deviation of the
errors; *within ±N min* and *80% of errors under* describe how tightly the errors cluster. Treating
"delay / no delay" as a yes-or-no prediction (a delay is predicted from {threshold:g} min), *precision* = of the
times the model predicted a delay, how often one happened; *recall* = of the delays that happened, how many were
predicted; *accuracy* = share of all {unit} classified correctly; *F1* combines precision and recall.

**Map** — the same IDW precipitation surface as the precipitation page (settings in the sidebar). Circles use the same
size scale on both maps, reaching full size at {SIZE_CAP} minutes, so actual and predicted circles can be compared
directly. Station locations come from the `geometry` column of the predictions file.
""")
