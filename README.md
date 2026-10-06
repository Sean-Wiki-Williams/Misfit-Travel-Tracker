># Dad's Flight Tracker

A Django web app that shows signed-in users their flight schedules by parsing their individual iCal/CalDAV feeds. User accounts, schedules, and individual flight records are stored in a database, with each flight belonging to one user.

## How it works

1. Users register with an email and password, then add their private calendar feed in Account.
2. **`tracker`** fetches the user's `.ics` feed, parses it using `flight_parser.py`, and saves the schedule and individual flights.
3. **`templates/index.html`** renders the signed-in user's schedule, a live map, and a home/leave countdown. Schedule API access requires authentication.
4. The iCloud feed is not a simple "one event per flight" calendar — most events are multi-day **rotation reports** (e.g. `SUMMARY: 2904 BDL (0626-1706)`) whose `DESCRIPTION` contains several flight legs grouped under `Rpt- HHMM DDMMM` markers. `parse_rotation_legs()` in `flight_parser.py` extracts each individual leg (flight number, route, times, aircraft) from those blocks. A small number of standalone single-leg events (`SUMMARY: I DL1805 : SAP - ATL`) are also supported for backward compatibility.
5. Users can also add, edit, and remove flights manually at `/flights/add/`. These are kept separately from imported flights and are not removed by calendar refreshes.

## Local development

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

The local development database defaults to SQLite in `db.sqlite3`; `python manage.py migrate` creates its tables. Create an account at `/register/`. For local SkyLink lookup, copy `.env.example` to `.env` and enter your rotated direct-subscription key in that local file:

```powershell
Copy-Item .env.example .env
# Edit .env and set SKYLINK_API_KEY to your rotated key.
python manage.py runserver
```

The real `.env` is ignored by Git; never put the key in source code, browser JavaScript, or a committed settings file. Existing process environment variables take precedence over `.env`. For production, set the key as a server-side environment variable alongside a unique `SECRET_KEY`, `DEBUG=0`, `ALLOWED_HOSTS`, and `DATABASE_URL` to a PostgreSQL URL. Set `SECURE_SSL_REDIRECT=1` and secure cookies when the app is behind HTTPS.

The Django database starts empty; existing records in the old Flask database are not automatically migrated.

`ICAL_ALLOWED_HOSTS` is a comma-separated allowlist for iCal provider domains. It defaults to iCloud domains; feed URLs must use HTTPS (or `webcal://`, which is upgraded to HTTPS), and redirects must stay within the allowlist.

## API

- `GET /api/flights/` (alias `/api/data/`) — requires sign-in; returns the current user's parsed schedule, cached in the database for 60 seconds. Add `?force=1` to refresh immediately.
- `/register/`, `/accounts/login/`, `POST /accounts/logout/` — account/session actions protected by Django's CSRF middleware.
- `GET|POST /account/` — view or update the signed-in user's iCal feed.
- `GET|POST /flights/add/` — add a flight using its number, IATA airports, airport-local departure/arrival times, and optional equipment/gates. Arrival and departure must be later than the departure as absolute instants. Nonexistent local times during daylight-saving transitions are rejected; ambiguous repeated times use the earlier occurrence.
- `POST /flights/lookup/` — authenticated server-side SkyLink v3.1 lookup by IATA or ICAO flight number. The direct API key is read from `SKYLINK_API_KEY` and never sent to the browser. SkyLink's flight-status endpoint has no date parameter and returns operationally available dates/times, airports, and gates; review the results and adjust them for future flights before saving. Equipment is not returned by this endpoint.
- `GET /api/opensky/states/` — authenticated, same-origin proxy for OpenSky state vectors. The browser requests positions for a validated route bounding box so OpenSky's CORS policy does not block the dashboard.
- The dashboard checks OpenSky for a live ADS-B position while the selected flight is in the air, polling every 45 seconds. It currently resolves Delta flight numbers to their `DAL` callsign and requests only a bounding box around the route. The progress bar uses the aircraft's along-route position when live data is available and a schedule-time estimate otherwise; it no longer uses a fixed percentage. If a matching aircraft position is unavailable, the map labels its position as estimated. OpenSky supplies position and heading; schedule/status data remains from the calendar parser.
- `GET|POST /flights/<id>/edit/` — edit one of the signed-in user's manually added flights using the same validation and airport-local time rules.
- `POST /flights/<id>/delete/` — remove one of the signed-in user's manually added flights.

Key response fields:
- `flights` — every parsed flight leg (past legs before today are dropped)
- `upcoming_flights` / `upcoming_dates` — legs/dates within the next 2 months
- `featured_flight` — the flight currently in the air, or the next upcoming one
- `countdown_mode` (`"home"` or `"leave"`) and `countdown_days` — drive the "Dad will be home/leave in X days" banner
- `tonight_stay` — layover hotel info for the current trip, if any

## Deployment

Deploy to a host that provides PostgreSQL and allows outbound HTTPS requests to the calendar provider domains in `ICAL_ALLOWED_HOSTS`. Configure `DATABASE_URL`, `SECRET_KEY`, `DEBUG=0`, `ALLOWED_HOSTS`, and HTTPS settings in the host's environment, install `requirements.txt`, run `python manage.py migrate`, and start `config.wsgi:application` with a production WSGI server.

The old GitHub Actions relay and current `flight_calendar.ics` file have been removed from this source tree. Push those removals before deploying private user calendars. Removing the file from the latest commit does not erase copies in Git history; rotate the calendar feed URL if it was exposed.

The old shared-feed relay cannot safely serve as a multi-user replacement because it publishes a single feed. The nested `PilotScheduler/site/` app is an older Flask copy and is not the Django application.

## Known data quirks

- Flight departure and arrival timestamps are converted by the browser to the viewer's local time zone for the flight table, details modal, and drawer.
- If a leg's arrival time is earlier than its departure time, it's assumed to land the next day (overnight/red-eye legs).
- `Rpt-` dates don't include a year, so the parser infers it from the event's own start/end dates and adjusts for rotations spanning a New Year's boundary.
- The dashboard uses Esri World Street Map raster tiles, with Esri and source attribution shown on the map; it does not call the OpenStreetMap Foundation's standard tile endpoint.
