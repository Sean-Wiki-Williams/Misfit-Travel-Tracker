from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Flight, ScheduleCache, User


def calendar_with_flight(flight_number, origin, dest, days_from_now=1):
    start = datetime.now(timezone.utc) + timedelta(days=days_from_now)
    end = start + timedelta(hours=2)
    calendar = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Flight Tracker Tests//EN
BEGIN:VEVENT
UID:{flight_number}@test
DTSTAMP:{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}
DTSTART:{start.strftime("%Y%m%dT%H%M%SZ")}
DTEND:{end.strftime("%Y%m%dT%H%M%SZ")}
SUMMARY:I DL{flight_number} : {origin} - {dest}
DESCRIPTION:Eqp/Ship- 739/N12345
END:VEVENT
END:VCALENDAR
"""
    return calendar.encode("utf-8")


class UserScheduleTests(TestCase):
    def create_user(self, email):
        user = User.objects.create_user(email=email, password="a-strong-test-password")
        self.client.force_login(user)
        return user

    def save_feed(self, url):
        return self.client.post(reverse("account"), {"calendar_url": url})

    def manual_flight_data(self, flight_number="AA321"):
        date = (datetime.now(timezone.utc) + timedelta(days=5)).date()
        return {
            "flight_number": flight_number,
            "origin_code": "ATL",
            "dest_code": "LAX",
            "departure_date": date.isoformat(),
            "departure_time": "09:00",
            "arrival_date": date.isoformat(),
            "arrival_time": "11:00",
            "equipment": "739",
            "dep_gate": "A12",
            "arr_gate": "B4",
        }

    def test_schedule_is_persisted_and_isolated_by_user(self):
        first_user = self.create_user("first@example.com")
        self.save_feed("webcal://p01-caldav.icloud.com/calendar/first")
        first_feed = calendar_with_flight("1111", "ATL", "LAX")
        updated_first_feed = calendar_with_flight("1111", "ATL", "SFO")

        with patch("tracker.views.fetch_ical_content", side_effect=[first_feed, updated_first_feed]):
            first_response = self.client.get(reverse("flights_api"), {"force": "1"})
            self.assertEqual(first_response.status_code, 200)
            self.assertEqual(first_response.json()["flights"][0]["route"], "ATL → LAX")

            updated_response = self.client.get(reverse("flights_api"), {"force": "1"})
            self.assertEqual(updated_response.status_code, 200)
            self.assertEqual(updated_response.json()["flights"][0]["route"], "ATL → SFO")

        self.client.logout()
        second_user = User.objects.create_user(email="second@example.com", password="a-strong-test-password")
        self.client.force_login(second_user)
        self.save_feed("https://p02-caldav.icloud.com/calendar/second")
        second_feed = calendar_with_flight("2222", "LAX", "SEA")
        with patch("tracker.views.fetch_ical_content", return_value=second_feed):
            response = self.client.get(reverse("flights_api"), {"force": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["flights"][0]["route"], "LAX → SEA")

        self.assertEqual(Flight.objects.filter(user=first_user).count(), 1)
        self.assertEqual(Flight.objects.filter(user=second_user).count(), 1)
        self.assertEqual(
            ScheduleCache.objects.get(user=first_user).payload["flights"][0]["route"],
            "ATL → SFO",
        )
        self.assertNotEqual(first_user.password, "a-strong-test-password")

    def test_schedule_requires_authentication_and_feed(self):
        response = self.client.get(reverse("flights_api"))
        self.assertEqual(response.status_code, 401)
        self.assertRedirects(
            self.client.get(reverse("home")),
            f"{reverse('login')}?next=/",
        )
        user = self.create_user("setup@example.com")
        response = self.client.get(reverse("flights_api"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["account_url"], reverse("account"))
        self.assertEqual(self.client.get("/flight_parser.py").status_code, 404)
        self.assertEqual(user.email, "setup@example.com")

    def test_registration_and_email_login(self):
        response = self.client.post(reverse("register"), {
            "email": "New.User@example.com",
            "password1": "a-strong-test-password",
            "password2": "a-strong-test-password",
        })
        self.assertRedirects(response, reverse("account"))
        self.assertTrue(User.objects.filter(email="new.user@example.com").exists())
        self.save_feed("https://p01-caldav.icloud.com/calendar/login")
        self.client.post(reverse("logout"))
        response = self.client.post(reverse("login"), {
            "username": "New.User@EXAMPLE.COM",
            "password": "a-strong-test-password",
        })
        self.assertRedirects(response, reverse("home"))

    def test_registration_race_shows_error_instead_of_server_error(self):
        from django.db import IntegrityError

        with patch("tracker.forms.RegistrationForm.save", side_effect=IntegrityError("UNIQUE")):
            response = self.client.post(reverse("register"), {
                "email": "race@example.com",
                "password1": "a-strong-test-password",
                "password2": "a-strong-test-password",
            })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")

    def test_csrf_protection(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(reverse("register"), {
            "email": "csrf@example.com",
            "password1": "a-strong-test-password",
            "password2": "a-strong-test-password",
        })
        self.assertEqual(response.status_code, 403)

    def test_feed_url_validation_and_account_page(self):
        self.create_user("feed@example.com")
        response = self.save_feed("https://127.0.0.1/private")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"HTTPS calendar URL from an allowed calendar provider", response.content)
        response = self.save_feed("webcal://p01-caldav.icloud.com/calendar/token")
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(
            User.objects.get(email="feed@example.com").calendar_url,
            "https://p01-caldav.icloud.com/calendar/token",
        )
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

    def test_completed_flights_are_saved_but_not_returned(self):
        user = self.create_user("history@example.com")
        self.save_feed("https://p01-caldav.icloud.com/calendar/history")
        old_feed = calendar_with_flight("3333", "SEA", "LAX", days_from_now=-3)
        with patch("tracker.views.fetch_ical_content", return_value=old_feed):
            response = self.client.get(reverse("flights_api"), {"force": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["flights"], [])
        self.assertNotIn("_stored_flights", response.json())
        self.assertEqual(Flight.objects.filter(user=user).count(), 1)

    def test_manual_flight_uses_airport_time_zones_and_loads_without_feed(self):
        user = self.create_user("manual@example.com")
        data = self.manual_flight_data()
        response = self.client.post(reverse("add_flight"), data)
        self.assertRedirects(response, reverse("home"))

        flight = Flight.objects.get(user=user, is_manual=True)
        expected_departure = datetime.fromisoformat(
            f"{data['departure_date']}T09:00"
        ).replace(tzinfo=ZoneInfo("America/New_York"))
        expected_arrival = datetime.fromisoformat(
            f"{data['arrival_date']}T11:00"
        ).replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        self.assertEqual(flight.start_time, expected_departure)
        self.assertEqual(flight.end_time, expected_arrival)
        self.assertEqual(flight.details, {"dep_gate": "A12", "arr_gate": "B4"})

        response = self.client.get(reverse("flights_api"), {"force": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["flights"]), 1)
        saved = response.json()["flights"][0]
        self.assertEqual(saved["flight"], "AA321")
        self.assertEqual(saved["route"], "ATL → LAX")
        self.assertEqual(saved["dep_gate"], "A12")
        self.assertEqual(saved["arr_gate"], "B4")
        self.assertNotIn("_ical_uid", saved)

    def test_manual_flight_can_arrive_the_day_after_departure(self):
        user = self.create_user("overnight@example.com")
        data = self.manual_flight_data()
        departure_date = datetime.fromisoformat(data["departure_date"]).date()
        arrival_date = departure_date + timedelta(days=1)
        data["arrival_date"] = arrival_date.isoformat()

        response = self.client.post(reverse("add_flight"), data)
        self.assertRedirects(response, reverse("home"))

        flight = Flight.objects.get(user=user, is_manual=True)
        self.assertEqual(
            flight.start_time.astimezone(ZoneInfo("America/New_York")).date(),
            departure_date,
        )
        self.assertEqual(
            flight.end_time.astimezone(ZoneInfo("America/Los_Angeles")).date(),
            arrival_date,
        )
        dashboard = self.client.get(reverse("home"))
        self.assertContains(dashboard, "formatFlightDate(f.end)")
        self.assertContains(dashboard, "formatFlightDate(flight.end)")

    def test_flight_landing_today_is_listed_on_todays_date(self):
        self.create_user("redeye@example.com")
        self.save_feed("https://p01-caldav.icloud.com/calendar/redeye")
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(hours=2)
        end = today + timedelta(hours=3)
        feed = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Flight Tracker Tests//EN
BEGIN:VEVENT
UID:5555@test
DTSTAMP:{today.strftime("%Y%m%dT%H%M%SZ")}
DTSTART:{start.strftime("%Y%m%dT%H%M%SZ")}
DTEND:{end.strftime("%Y%m%dT%H%M%SZ")}
SUMMARY:I DL5555 : ATL - LAX
DESCRIPTION:Eqp/Ship- 739/N12345
END:VEVENT
END:VCALENDAR
""".encode("utf-8")
        with patch("tracker.views.fetch_ical_content", return_value=feed):
            data = self.client.get(reverse("flights_api"), {"force": "1"}).json()

        flight = data["flights"][0]
        self.assertEqual(flight["date_key"], start.strftime("%Y-%m-%d"))
        self.assertEqual(flight["end_date_key"], today.strftime("%Y-%m-%d"))
        date_keys = {d["date_key"] for d in data["available_dates"]}
        self.assertEqual(
            date_keys,
            {start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")},
        )

    def test_manual_flights_survive_refresh_and_merge_with_calendar(self):
        user = self.create_user("combined@example.com")
        self.save_feed("https://p01-caldav.icloud.com/calendar/combined")
        self.client.post(reverse("add_flight"), self.manual_flight_data())
        feed = calendar_with_flight("4444", "LAX", "SEA")

        with patch("tracker.views.fetch_ical_content", return_value=feed):
            first_response = self.client.get(reverse("flights_api"), {"force": "1"})
            second_response = self.client.get(reverse("flights_api"), {"force": "1"})

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(
            {flight["flight"] for flight in second_response.json()["flights"]},
            {"AA321", "DL4444"},
        )
        self.assertEqual(Flight.objects.filter(user=user, is_manual=True).count(), 1)
        self.assertEqual(Flight.objects.filter(user=user, is_manual=False).count(), 1)

    def test_manual_flight_can_be_edited_and_invalidates_schedule_cache(self):
        user = self.create_user("edit@example.com")
        self.client.post(reverse("add_flight"), self.manual_flight_data())
        flight = Flight.objects.get(user=user, is_manual=True)
        self.client.get(reverse("flights_api"), {"force": "1"})
        self.assertTrue(ScheduleCache.objects.filter(user=user).exists())

        response = self.client.get(reverse("edit_flight", args=[flight.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="AA321"')
        self.assertContains(response, 'value="09:00:00"')

        updated_data = self.manual_flight_data(flight_number="UA456")
        updated_data.update({
            "origin_code": "LAX",
            "dest_code": "SEA",
            "departure_time": "12:15",
            "arrival_time": "15:30",
            "equipment": "7M8",
            "dep_gate": "C2",
            "arr_gate": "D8",
        })
        response = self.client.post(
            reverse("edit_flight", args=[flight.pk]),
            updated_data,
        )

        self.assertRedirects(response, reverse("add_flight"))
        flight.refresh_from_db()
        self.assertEqual(flight.flight_number, "UA456")
        self.assertEqual(flight.origin_code, "LAX")
        self.assertEqual(flight.dest_code, "SEA")
        self.assertEqual(
            flight.start_time.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%H:%M"),
            "12:15",
        )
        self.assertEqual(
            flight.end_time.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%H:%M"),
            "15:30",
        )
        self.assertEqual(flight.equipment, "7M8")
        self.assertEqual(flight.details, {"dep_gate": "C2", "arr_gate": "D8"})
        self.assertFalse(ScheduleCache.objects.filter(user=user).exists())

    def test_manual_flight_validation_rejects_unknown_airports_and_dst_gap(self):
        self.create_user("validation@example.com")
        data = self.manual_flight_data()
        data["origin_code"] = "ZZZ"
        response = self.client.post(reverse("add_flight"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "valid three-letter IATA airport code")

        data = self.manual_flight_data()
        data.update({
            "departure_date": "2026-03-08",
            "departure_time": "02:30",
            "arrival_date": "2026-03-08",
            "arrival_time": "08:00",
        })
        response = self.client.post(reverse("add_flight"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not exist because of a daylight-saving")
        self.assertFalse(Flight.objects.filter(is_manual=True).exists())

    @override_settings(SKYLINK_API_KEY="rotated-test-key")
    def test_skylink_lookup_populates_flight_fields_server_side(self):
        self.create_user("lookup@example.com")
        today = datetime.now(timezone.utc).date()
        tomorrow = today + timedelta(days=1)
        api_response = Mock()
        api_response.status_code = 200
        api_response.ok = True
        api_response.json.return_value = {
            "flight_number": "AA 100",
            "status": "Scheduled",
            "departure": {
                "airport": "VPS � Destin/Fort Walton Beach",
                "scheduled_date": today.strftime("%d %b"),
                "scheduled_time": "09:15",
                "actual_time": "--:--",
                "gate": "A8",
            },
            "arrival": {
                "airport": "KLAX",
                "scheduled_date": tomorrow.strftime("%d %b"),
                "scheduled_time": "11:30",
                "estimated_date": "",
                "estimated_time": "--:--",
                "gate": "B6",
            },
        }

        with patch("tracker.skylink.requests.get", return_value=api_response) as get:
            response = self.client.post(
                reverse("lookup_flight"),
                {"flight_number": "aa 100"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["flight"], {
            "flight_number": "AA100",
            "origin_code": "VPS",
            "dest_code": "LAX",
            "departure_date": today.isoformat(),
            "departure_time": "09:15",
            "arrival_date": tomorrow.isoformat(),
            "arrival_time": "11:30",
            "dep_gate": "A8",
            "arr_gate": "B6",
            "status": "Scheduled",
        })
        self.assertEqual(get.call_args.args[0], "https://data.skylinkapi.com/v3.1/flight_status/AA100")
        self.assertEqual(get.call_args.kwargs["headers"], {"x-api-key": "rotated-test-key"})
        self.assertEqual(get.call_args.kwargs["timeout"], (5, 15))

    @override_settings(SKYLINK_API_KEY="")
    def test_skylink_lookup_requires_server_key(self):
        self.create_user("missing-key@example.com")

        with patch("tracker.skylink.requests.get") as get:
            response = self.client.post(
                reverse("lookup_flight"),
                {"flight_number": "AA100"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Set SKYLINK_API_KEY", response.json()["error"])
        get.assert_not_called()

    def test_skylink_lookup_requires_authentication(self):
        response = self.client.post(
            reverse("lookup_flight"),
            {"flight_number": "AA100"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_opensky_states_proxy_is_authenticated_and_forwards_bounded_query(self):
        self.create_user("opensky@example.com")
        api_response = Mock()
        api_response.status_code = 200
        api_response.ok = True
        api_response.json.return_value = {
            "time": 1234567890,
            "states": [["abc123", "DAL2170", "United States", 1234567880, 1234567890,
                        -85.0, 34.0, 10000.0, False, 230.0, 75.0, 0.0, None, 10100.0, None, False, 0]],
        }

        with patch("tracker.opensky.requests.get", return_value=api_response) as get:
            response = self.client.get(reverse("opensky_states"), {
                "lamin": "25",
                "lomin": "-90",
                "lamax": "50",
                "lomax": "-60",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["states"][0][1], "DAL2170")
        self.assertEqual(
            get.call_args.kwargs["params"],
            {"lamin": 25.0, "lomin": -90.0, "lamax": 50.0, "lomax": -60.0},
        )

    def test_opensky_states_proxy_rejects_invalid_bounds(self):
        self.create_user("opensky-validation@example.com")

        with patch("tracker.opensky.requests.get") as get:
            response = self.client.get(reverse("opensky_states"), {
                "lamin": "50",
                "lomin": "-90",
                "lamax": "25",
                "lomax": "-60",
            })

        self.assertEqual(response.status_code, 400)
        get.assert_not_called()

    def route_requests(self, opensky_response, adsb_response):
        calls = []

        def fake_get(url, *args, **kwargs):
            calls.append(url)
            return adsb_response if "adsb.lol" in url else opensky_response

        return fake_get, calls

    def test_opensky_failure_falls_back_to_adsb_lol_for_callsign(self):
        self.create_user("adsb-fallback@example.com")
        opensky_response = Mock(status_code=429, ok=False)
        adsb_response = Mock(status_code=200, ok=True)
        adsb_response.json.return_value = {"ac": [{
            "hex": "a7f684", "flight": "DAL653  ", "lat": 33.8, "lon": -85.5,
            "alt_baro": 24250, "gs": 400, "track": 273.1,
        }]}
        fake_get, calls = self.route_requests(opensky_response, adsb_response)

        with patch("requests.get", side_effect=fake_get):
            response = self.client.get(reverse("opensky_states"), {
                "lamin": "25", "lomin": "-90", "lamax": "50", "lomax": "-60",
                "callsign": "dal653",
            })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["source"], "adsb.lol")
        state = data["states"][0]
        self.assertEqual((state[1], state[5], state[6], state[10]), ("DAL653", -85.5, 33.8, 273.1))
        self.assertTrue(calls[-1].endswith("/DAL653"))

    def test_adsb_lol_not_used_when_opensky_has_the_callsign(self):
        self.create_user("adsb-unused@example.com")
        opensky_response = Mock(status_code=200, ok=True)
        opensky_response.json.return_value = {
            "time": 1,
            "states": [["abc123", "DAL653", "US", 1, 1, -85.0, 34.0, 1.0, False, 1.0, 75.0]],
        }
        fake_get, calls = self.route_requests(opensky_response, Mock())

        with patch("requests.get", side_effect=fake_get):
            response = self.client.get(reverse("opensky_states"), {
                "lamin": "25", "lomin": "-90", "lamax": "50", "lomax": "-60",
                "callsign": "DAL653",
            })

        self.assertEqual(response.json()["source"], "opensky")
        self.assertFalse(any("adsb.lol" in url for url in calls))

    def test_opensky_error_returned_when_adsb_lol_also_fails(self):
        self.create_user("adsb-both-fail@example.com")
        fake_get, _ = self.route_requests(
            Mock(status_code=429, ok=False),
            Mock(status_code=500, ok=False),
        )

        with patch("requests.get", side_effect=fake_get):
            response = self.client.get(reverse("opensky_states"), {
                "lamin": "25", "lomin": "-90", "lamax": "50", "lomax": "-60",
                "callsign": "DAL653",
            })

        self.assertEqual(response.status_code, 429)

    def test_opensky_states_proxy_rejects_invalid_callsign(self):
        self.create_user("adsb-invalid@example.com")

        with patch("requests.get") as get:
            response = self.client.get(reverse("opensky_states"), {
                "lamin": "25", "lomin": "-90", "lamax": "50", "lomax": "-60",
                "callsign": "../etc",
            })

        self.assertEqual(response.status_code, 400)
        get.assert_not_called()

    def test_opensky_states_proxy_requires_authentication(self):
        response = self.client.get(reverse("opensky_states"), {
            "lamin": "25",
            "lomin": "-90",
            "lamax": "50",
            "lomax": "-60",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_manual_flight_delete_is_scoped_to_owner(self):
        owner = self.create_user("owner@example.com")
        response = self.client.post(reverse("add_flight"), self.manual_flight_data())
        self.assertRedirects(response, reverse("home"))
        flight = Flight.objects.get(user=owner, is_manual=True)

        self.client.logout()
        other_user = User.objects.create_user(
            email="other@example.com",
            password="a-strong-test-password",
        )
        self.client.force_login(other_user)
        response = self.client.get(reverse("edit_flight", args=[flight.pk]))
        self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse("delete_flight", args=[flight.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Flight.objects.filter(pk=flight.pk, user=owner).exists())


class SharedDashboardTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@example.com", password="a-strong-test-password")
        self.owner.calendar_url = "https://p01-caldav.icloud.com/calendar/owner"
        self.owner.save()
        self.client.force_login(self.owner)

    def publish(self):
        self.client.post(reverse("share"), {"action": "publish"})
        self.owner.refresh_from_db()
        return self.owner.share_token

    def test_share_requires_login_and_starts_unpublished(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("share")).status_code, 302)
        self.assertEqual(self.client.post(reverse("share"), {"action": "publish"}).status_code, 302)
        self.owner.refresh_from_db()
        self.assertIsNone(self.owner.share_token)

    def test_publish_gives_anonymous_read_only_access(self):
        token = self.publish()
        self.assertTrue(token)
        self.assertContains(self.client.get(reverse("share")), token)

        feed = calendar_with_flight("4444", "ATL", "LAX")
        anonymous = Client()
        with patch("tracker.views.fetch_ical_content", return_value=feed):
            page = anonymous.get(reverse("shared_dashboard", args=[token]))
            api = anonymous.get(reverse("shared_flights_api", args=[token]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, reverse("add_flight"))
        self.assertNotContains(page, reverse("account"))
        self.assertEqual(page["Referrer-Policy"], "no-referrer")
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["flights"][0]["route"], "ATL → LAX")
        self.assertNotIn(self.owner.email, api.content.decode())

        for name in ("shared_dashboard", "shared_flights_api"):
            response = anonymous.post(reverse(name, args=[token]))
            self.assertEqual(response.status_code, 405)

    def test_shared_api_never_forces_a_refresh(self):
        token = self.publish()
        feed = calendar_with_flight("5555", "ATL", "LAX")
        with patch("tracker.views.fetch_ical_content", return_value=feed) as fetch:
            Client().get(reverse("shared_flights_api", args=[token]) + "?force=1")
            Client().get(reverse("shared_flights_api", args=[token]) + "?force=1")
        self.assertEqual(fetch.call_count, 1)

    def test_unpublish_and_regenerate_revoke_old_link(self):
        old_token = self.publish()
        self.client.post(reverse("share"), {"action": "regenerate"})
        self.owner.refresh_from_db()
        self.assertNotEqual(old_token, self.owner.share_token)
        self.assertEqual(Client().get(reverse("shared_dashboard", args=[old_token])).status_code, 404)

        new_token = self.owner.share_token
        self.client.post(reverse("share"), {"action": "unpublish"})
        self.owner.refresh_from_db()
        self.assertIsNone(self.owner.share_token)
        for name in ("shared_dashboard", "shared_flights_api", "shared_opensky_states"):
            self.assertEqual(Client().get(reverse(name, args=[new_token])).status_code, 404)

    def test_unknown_token_is_not_found(self):
        self.assertEqual(Client().get(reverse("shared_dashboard", args=["nope"])).status_code, 404)

    def test_private_dashboard_still_requires_login(self):
        self.publish()
        anonymous = Client()
        self.assertEqual(anonymous.get(reverse("home")).status_code, 302)
        self.assertEqual(anonymous.get(reverse("flights_api")).status_code, 401)


class AboutPageTests(TestCase):
    def test_about_is_public_and_has_no_ads(self):
        response = self.client.get(reverse('about'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'adsbygoogle.js')

class TravelerNameTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="n@example.com", password="pw-12345-abc")
        self.user.calendar_url = "https://p01-caldav.icloud.com/x.ics"
        self.user.save()

    def test_default_name_is_dad(self):
        self.assertEqual(self.user.display_name, "Dad")

    def test_account_saves_name_and_shared_page_uses_it(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("account"), {
            "calendar_url": "https://p01-caldav.icloud.com/x.ics",
            "traveler_name": "Grandpa Joe",
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.traveler_name, "Grandpa Joe")
        self.user.share_token = "tok123"
        self.user.save()
        self.client.logout()
        self.assertContains(self.client.get(reverse("shared_dashboard", args=["tok123"])), "Grandpa Joe’s Travel Tracker")

    def test_name_is_escaped(self):
        self.user.traveler_name = "<b>x</b>"
        self.user.share_token = "tok456"
        self.user.save()
        body = self.client.get(reverse("shared_dashboard", args=["tok456"])).content.decode()
        self.assertNotIn("<b>x</b>", body)
