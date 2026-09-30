># Dad's Flight Tracker

A small Flask web app that shows Colin where Dad (a Delta pilot) is flying — today, tomorrow, and over the next two months — by parsing his iCloud crew schedule (iCal/CalDAV feed) from Delta's MyCrew system.

## How it works

1. **`app.py`** fetches an `.ics` calendar feed and parses it into flights, layovers, and rotation reports.
2. **`index.html`** is a static single-page frontend that calls `/api/flights` and renders the schedule, a live map, and a home/leave countdown.
3. The iCloud feed is not a simple "one event per flight" calendar — most events are multi-day **rotation reports** (e.g. `SUMMARY: 2904 BDL (0626-1706)`) whose `DESCRIPTION` contains several flight legs grouped under `Rpt- HHMM DDMMM` markers. `parse_rotation_legs()` in `app.py` extracts each individual leg (flight number, route, times, aircraft) from those blocks. A small number of standalone single-leg events (`SUMMARY: I DL1805 : SAP - ATL`) are also supported for backward compatibility.

## Local development

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
$env:ICAL_URL = "https://your-actual-icloud-caldav-url"
.venv\Scripts\python app.py
```

The app runs on `http://localhost:5000` by default (override with the `PORT` env var).

## API

- `GET /api/flights` (alias `/api/data`) — returns parsed schedule JSON, cached for `CACHE_TTL_SECONDS` (60s). Add `?force=1` to bypass the cache.

Key response fields:
- `flights` — every parsed flight leg (past legs before today are dropped)
- `upcoming_flights` / `upcoming_dates` — legs/dates within the next 2 months
- `featured_flight` — the flight currently in the air, or the next upcoming one
- `countdown_mode` (`"home"` or `"leave"`) and `countdown_days` — drive the "Dad will be home/leave in X days" banner
- `tonight_stay` — layover hotel info for the current trip, if any

## Deployment (PythonAnywhere) and the GitHub relay

PythonAnywhere's **free tier** only allows outbound HTTPS requests to a whitelisted set of domains — `icloud.com` is not on it, so fetching the calendar directly from PythonAnywhere fails with `URLError: Tunnel connection failed: 403 Forbidden`.

To work around this without upgrading PythonAnywhere, a companion GitHub repo ([PilotScheduler](https://github.com/Sean-Wiki-Williams/PilotScheduler)) runs a scheduled **GitHub Actions** workflow (`.github/workflows/sync-ical.yml`) that:

1. Fetches the real iCloud `.ics` feed every 20 minutes (URL kept secret via the `ICAL_URL` repository secret).
2. Commits it as `flight_calendar.ics` in that repo.

`app.py`'s `DEFAULT_ICAL_URL` then reads from `https://raw.githubusercontent.com/...` instead of iCloud directly — `raw.githubusercontent.com` is on PythonAnywhere's free whitelist. Set the `ICAL_URL` environment variable on PythonAnywhere to override this if needed.

### Deploy steps

1. Push `app.py` and `index.html` to PythonAnywhere (Files tab or git pull).
2. Set the `ICAL_URL` env var only if you want to bypass the GitHub relay.
3. Reload the web app from the **Web** tab.

## Known data quirks

- Times in the feed are timezone-aware (`TZID=America/Chicago`, etc.). The backend preserves those timestamps, and the frontend renders flight dates/times in the viewer's local browser timezone.
- If a leg's arrival time is earlier than its departure time, it's assumed to land the next day (overnight/red-eye legs).
- `Rpt-` dates don't include a year, so the parser infers it from the event's own start/end dates and adjusts for rotations spanning a New Year's boundary.
