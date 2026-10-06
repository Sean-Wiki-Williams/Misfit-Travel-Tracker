import hashlib
from datetime import datetime

from django.db import transaction
from django.utils import timezone as django_timezone

from .models import Flight, ScheduleCache

CACHE_TTL_SECONDS = 60


def flight_database_key(flight):
    identity = "|".join((
        flight.get("flight", ""),
        flight.get("start", ""),
        flight.get("origin", {}).get("code", ""),
        flight.get("dest", {}).get("code", ""),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def schedule_is_fresh(schedule):
    return (django_timezone.now() - schedule.updated_at).total_seconds() < CACHE_TTL_SECONDS


@transaction.atomic
def persist_schedule(user, data, stored_flights):
    now = django_timezone.now()
    flights_by_key = {flight_database_key(flight): flight for flight in stored_flights}
    existing_flights = list(Flight.objects.filter(user=user, is_manual=False))
    existing_by_key = {flight.flight_key: flight for flight in existing_flights}

    for key, details in flights_by_key.items():
        flight = existing_by_key.get(key)
        if flight is None:
            flight = Flight(user=user, flight_key=key, is_manual=False)
        flight.flight_number = details["flight"]
        flight.origin_code = details["origin"]["code"]
        flight.dest_code = details["dest"]["code"]
        flight.start_time = datetime.fromisoformat(details["start"])
        flight.end_time = datetime.fromisoformat(details["end"])
        flight.equipment = details["equipment"]
        flight.details = details
        flight.save()

    obsolete_future = Flight.objects.filter(user=user, is_manual=False, end_time__gte=now)
    if flights_by_key:
        obsolete_future = obsolete_future.exclude(flight_key__in=flights_by_key)
    obsolete_future.delete()

    schedule, _ = ScheduleCache.objects.update_or_create(
        user=user,
        defaults={"payload": data, "updated_at": now},
    )
    return schedule
