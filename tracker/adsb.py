import math
import re
import time

import requests

ADSB_LOL_CALLSIGN_URL = "https://api.adsb.lol/v2/callsign/{callsign}"
CALLSIGN_PATTERN = re.compile(r"^[A-Z0-9]{2,8}$")
FEET_TO_METERS = 0.3048
KNOTS_TO_MPS = 0.514444


class AdsbLookupError(Exception):
    pass


def normalize_callsign(value):
    callsign = (value or "").strip().upper()
    if not callsign:
        return None
    if not CALLSIGN_PATTERN.match(callsign):
        raise ValueError("Provide a valid callsign.")
    return callsign


def states_contain_callsign(states, callsign):
    return any(
        isinstance(state, list)
        and len(state) > 1
        and isinstance(state[1], str)
        and state[1].strip().upper() == callsign
        for state in states
    )


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _to_state_vector(aircraft):
    lat = _number(aircraft.get("lat"))
    lon = _number(aircraft.get("lon"))
    if lat is None or lon is None:
        return None

    altitude = aircraft.get("alt_baro")
    on_ground = altitude == "ground"
    altitude_ft = _number(altitude)
    speed_kt = _number(aircraft.get("gs"))
    # Same field order as OpenSky state vectors so the dashboard can use either source.
    return [
        aircraft.get("hex"),
        str(aircraft.get("flight") or "").strip(),
        None,
        None,
        None,
        lon,
        lat,
        altitude_ft * FEET_TO_METERS if altitude_ft is not None else None,
        on_ground,
        speed_kt * KNOTS_TO_MPS if speed_kt is not None else None,
        _number(aircraft.get("track")),
    ]


def fetch_callsign_states(callsign):
    try:
        response = requests.get(
            ADSB_LOL_CALLSIGN_URL.format(callsign=callsign),
            timeout=(5, 15),
        )
    except requests.RequestException as error:
        raise AdsbLookupError("Could not connect to adsb.lol.") from error
    if not response.ok:
        raise AdsbLookupError("adsb.lol could not provide aircraft positions.")

    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError as error:
        raise AdsbLookupError("adsb.lol returned an unreadable response.") from error
    aircraft = payload.get("ac") if isinstance(payload, dict) else None
    if not isinstance(aircraft, list):
        raise AdsbLookupError("adsb.lol returned an unreadable response.")

    states = [
        state
        for item in aircraft
        if isinstance(item, dict)
        for state in [_to_state_vector(item)]
        if state is not None
    ]
    return {"time": int(time.time()), "states": states}
