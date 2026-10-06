import math

import requests

OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"
BOUND_PARAMETERS = ("lamin", "lomin", "lamax", "lomax")


class OpenSkyRequestError(Exception):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def parse_bounding_box(query_params):
    bounds = {}
    for name in BOUND_PARAMETERS:
        try:
            bounds[name] = float(query_params[name])
        except (KeyError, TypeError, ValueError) as error:
            raise OpenSkyRequestError(
                "Provide valid minimum and maximum latitude and longitude bounds.",
                status_code=400,
            ) from error

    if not all(math.isfinite(value) for value in bounds.values()):
        raise OpenSkyRequestError("Bounding coordinates must be finite numbers.", status_code=400)
    if not (-90 <= bounds["lamin"] < bounds["lamax"] <= 90):
        raise OpenSkyRequestError("Latitude bounds must be ordered and between -90 and 90.", status_code=400)
    if not (-180 <= bounds["lomin"] < bounds["lomax"] <= 180):
        raise OpenSkyRequestError("Longitude bounds must be ordered and between -180 and 180.", status_code=400)

    return bounds


def fetch_states(bounds):
    try:
        response = requests.get(
            OPENSKY_STATES_URL,
            params=bounds,
            timeout=(5, 15),
        )
    except requests.RequestException as error:
        raise OpenSkyRequestError("Could not connect to OpenSky.") from error

    if response.status_code == 429:
        raise OpenSkyRequestError("OpenSky's request limit was reached. Try again later.", status_code=429)
    if not response.ok:
        raise OpenSkyRequestError("OpenSky could not provide aircraft positions.")

    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError as error:
        raise OpenSkyRequestError("OpenSky returned an unreadable response.") from error
    if not isinstance(payload, dict):
        raise OpenSkyRequestError("OpenSky returned an unreadable response.")

    states = payload.get("states")
    if states is not None and not isinstance(states, list):
        raise OpenSkyRequestError("OpenSky returned an unreadable response.")
    return {"time": payload.get("time"), "states": states or []}
