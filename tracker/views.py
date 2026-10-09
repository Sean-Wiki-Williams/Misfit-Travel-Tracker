import logging
import secrets
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import requests
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db import DatabaseError, IntegrityError, transaction
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
from .models import Flight, ScheduleCache, User
from .adsb import AdsbLookupError, fetch_callsign_states, normalize_callsign, states_contain_callsign
from .opensky import OpenSkyRequestError, fetch_states, parse_bounding_box
from .services import persist_schedule, schedule_is_fresh
from .skylink import SkyLinkLookupError, lookup_flight as skylink_lookup_flight

logger = logging.getLogger(__name__)


def about(request):
    return render(request, "about.html")


def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            # A concurrent request registered this email after the form's uniqueness check.
            form.add_error("email", "A user with that email already exists. Try signing in.")
        else:
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
    return render(request, "index.html", {
        "shared": False,
        "flights_url": reverse("flights_api"),
        "opensky_url": reverse("opensky_states"),
    })


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
    return _opensky_response(request, request.user.pk)


def _opensky_response(request, user_id):
    try:
        bounds = parse_bounding_box(request.GET)
        callsign = normalize_callsign(request.GET.get("callsign"))
    except OpenSkyRequestError as error:
        return JsonResponse({"error": str(error)}, status=error.status_code)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    result = None
    opensky_error = None
    try:
        result = fetch_states(bounds)
        result["source"] = "opensky"
    except OpenSkyRequestError as error:
        opensky_error = error
        logger.warning(
            "OpenSky lookup failed for user %s (HTTP %s)",
            user_id,
            error.status_code,
        )

    if callsign and (result is None or not states_contain_callsign(result["states"], callsign)):
        try:
            fallback = fetch_callsign_states(callsign)
        except AdsbLookupError as error:
            logger.warning("adsb.lol lookup failed for user %s: %s", user_id, error)
        else:
            if fallback["states"] or result is None:
                result = {**fallback, "source": "adsb.lol"}

    if result is None:
        return JsonResponse({"error": str(opensky_error)}, status=opensky_error.status_code)
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
    force_refresh = request.GET.get("force", "0").lower() in ("1", "true")
    return _schedule_response(request.user, force_refresh)


def _schedule_response(user, force_refresh, public=False):
    manual_flights = list(Flight.objects.filter(user=user, is_manual=True))
    if not user.calendar_url and not manual_flights:
        if public:
            return JsonResponse({
                "success": False,
                "error": "This schedule is not available yet.",
            }, status=409)
        return JsonResponse({
            "success": False,
            "error": "Add an iCal feed or manually add a flight before loading your schedule.",
            "account_url": reverse("account"),
        }, status=409)

    cached_schedule = ScheduleCache.objects.filter(user=user).first()
    if not force_refresh and cached_schedule and schedule_is_fresh(cached_schedule):
        return JsonResponse(cached_schedule.payload)

    try:
        if user.calendar_url:
            raw_content = fetch_ical_content(user.calendar_url)
        else:
            raw_content = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
        raw_content = _add_manual_flights(raw_content, manual_flights)
        data = parse_calendar_data(raw_content)
    except (requests.RequestException, ValueError, TypeError, UnicodeDecodeError, InvalidCalendar) as error:
        logger.warning(
            "Calendar refresh failed for user %s (%s)",
            user.pk,
            type(error).__name__,
        )
        if cached_schedule:
            stale_data = dict(cached_schedule.payload)
            stale_data["stale"] = True
            stale_data["refresh_warning"] = "Could not refresh the feed; showing the last saved schedule."
            return JsonResponse(stale_data)
        return JsonResponse({
            "success": False,
            "error": (
                "The schedule is temporarily unavailable."
                if public
                else "Could not load the calendar feed. Check the feed URL and try again."
            ),
        }, status=502)

    all_stored_flights = data.pop("_stored_flights", data["flights"])
    stored_flights = [
        flight for flight in all_stored_flights
        if not flight.get("_ical_uid", "").startswith("manual:")
    ]
    for flight in all_stored_flights:
        flight.pop("_ical_uid", None)
    try:
        persist_schedule(user, data, stored_flights)
    except DatabaseError:
        logger.exception("Could not save schedule for user %s", user.pk)
        return JsonResponse({
            "success": False,
            "error": "The calendar was loaded but its schedule could not be saved.",
        }, status=500)
    return JsonResponse(data)


def _generate_share_token():
    while True:
        token = secrets.token_urlsafe(32)
        if not User.objects.filter(share_token=token).exists():
            return token


@login_required
@require_http_methods(["GET", "POST"])
def share(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("publish", "regenerate"):
            if action == "regenerate" or not request.user.share_token:
                request.user.share_token = _generate_share_token()
                request.user.save(update_fields=["share_token"])
            messages.success(request, "Your dashboard is published.")
        elif action == "unpublish":
            request.user.share_token = None
            request.user.save(update_fields=["share_token"])
            messages.success(request, "Your dashboard is no longer public.")
        return redirect("share")
    share_url = None
    if request.user.share_token:
        share_url = request.build_absolute_uri(
            reverse("shared_dashboard", args=[request.user.share_token])
        )
    return render(request, "share.html", {"share_url": share_url})


def _shared_user_or_404(token):
    return get_object_or_404(User, share_token=token, is_active=True)


def _private_response(response):
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def shared_dashboard(request, token):
    _shared_user_or_404(token)
    return _private_response(render(request, "index.html", {
        "shared": True,
        "flights_url": reverse("shared_flights_api", args=[token]),
        "opensky_url": reverse("shared_opensky_states", args=[token]),
    }))


@require_GET
def shared_flights_api(request, token):
    user = _shared_user_or_404(token)
    return _private_response(_schedule_response(user, force_refresh=False, public=True))


@require_GET
def shared_opensky_states(request, token):
    user = _shared_user_or_404(token)
    return _private_response(_opensky_response(request, user.pk))
