"""TRMNL Air Quality plugin backend.

Fetches current observations and forecast data from the AirNow API plus a
hyperlocal PM2.5 reading from a PurpleAir sensor, combines them into a
single JSON payload shaped for TRMNL's Liquid markup, and serves it from an
in-memory cache refreshed in the background every 15 minutes.
"""

import os
import threading
from datetime import datetime, timezone

import pytz
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, jsonify

load_dotenv()

app = Flask(__name__)

AIRNOW_API_KEY = os.environ.get("AIRNOW_API_KEY", "")
PURPLEAIR_API_KEY = os.environ.get("PURPLEAIR_API_KEY", "")
PURPLEAIR_SENSOR_INDEX = os.environ.get("PURPLEAIR_SENSOR_INDEX", "66251")
ZIP_CODE = os.environ.get("ZIP_CODE", "80127")
LOCATION_LABEL = "KEN CARYL, CO"

OBSERVATION_URL = "https://www.airnowapi.org/aq/observation/zipCode/current/"
FORECAST_URL = "https://www.airnowapi.org/aq/forecast/zipCode/"
PURPLEAIR_URL = "https://api.purpleair.com/v1/sensors/{}".format(PURPLEAIR_SENSOR_INDEX)
PURPLEAIR_FIELDS = (
    "name,pm2.5,pm2.5_10minute,pm2.5_30minute,pm2.5_60minute,"
    "humidity,temperature,pressure,last_seen,confidence"
)

MOUNTAIN_TZ = pytz.timezone("America/Denver")

# EPA PM2.5 breakpoints: (conc_low, conc_high, aqi_low, aqi_high)
PM25_BREAKPOINTS = [
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 500),
]

# Category number -> estimated AQI midpoint, used when AirNow only
# supplies a category (AQI == -1) for a forecast pollutant.
CATEGORY_AQI_ESTIMATE = {1: 25, 2: 75, 3: 125, 4: 175, 5: 250, 6: 350}

CATEGORY_NAMES = {
    1: "Good",
    2: "Moderate",
    3: "USG",
    4: "Unhealthy",
    5: "Very Unhealthy",
    6: "Hazardous",
}

HEALTH_MESSAGES = {
    1: "Air quality is satisfactory.",
    2: "Acceptable. Sensitive individuals may be affected.",
    3: "Sensitive groups may experience health effects.",
    4: "Everyone may begin to experience effects.",
    5: "Health alert: serious risk for everyone.",
    6: "Emergency conditions. Avoid outdoor activity.",
}

DAY_ABBREV = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

CACHE_TTL_SECONDS = 15 * 60

# In-memory cache: {"data": <dict>, "fetched_at": <datetime>}
_cache_lock = threading.Lock()
_cache = {"data": None, "fetched_at": None}


def _fetch_json(url, params=None, headers=None):
    response = requests.get(url, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()


def _day_label(date_str):
    try:
        return DAY_ABBREV[datetime.strptime(date_str, "%Y-%m-%d").weekday()]
    except (ValueError, TypeError):
        return date_str


def aqi_to_category(aqi):
    """Return (category_number, category_name) for an AQI value."""
    if aqi is None:
        return None, None
    if aqi <= 50:
        return 1, CATEGORY_NAMES[1]
    if aqi <= 100:
        return 2, CATEGORY_NAMES[2]
    if aqi <= 150:
        return 3, CATEGORY_NAMES[3]
    if aqi <= 200:
        return 4, CATEGORY_NAMES[4]
    if aqi <= 300:
        return 5, CATEGORY_NAMES[5]
    return 6, CATEGORY_NAMES[6]


def pm25_to_aqi(pm25_ugm3):
    """Convert a PM2.5 concentration (µg/m³) to AQI using EPA breakpoints."""
    if pm25_ugm3 is None:
        return None

    concentration = round(max(pm25_ugm3, 0.0), 1)
    for conc_low, conc_high, aqi_low, aqi_high in PM25_BREAKPOINTS:
        if conc_low <= concentration <= conc_high:
            aqi = ((aqi_high - aqi_low) / (conc_high - conc_low)) * (
                concentration - conc_low
            ) + aqi_low
            return round(aqi)

    # Above the top breakpoint - clamp to the worst published category.
    conc_low, conc_high, aqi_low, aqi_high = PM25_BREAKPOINTS[-1]
    return aqi_high


def get_health_message(category_number):
    return HEALTH_MESSAGES.get(category_number, "")


def fetch_airnow_observations():
    try:
        return _fetch_json(
            OBSERVATION_URL,
            params={
                "format": "application/json",
                "zipCode": ZIP_CODE,
                "distance": 25,
                "API_KEY": AIRNOW_API_KEY,
            },
        )
    except (requests.RequestException, ValueError):
        return None


def fetch_airnow_forecast():
    try:
        return _fetch_json(
            FORECAST_URL,
            params={
                "format": "application/json",
                "zipCode": ZIP_CODE,
                "distance": 25,
                "API_KEY": AIRNOW_API_KEY,
            },
        )
    except (requests.RequestException, ValueError):
        return None


def fetch_purpleair():
    try:
        return _fetch_json(
            PURPLEAIR_URL,
            params={"fields": PURPLEAIR_FIELDS},
            headers={"X-API-Key": PURPLEAIR_API_KEY},
        )
    except (requests.RequestException, ValueError):
        return None


def _process_current(observations):
    if not observations:
        return None

    pollutants = sorted(
        (
            {
                "name": obs.get("ParameterName"),
                "aqi": obs.get("AQI"),
                "value": "AQI {}".format(obs.get("AQI")),
            }
            for obs in observations
        ),
        key=lambda p: p["aqi"] if p["aqi"] is not None else -1,
        reverse=True,
    )

    worst = max(observations, key=lambda obs: obs.get("AQI", -1))
    first = observations[0]

    return {
        "overall_aqi": worst.get("AQI"),
        "category": (worst.get("Category") or {}).get("Name"),
        "category_number": (worst.get("Category") or {}).get("Number"),
        "reporting_area": first.get("ReportingArea"),
        "pollutants": pollutants,
    }


def _pollutant_aqi(entry):
    aqi = entry.get("AQI")
    if aqi is None or aqi == -1:
        category_number = (entry.get("Category") or {}).get("Number")
        return CATEGORY_AQI_ESTIMATE.get(category_number, 0)
    return aqi


def _process_forecast(forecast_entries):
    if not forecast_entries:
        return []

    by_date = {}
    for entry in forecast_entries:
        date = (entry.get("DateForecast") or "").strip()
        if not date:
            continue
        by_date.setdefault(date, []).append(entry)

    days = []
    for date in sorted(by_date.keys()):
        entries = by_date[date]
        worst = max(entries, key=_pollutant_aqi)
        worst_category = worst.get("Category") or {}
        days.append(
            {
                "date": date,
                "day_label": _day_label(date),
                "overall_aqi": _pollutant_aqi(worst),
                "category": worst_category.get("Name"),
                "category_number": worst_category.get("Number"),
                "action_day": any(e.get("ActionDay") for e in entries),
            }
        )
    return days


def _process_local_sensor(purpleair_response):
    if not purpleair_response:
        return None

    sensor = purpleair_response.get("sensor") or {}
    pm25 = sensor.get("pm2.5")
    if pm25 is None:
        return None

    aqi = pm25_to_aqi(pm25)
    _, category_name = aqi_to_category(aqi)

    last_seen = sensor.get("last_seen")
    last_seen_minutes_ago = None
    if last_seen is not None:
        last_seen_minutes_ago = max(
            0, round((datetime.now(timezone.utc).timestamp() - last_seen) / 60)
        )

    return {
        "name": sensor.get("name", "Ken Caryl Valley"),
        "pm25_ugm3": pm25,
        "pm25_aqi": aqi,
        "pm25_category": category_name,
        "temperature_f": sensor.get("temperature"),
        "humidity_pct": sensor.get("humidity"),
        "confidence": sensor.get("confidence"),
        "last_seen_minutes_ago": last_seen_minutes_ago,
    }


def _format_time_no_leading_zero(dt):
    """Format as 'h:MM AM/PM' without a leading zero on the hour.

    Uses %I and strips manually instead of the '%-I' glibc extension,
    which is unavailable on Windows (used for local dev on this project).
    """
    formatted = dt.strftime("%I:%M %p")
    return formatted.lstrip("0")


def build_response():
    now_mountain = datetime.now(timezone.utc).astimezone(MOUNTAIN_TZ)

    payload = {
        "location": LOCATION_LABEL,
        "updated_at": _format_time_no_leading_zero(now_mountain),
        "updated_at_full": now_mountain.isoformat(),
        "current": None,
        "local_sensor": None,
        "forecast": [],
        "health_message": "",
    }

    observations = fetch_airnow_observations()
    forecast_entries = fetch_airnow_forecast()
    purpleair_response = fetch_purpleair()

    current = _process_current(observations)
    payload["current"] = current
    if current is None:
        payload["error"] = "No current data available"
    else:
        payload["health_message"] = get_health_message(current.get("category_number"))

    payload["local_sensor"] = _process_local_sensor(purpleair_response)
    payload["forecast"] = _process_forecast(forecast_entries)

    return payload


def refresh_cache():
    try:
        data = build_response()
    except Exception:
        # Never let a scheduler tick crash the app; keep serving stale data.
        return
    with _cache_lock:
        _cache["data"] = data
        _cache["fetched_at"] = datetime.now(timezone.utc)


def _cache_is_stale():
    fetched_at = _cache["fetched_at"]
    if fetched_at is None:
        return True
    age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    return age > CACHE_TTL_SECONDS


@app.route("/")
def health_check():
    return jsonify({"status": "ok", "service": "trmnl-airquality"})


@app.route("/api/aqi")
def get_aqi():
    with _cache_lock:
        stale = _cache_is_stale()
        data = _cache["data"]

    if stale:
        refresh_cache()
        with _cache_lock:
            data = _cache["data"]

    if data is None:
        data = {
            "location": LOCATION_LABEL,
            "updated_at": None,
            "updated_at_full": None,
            "current": None,
            "local_sensor": None,
            "forecast": [],
            "health_message": "",
            "error": "No data available",
        }

    return jsonify(data)


scheduler = BackgroundScheduler()
scheduler.add_job(refresh_cache, "interval", minutes=15, next_run_time=datetime.now())
scheduler.start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
