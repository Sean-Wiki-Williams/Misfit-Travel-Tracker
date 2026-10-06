import re
from datetime import date, datetime

import requests
from django.conf import settings
from django.utils import timezone

from .forms import AIRPORTS

BASE_URL = "https://data.skylinkapi.com/v3.1"
MONTH_ABBREVIATIONS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


class SkyLinkLookupError(Exception):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def _airport_iata(code):
    normalized = code.strip().upper()
    candidates = [normalized]
    candidates.extend(re.findall(r"(?<![A-Z0-9])([A-Z]{3,4})(?![A-Z0-9])", normalized))
    for candidate in candidates:
        if candidate in AIRPORTS:
            return candidate
        for iata, details in AIRPORTS.items():
            if details.get("icao", "").upper() == candidate:
                return iata
    raise SkyLinkLookupError("SkyLink returned an airport code we could not match.")


def _parse_date_label(value, reference):
    match = re.fullmatch(r"\s*(\d{1,2})\s+([A-Za-z]{3})\s*", value)
    if not match:
        raise SkyLinkLookupError("SkyLink returned an unsupported flight date.")
    day = int(match.group(1))
    month_name = match.group(2).title()
    try:
        month = MONTH_ABBREVIATIONS.index(month_name) + 1
    except ValueError:
        raise SkyLinkLookupError("SkyLink returned an unsupported flight date.")
    candidates = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        raise SkyLinkLookupError("SkyLink returned an invalid flight date.")
    return min(candidates, key=lambda candidate: abs((candidate - reference).days))


def _first_valid_time(data, *keys):
    for key in keys:
        value = _optional_text(data, key)
        if not value or value == "--:--":
            continue
        try:
            return datetime.strptime(value, "%H:%M").time()
        except ValueError:
            continue
    raise SkyLinkLookupError("SkyLink did not return a usable flight time.")


def _required_text(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkyLinkLookupError("SkyLink returned incomplete flight information.")
    return value.strip()


def _optional_text(data, key):
    value = data.get(key, "")
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise SkyLinkLookupError("SkyLink returned incomplete flight information.")
    return value.strip()


def lookup_flight(flight_number):
    if not settings.SKYLINK_API_KEY:
        raise SkyLinkLookupError(
            "Flight lookup is not configured. Set SKYLINK_API_KEY on the server.",
            status_code=503,
        )

    normalized_number = re.sub(r"\s+", "", flight_number).upper()
    if not re.fullmatch(r"[A-Z]{2,3}\d{1,4}", normalized_number):
        raise SkyLinkLookupError(
            "Enter a flight number with a 2- or 3-letter code and 1–4 digits.",
            status_code=400,
        )

    try:
        response = requests.get(
            f"{BASE_URL}/flight_status/{normalized_number}",
            headers={"x-api-key": settings.SKYLINK_API_KEY},
            timeout=(5, 15),
        )
    except requests.RequestException as error:
        raise SkyLinkLookupError("Could not connect to the SkyLink flight service.") from error

    if response.status_code == 404:
        raise SkyLinkLookupError(
            "SkyLink did not find an operating flight with that number. This lookup does not accept a date.",
            status_code=404,
        )
    if response.status_code in (401, 403):
        raise SkyLinkLookupError(
            "SkyLink rejected the server key or subscription. Check the configured API key and plan."
        )
    if response.status_code == 429:
        raise SkyLinkLookupError("SkyLink's request limit was reached. Please try again later.")
    if response.status_code >= 500:
        raise SkyLinkLookupError("SkyLink's flight service is temporarily unavailable.")
    if not response.ok:
        raise SkyLinkLookupError("SkyLink could not complete the flight lookup.")

    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError as error:
        raise SkyLinkLookupError("SkyLink returned an unreadable response.") from error
    if not isinstance(data, dict):
        raise SkyLinkLookupError("SkyLink returned an unreadable response.")

    departure = data.get("departure")
    arrival = data.get("arrival")
    if not isinstance(departure, dict) or not isinstance(arrival, dict):
        raise SkyLinkLookupError("SkyLink returned incomplete flight information.")

    origin_code = _airport_iata(_required_text(departure, "airport"))
    dest_code = _airport_iata(_required_text(arrival, "airport"))
    departure_date = _parse_date_label(
        _required_text(departure, "scheduled_date"),
        timezone.localdate(),
    )
    departure_time = _first_valid_time(departure, "scheduled_time", "actual_time")
    arrival_time = _first_valid_time(arrival, "estimated_time", "scheduled_time")
    arrival_date_label = _optional_text(arrival, "estimated_date") or _required_text(
        arrival, "scheduled_date"
    )
    arrival_date = _parse_date_label(arrival_date_label, departure_date)

    returned_flight_number = data.get("flight_number")
    if isinstance(returned_flight_number, str) and returned_flight_number.strip():
        normalized_number = re.sub(r"\s+", "", returned_flight_number).upper()

    return {
        "flight_number": normalized_number,
        "origin_code": origin_code,
        "dest_code": dest_code,
        "departure_date": departure_date.isoformat(),
        "departure_time": departure_time.isoformat(timespec="minutes"),
        "arrival_date": arrival_date.isoformat(),
        "arrival_time": arrival_time.isoformat(timespec="minutes"),
        "dep_gate": _optional_text(departure, "gate"),
        "arr_gate": _optional_text(arrival, "gate"),
        "status": _optional_text(data, "status"),
    }
