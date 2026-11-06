# Precipitation and TTC weather-related bus delays

Interactive Streamlit dashboard: daily precipitation across Toronto (inverse distance weighted from the
City of Toronto rain gauges) with the TTC's weather-related bus delays.

* Pick a date, or jump between delay days, to map that day's precipitation surface and delays
* Click a day on the timeline or a row in the delay-day table to map it
* Compare delays per day across precipitation groups
* Advanced settings (sidebar): winter gauge rule, IDW power, colour scale, hot-spot layer

## Pages

* **Precipitation & weather delays** (`precipitation_delays.py`): the interactive map and charts above.
* **Predicted vs actual delays** (`prediction_comparison.py`): the model's per-station delay predictions
  (`data/predictions.csv`) against the delays that happened, day by day: side-by-side actual and predicted maps on
  the precipitation surface (or one map of the prediction error), daily totals, and accuracy and precision
  statistics (MAE, RMSE, bias, R², error spread, precision/recall and a confusion matrix). To update it, replace
  `data/predictions.csv` with a new file that has the same columns (Station, Date, geometry, Min Delay, Predicted Delay).
* **Bus delay EDA (pre-2025)** (`bus_delay_eda.py`): an interactive dashboard of every TTC bus delay
  2014–2024. Sidebar filters (years, incident types, days of week, routes, maximum delay) drive summary
  figures and tabs for monthly trends, incident types, time of day, top routes and locations, a delay
  heat map, and the matching rows (downloadable as CSV). It reads two gzipped files made by
  `prepare_bus_eda_data.py`:

      python prepare_bus_eda_data.py path/to/folder/with/the/pre-2025/CSVs

  which writes `data/eda_delays.csv.gz` and `data/eda_delays_geo.csv.gz` (about 8 MB each).
  Upload those two files to the `data` folder; the large original CSVs are not needed online.

## Run locally

    pip install -r requirements.txt
    streamlit run streamlit_app.py

## Deploy on Streamlit Community Cloud

1. Put this folder's contents in a public GitHub repository (keep the `data` and `.streamlit` folders).
2. At https://share.streamlit.io choose **Create app → Deploy a public app from GitHub**,
   pick the repository and branch, and set the main file to `streamlit_app.py`.

## Data (`data/`)

| File | Contents |
|---|---|
| `daily_precipitation.csv` | Daily precipitation (mm) per gauge, Jun 2015 – Oct 2026, summed from Toronto Open Data 5-minute readings |
| `rain_gauges.csv` | Gauge locations and 3-/4-season type |
| `bus_delays.csv` | TTC weather-related bus delays with coordinates |
| `predictions.csv` | Model predictions vs actual delay minutes per station and day |
| `city_grid.csv` | ~500 m grid cells covering the city, with neighbourhood |
| `neighbourhoods.json` | Toronto neighbourhood outlines (simplified) |
| `eda_delays.csv.gz` | All pre-2025 TTC bus delays (EDA page) |
| `eda_delays_geo.csv.gz` | Pre-2025 TTC bus delays with coordinates (EDA page map) |
