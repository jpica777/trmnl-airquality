# TRMNL Air Quality Plugin

Shows current AQI, a hyperlocal PurpleAir PM2.5 reading, and a multi-day
forecast for zip code **80127** (Ken Caryl Ranch, Littleton, CO) on a TRMNL
e-ink display. A Flask backend on Railway combines data from the **AirNow**
API (regional AQI + forecast) and a **PurpleAir** sensor in the Ken Caryl
Valley (sensor index `66251`) into one JSON payload; TRMNL polls that
endpoint and renders it via Liquid markup.

## 1. Get API keys

**AirNow**
1. Go to https://docs.airnowapi.org/
2. Click **Log In** and create a free account.
3. After email verification, your API key is on the account page.

**PurpleAir**
1. Go to https://develop.purpleair.com/
2. Sign in with a Google account and request a free API key (read key).
3. It may take a day or two to be approved.

## 2. Local setup

```bash
cd trmnl-airquality
python -m venv .venv
. .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # then fill in AIRNOW_API_KEY and PURPLEAIR_API_KEY
flask --app app run
```

Visit `http://localhost:5000/api/aqi` and confirm you get back JSON shaped
like the example in [Response shape](#response-shape).

## 3. Deploy to Railway

1. Push this directory to a GitHub repo.
2. In Railway: **New Project → Deploy from GitHub repo**, select the repo.
3. Set environment variables under the service's **Variables** tab:
   - `AIRNOW_API_KEY` — your AirNow key
   - `PURPLEAIR_API_KEY` — your PurpleAir key
   - `PURPLEAIR_SENSOR_INDEX` — `66251` (optional, this is the default)
   - `ZIP_CODE` — `80127` (optional, this is the default)
4. Railway auto-detects Python and deploys using the included `Procfile`.
5. Copy the public URL Railway gives you, e.g.
   `https://trmnl-airquality-production-xxxx.up.railway.app`.
6. Confirm `https://<your-app>.up.railway.app/api/aqi` returns valid JSON.

## 4. Configure the TRMNL plugin

1. In the TRMNL dashboard: **Plugins → Private Plugin → New**.
2. Name: `Air Quality — Ken Caryl`.
3. Strategy: **Polling**.
4. Polling URL: `https://<your-app>.up.railway.app/api/aqi`.
5. Leave the polling interval at the default.
6. Open **Edit Markup → Full** and paste the Liquid markup below.
7. Save, then click **Force Refresh** in the TRMNL dashboard and check the
   preview.

**Important:** never use `.screen` as a CSS class name in TRMNL markup — it
conflicts with TRMNL's built-in `plugins.css` framework. This template uses
`.aqi-header`, `.aqi-main`, `.aqi-pollutants`, `.aqi-local-row`, and
`.aqi-forecast-grid` instead. Templates must be bare HTML with a single
`<style>` block — no `<!DOCTYPE>`, `<html>`, `<head>`, or `<body>` tags.

### Liquid markup

```html
<style>
  .aqi-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;
    padding-bottom: 4px;
    border-bottom: 2px solid black;
  }
  .aqi-main {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 12px 0;
  }
  .aqi-pollutants {
    display: flex;
    justify-content: space-around;
    width: 100%;
    padding: 8px 0;
  }
  .aqi-pollutant-item {
    text-align: center;
  }
  .aqi-local-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;
    padding: 6px 0;
  }
  .aqi-forecast-grid {
    display: flex;
    justify-content: space-around;
    width: 100%;
    padding: 4px 0;
  }
  .aqi-forecast-day {
    text-align: center;
    min-width: 100px;
  }
  .aqi-no-data {
    text-align: center;
    padding: 16px 0;
  }
</style>

<div class="layout layout--col layout--left">

  <!-- HEADER: Location + Time -->
  <div class="aqi-header">
    <span class="title title--small">{{ location }}</span>
    <span class="title title--small">{{ updated_at }}</span>
  </div>

  {% if current %}
    <!-- MAIN AQI DISPLAY -->
    <div class="aqi-main">
      <span class="value value--xlarge">{{ current.overall_aqi }}</span>
      <div class="layout layout--col layout--left">
        <span class="label">AQI</span>
        <span class="title">{{ current.category | upcase }} AIR</span>
      </div>
    </div>

    <!-- POLLUTANT BREAKDOWN -->
    <div class="divider"></div>
    <div class="aqi-pollutants">
      {% for p in current.pollutants %}
        <div class="aqi-pollutant-item">
          <span class="label label--small">{{ p.name }}</span>
          <span class="description">{{ p.value }}</span>
        </div>
      {% endfor %}
    </div>

  {% else %}
    <div class="aqi-no-data">
      <span class="description">No current AQI data available</span>
    </div>
  {% endif %}

  <!-- LOCAL PURPLEAIR SENSOR -->
  {% if local_sensor %}
    <div class="divider"></div>
    <div class="aqi-local-row">
      <span class="label label--small">LOCAL: {{ local_sensor.name }}</span>
      <span class="description">AQI {{ local_sensor.pm25_aqi }} · {{ local_sensor.pm25_category }}</span>
      <span class="description">{{ local_sensor.temperature_f }}°F  {{ local_sensor.humidity_pct }}% RH</span>
    </div>
  {% endif %}

  <!-- FORECAST -->
  <div class="divider"></div>

  {% if forecast.size > 0 %}
    <span class="label label--small" style="padding: 4px 0;">{{ forecast.size }} DAY FORECAST</span>
    <div class="aqi-forecast-grid">
      {% for day in forecast %}
        <div class="aqi-forecast-day">
          <span class="label">{{ day.day_label }}</span>
          <span class="value value--large">{% if day.action_day %}⚠ {% endif %}{{ day.overall_aqi }}</span>
          <span class="description">{{ day.category | upcase }}</span>
        </div>
      {% endfor %}
    </div>
  {% else %}
    <div class="aqi-no-data">
      <span class="description">No forecast beyond today</span>
    </div>
  {% endif %}

  <!-- HEALTH MESSAGE -->
  {% if health_message %}
    <div class="divider"></div>
    <span class="description" style="padding-top: 4px; font-style: italic;">{{ health_message }}</span>
  {% endif %}

</div>

<!-- TITLE BAR -->
<div class="title_bar">
  <span class="title">Air Quality</span>
  <span class="instance">{{ location }}</span>
</div>
```

Tweak spacing, flex ratios, and font sizes in TRMNL's live preview as
needed — the values above are a starting point.

## Response shape

`GET /api/aqi` returns:

```json
{
  "location": "KEN CARYL, CO",
  "updated_at": "7:42 AM",
  "updated_at_full": "2026-07-01T07:42:00-06:00",

  "current": {
    "overall_aqi": 42,
    "category": "Good",
    "category_number": 1,
    "reporting_area": "Denver-Boulder",
    "pollutants": [
      {"name": "O3", "aqi": 42, "value": "AQI 42"},
      {"name": "PM2.5", "aqi": 35, "value": "AQI 35"}
    ]
  },

  "local_sensor": {
    "name": "Ken Caryl Valley",
    "pm25_ugm3": 8.2,
    "pm25_aqi": 34,
    "pm25_category": "Good",
    "temperature_f": 88,
    "humidity_pct": 28,
    "confidence": 100,
    "last_seen_minutes_ago": 2
  },

  "forecast": [
    {
      "date": "2026-07-02",
      "day_label": "WED",
      "overall_aqi": 35,
      "category": "Good",
      "category_number": 1,
      "action_day": false
    }
  ],

  "health_message": "Air quality is satisfactory."
}
```

Notes:
- If AirNow returns no current observations, `current` is `null` and an
  `error` field is included; `health_message` is left empty in that case.
- If PurpleAir is unreachable or the sensor has no `pm2.5` reading,
  `local_sensor` is `null` — this never blocks the rest of the response.
- `forecast` defaults to `[]` if AirNow's forecast endpoint is unavailable.
- AirNow's observation endpoint only returns an AQI number per pollutant
  (no raw concentration), so `current.pollutants[].value` shows `"AQI {n}"`.
  The PurpleAir sensor is the only source with a raw µg/m³ reading.
- The server never crashes on a bad upstream response — it always returns
  valid JSON, falling back to the last good cached payload when possible.

## PM2.5 → AQI conversion

PurpleAir's API returns raw PM2.5 concentration in µg/m³, not AQI. The
`pm2.5_to_aqi()` function in `app.py` converts it using the standard EPA
breakpoint table and linear interpolation:

| PM2.5 (µg/m³) | AQI Low | AQI High | Category |
|---|---|---|---|
| 0.0–9.0 | 0 | 50 | Good |
| 9.1–35.4 | 51 | 100 | Moderate |
| 35.5–55.4 | 101 | 150 | USG |
| 55.5–125.4 | 151 | 200 | Unhealthy |
| 125.5–225.4 | 201 | 300 | Very Unhealthy |
| 225.5–325.4 | 301 | 500 | Hazardous |

The app requests PurpleAir's `pm2.5` field (not `pm2.5_cf_1` or
`pm2.5_atm`), which PurpleAir returns with the EPA correction factor for
outdoor sensors already applied.

## Caching

Data is cached in memory for 15 minutes (PurpleAir updates roughly every 2
minutes; AirNow observations update hourly). A background `APScheduler` job
refreshes the cache every 15 minutes, and `/api/aqi` also refreshes on
demand if the cache has gone stale, so the endpoint always serves current
data quickly.
