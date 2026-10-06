import logging
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import requests
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from icalendar import Calendar, Event
from icalendar.error import InvalidCalendar
from flight_parser import AIRPORTS, fetch_ical_content, parse_calendar_data

from .forms import (
    CalendarFeedForm,
    EmailAuthenticationForm,
    ManualFlightForm,
    RegistrationForm,
)
from .models import Flight, ScheduleCache
from .opensky import OpenSkyRequestError, fetch_states, parse_bounding_box
from .services import persist_schedule, schedule_is_fresh
from .skylink import SkyLinkLookupError, lookup_flight as skylink_lookup_flight

logger = logging.getLogger(__name__)


def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("account")
    return render(request, "register.html", {"form": form})


class EmailLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True


@login_required
@require_http_methods(["GET", "POST"])
def account(request):
    if request.method == "POST":
        form = CalendarFeedForm(request.POST)
        if form.is_valid():
            request.user.calendar_url = form.cleaned_data["calendar_url"]
            request.user.save(update_fields=["calendar_url"])
            ScheduleCache.objects.filter(user=request.user).delete()
            messages.success(request, "Calendar feed saved.")
            return redirect("home")
    else:
        form = CalendarFeedForm(initial={"calendar_url": request.user.calendar_url})
    return render(request, "account.html", {"form": form})


@login_required
@require_GET
def home(request):
    if not request.user.calendar_url and not Flight.objects.filter(
        user=request.user, is_manual=True
    ).exists():
        return redirect("account")
    return render(request, "index.html")


@login_required
@require_http_methods(["GET", "POST"])
def add_flight(request):
    if request.method == "POST":
        form = ManualFlightForm(request.POST)
        if form.is_valid():
            cleaned = form.cleaned_data
            flight_key = uuid.uuid4().hex
            Flight.objects.create(
                user=request.user,
                flight_key=flight_key,
                is_manual=True,
                flight_number=cleaned["flight_number"],
                origin_code=cleaned["origin_code"],
                dest_code=cleaned["dest_code"],
                start_time=cleaned["departure_at"],
                end_time=cleaned["arrival_at"],
                equipment=cleaned["equipment"],
                details={
                    "dep_gate": cleaned["dep_gate"],
                    "arr_gate": cleaned["arr_gate"],
                },
            )
            ScheduleCache.objects.filter(user=request.user).delete()
            messages.success(request, "Flight added to your schedule.")
            return redirect("home")
    else:
        form = ManualFlightForm()
    return render(
        request,
        "add-flight.html",
        {
            "form": form,
            "manual_flights": Flight.objects.filter(
                user=request.user, is_manual=True
            ).order_by("start_time"),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def edit_flight(request, flight_id):
    flight = get_object_or_404(
        Flight, pk=flight_id, user=request.user, is_manual=True
    )
    if request.method == "POST":
        form = ManualFlightForm(request.POST)
        if form.is_valid():
            cleaned = form.cleaned_data
            flight.flight_number = cleaned["flight_number"]
            flight.origin_code = cleaned["origin_code"]
            flight.dest_code = cleaned["dest_code"]
            flight.start_time = cleaned["departure_at"]
            flight.end_time = cleaned["arrival_at"]
            flight.equipment = cleaned["equipment"]
            flight.details = {
                "dep_gate": cleaned["dep_gate"],
                "arr_gate": cleaned["arr_gate"],
            }
            flight.save()
            ScheduleCache.objects.filter(user=request.user).delete()
            messages.success(request, "Flight updated.")
            return redirect("add_flight")
    else:
        origin_zone = ZoneInfo(AIRPORTS[flight.origin_code]["tz"])
        dest_zone = ZoneInfo(AIRPORTS[flight.dest_code]["tz"])
        departure = flight.start_time.astimezone(origin_zone)
        arrival = flight.end_time.astimezone(dest_zone)
        form = ManualFlightForm(initial={
            "flight_number": flight.flight_number,
            "origin_code": flight.origin_code,
            "dest_code": flight.dest_code,
            "departure_date": departure.date(),
            "departure_time": departure.time(),
            "arrival_date": arrival.date(),
            "arrival_time": arrival.time(),
            "equipment": flight.equipment,
            "dep_gate": flight.details.get("dep_gate", ""),
            "arr_gate": flight.details.get("arr_gate", ""),
        })
    return render(
        request,
        "add-flight.html",
        {
            "form": form,
            "manual_flights": Flight.objects.filter(
                user=request.user, is_manual=True
            ).order_by("start_time"),
            "form_title": f"Edit {flight.flight_number}",
            "submit_label": "Save changes",
        },
    )


@login_required
@require_http_methods(["POST"])
def delete_flight(request, flight_id):
    flight = get_object_or_404(
        Flight, pk=flight_id, user=request.user, is_manual=True
    )
    flight.delete()
    ScheduleCache.objects.filter(user=request.user).delete()
    messages.success(request, "Manually added flight removed.")
    return redirect("add_flight")


@login_required
@require_http_methods(["POST"])
def lookup_flight(request):
    try:
        result = skylink_lookup_flight(request.POST.get("flight_number", ""))
    except SkyLinkLookupError as error:
        logger.warning(
            "SkyLink lookup failed for user %s (HTTP %s)",
            request.user.pk,
            error.status_code,
        )
        return JsonResponse({"error": str(error)}, status=error.status_code)
    return JsonResponse({"flight": result})


@login_required
@require_GET
def opensky_states(request):
    try:
        bounds = parse_bounding_box(request.GET)
        result = fetch_states(bounds)
    except OpenSkyRequestError as error:
        logger.warning(
            "OpenSky lookup failed for user %s (HTTP %s)",
            request.user.pk,
            error.status_code,
        )
        return JsonResponse({"error": str(error)}, status=error.status_code)
    return JsonResponse(result)


def _add_manual_flights(calendar_bytes, manual_flights):
    calendar = Calendar.from_ical(calendar_bytes)
    for flight in manual_flights:
        origin_zone = ZoneInfo(AIRPORTS[flight.origin_code]["tz"])
        dest_zone = ZoneInfo(AIRPORTS[flight.dest_code]["tz"])
        event = Event()
        event.add("uid", f"manual:{flight.flight_key}")
        event.add("dtstamp", datetime.now(UTC))
        event.add("dtstart", flight.start_time.astimezone(origin_zone))
        event.add("dtend", flight.end_time.astimezone(dest_zone))
        origin_country = AIRPORTS[flight.origin_code].get("country")
        dest_country = AIRPORTS[flight.dest_code].get("country")
        prefix = "D" if origin_country == dest_country == "US" else "I"
        event.add(
            "summary",
            f"{prefix} {flight.flight_number} : {flight.origin_code} - {flight.dest_code}",
        )
        description = []
        if flight.equipment:
            description.append(f"Eqp/Ship- {flight.equipment}")
        dep_gate = flight.details.get("dep_gate", "")
        arr_gate = flight.details.get("arr_gate", "")
        if dep_gate:
            description.append(f"Dep- Gate- {dep_gate}")
        if arr_gate:
            description.append(f"Arr- Gate- {arr_gate}")
        if description:
            event.add("description", "\n".join(description))
        calendar.add_component(event)
    return calendar.to_ical()


@require_GET
def flights_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "error": "Authentication required."}, status=401)
    manual_flights = list(Flight.objects.filter(user=request.user, is_manual=True))
    if not request.user.calendar_url and not manual_flights:
        return JsonResponse({
            "success": False,
            "error": "Add an iCal feed or manually add a flight before loading your schedule.",
            "account_url": reverse("account"),
        }, status=409)

    cached_schedule = ScheduleCache.objects.filter(user=request.user).first()
    force_refresh = request.GET.get("force", "0").lower() in ("1", "true")
    if not force_refresh and cached_schedule and schedule_is_fresh(cached_schedule):
        return JsonResponse(cached_schedule.payload)

    try:
        if request.user.calendar_url:
            raw_content = fetch_ical_content(request.user.calendar_url)
        else:
            raw_content = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
        raw_content = _add_manual_flights(raw_content, manual_flights)
        data = parse_calendar_data(raw_content)
    except (requests.RequestException, ValueError, TypeError, UnicodeDecodeError, InvalidCalendar) as error:
        logger.warning(
            "Calendar refresh failed for user %s (%s)",
            request.user.pk,
            type(error).__name__,
        )
        if cached_schedule:
            stale_data = dict(cached_schedule.payload)
            stale_data["stale"] = True
            stale_data["refresh_warning"] = "Could not refresh the feed; showing the last saved schedule."
            return JsonResponse(stale_data)
        return JsonResponse({
            "success": False,
            "error": "Could not load the calendar feed. Check the feed URL and try again.",
        }, status=502)

    all_stored_flights = data.pop("_stored_flights", data["flights"])
    stored_flights = [
        flight for flight in all_stored_flights
        if not flight.get("_ical_uid", "").startswith("manual:")
    ]
    for flight in all_stored_flights:
        flight.pop("_ical_uid", None)
    try:
        persist_schedule(request.user, data, stored_flights)
    except DatabaseError:
        logger.exception("Could not save schedule for user %s", request.user.pk)
        return JsonResponse({
            "success": False,
            "error": "The calendar was loaded but its schedule could not be saved.",
        }, status=500)
    return JsonResponse(data)
