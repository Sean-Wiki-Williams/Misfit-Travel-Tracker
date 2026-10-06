># Daily Flight Tracker

A Django web app that shows signed-in users their flight schedules by parsing their individual iCal/CalDAV feeds. User accounts, schedules, and individual flight records are stored in a database, with each flight belonging to one user.

## How it works

1. Users register with an email and password, then add their private calendar feed in Account.
2. **`tracker`** fetches the user's `.ics` feed, parses it using `flight_parser.py`, and saves the schedule and individual flights.
3. **`templates/index.html`** renders the signed-in user's schedule, a live map, and a home/leave countdown. Schedule API access requires authentication.
4. The iCloud feed is not a simple "one event per flight" calendar — most events are multi-day **rotation reports** (e.g. `SUMMARY: 2904 BDL (0626-1706)`) whose `DESCRIPTION` contains several flight legs grouped under `Rpt- HHMM DDMMM` markers. `parse_rotation_legs()` in `flight_parser.py` extracts each individual leg (flight number, route, times, aircraft) from those blocks. A small number of standalone single-leg events (`SUMMARY: I DL1805 : SAP - ATL`) are also supported for backward compatibility.
5. Users can also add, edit, and remove flights manually at `/flights/add/`. These are kept separately from imported flights and are not removed by calendar refreshes.

## Local development

These steps are for Windows and Command Prompt. On a Mac, see [Local development on a Mac](#local-development-on-a-mac). To open Command Prompt, press the Windows key, type `cmd`, and press Enter. You only need to do the setup steps once per computer (and again if you delete the `.venv` folder).

### 1. Install Python

Install a current version of Python 3 from [python.org](https://www.python.org/downloads/windows/). During installation, select **Add python.exe to PATH** if the installer offers that option.

Open Command Prompt and check that the Python launcher is available:

```bat
py --version
```

If Command Prompt says that `py` is not recognized, finish or repair the Python installation, then close and reopen Command Prompt.

### 2. Download the project from GitHub

1. Open the repository in your browser: <https://github.com/Sean-Wiki-Williams/PilotScheduler>.
2. Select the green **Code** button, then **Download ZIP**.
3. Open your Downloads folder, right-click the ZIP file, and choose **Extract All...**.
4. Move the extracted `PilotScheduler` folder somewhere easy to find, such as your Documents folder.

### 3. Open the project folder

In Command Prompt, change to the folder you downloaded. You can also right click in that folder on windows and select open in terminal.

```bat
cd /d "%USERPROFILE%\Documents\PilotScheduler"
```

Adjust the path if you saved the project somewhere else. If you extracted a ZIP, the folder you need is the one that directly contains `manage.py` and `requirements.txt`; it may be nested inside another folder with the same name. You can check with:

```bat
dir manage.py requirements.txt
```

### 4. Create and activate a virtual environment

A virtual environment keeps this project's Python packages separate from other projects. Create it once:

```bat
py -m venv .venv
```

Activate it whenever you open a new Command Prompt window to work on this project:

```bat
.venv\Scripts\activate.bat
```

When activation succeeds, the prompt starts with `(.venv)`.

### 5. Install the project packages

With the virtual environment activated, install the packages the project needs:

```bat
python -m pip install -r requirements.txt
```

### 6. Configure SkyLink lookup

Drop the `.env` file the admin emails you into the project folder (the folder containing `manage.py`). Do not share this file with anyone or upload it to any shared drives.

### 7. Create the local database tables

Run the database migrations:

```bat
python manage.py migrate
```

The development database uses SQLite by default. You do not need to create a database yourself; Django creates `db.sqlite3` and its tables when you run the migrations.

### 8. Start the development server

Start the server from the project folder, with the virtual environment activated:

```bat
python manage.py runserver
```

Keep this Command Prompt window open while using the app, and visit <http://127.0.0.1:8000/> in your internet browser. Stop the server by pressing **Ctrl+C** in Command Prompt. To start it again later, open Command Prompt, change to the project folder, activate `.venv`, and run the command above. Create an account at <http://127.0.0.1:8000/register/>.

If the project is updated and reports missing database tables, run `python manage.py migrate` again before starting the server.

### Access the site from other devices on your network

By default the site is only reachable from the computer running it. To let phones and other computers on the same home network open it:

1. On the host computer, find its local IP address:

   ```bat
   ipconfig
   ```

   Look for the **IPv4 Address** of your active adapter, for example `192.168.1.50`.

2. Allow that address by adding this line to the `.env` file in the project folder, using your own IP and keeping the localhost entries:

   ```
   ALLOWED_HOSTS=localhost,127.0.0.1,[::1],192.168.1.50
   ```

   Do not set `DEBUG=0` for this; with it off, login and form cookies require HTTPS and will not work over plain `http://`.

3. Start the server so it listens on the network instead of only on the local computer:

   ```bat
   python manage.py runserver 0.0.0.0:8000
   ```

   The first time you run it, Windows may ask to allow Python through the firewall. Allow it on **Private** networks. If no prompt appears, add an inbound Windows Firewall rule for TCP port 8000.

4. On another device connected to the same network, open `http://192.168.1.50:8000/` (using your own IP).

The host computer must stay on with the server window open. Your router may assign the computer a new IP address after a restart; reserve a fixed IP for it in your router settings if that happens, and update `ALLOWED_HOSTS` if the address changes. `runserver` is for development only: keep it on your home network and do not port-forward it to the internet. For internet access, follow the Deployment section below.

### Quick start after initial setup

For later sessions, open Command Prompt in the project folder (the folder containing `manage.py`), then run:

```bat
.venv\Scripts\activate.bat
python manage.py runserver
```

The Django database starts empty.

`ICAL_ALLOWED_HOSTS` is a comma-separated allowlist for iCal provider domains. It defaults to iCloud domains; feed URLs must use HTTPS (or `webcal://`, which is upgraded to HTTPS), and redirects must stay within the allowlist.

## Local development on a Mac

The steps are the same as the Windows steps above, except for the commands below. Use the Terminal app (press Cmd+Space, type `Terminal`, and press Return).

1. **Install Python.** Install Python 3 from [python.org](https://www.python.org/downloads/macos/) (or run `brew install python` if you use Homebrew), then check it:

   ```bash
   python3 --version
   ```

2. **Download the project.** For the ZIP option, double-click the downloaded ZIP to extract it. For the Git option, run `git --version`; if Git is missing, macOS offers to install the Command Line Tools. Then:

   ```bash
   cd ~/Documents
   git clone https://github.com/Sean-Wiki-Williams/PilotScheduler.git
   ```

3. **Open the project folder** and check that you are in the right place:

   ```bash
   cd ~/Documents/PilotScheduler
   ls manage.py requirements.txt
   ```

4. **Create and activate a virtual environment:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   The prompt starts with `(.venv)` when activation succeeds. Activate it again with the second command whenever you open a new Terminal window.

5. **Install the packages:**

   ```bash
   python -m pip install -r requirements.txt
   ```

6. **Add the `.env` file** the admin emails you to the project folder. Files starting with a dot are hidden in Finder; press Cmd+Shift+. to show them. If your mail app saves the file as `env` or `env.txt`, rename it in Terminal (adjust the path to where it was saved):

   ```bash
   mv ~/Downloads/env ~/Documents/PilotScheduler/.env
   ```

7. **Create the database tables and start the server:**

   ```bash
   python manage.py migrate
   python manage.py runserver
   ```

   Then visit <http://127.0.0.1:8000/>. Stop the server with **Ctrl+C**. For later sessions, run `cd ~/Documents/PilotScheduler`, `source .venv/bin/activate`, and `python manage.py runserver`.

To reach the site from other devices on your network, follow the network access steps above with these changes: find the IP address with `ipconfig getifaddr en0` (Wi-Fi; try `en1` if it prints nothing, or check System Settings > Network), and click **Allow** if macOS asks whether Python can accept incoming connections.

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
