import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import airportsdata
from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from flight_parser import normalize_ical_url

from .models import User

AIRPORTS = airportsdata.load("IATA")


class RegistrationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("email",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class EmailAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Email"
        self.fields["username"].widget = forms.EmailInput(attrs={
            "autocomplete": "email",
            "maxlength": 320,
            "class": "form-control",
        })
        self.fields["password"].widget.attrs["class"] = "form-control"

    def clean(self):
        email = self.cleaned_data.get("username")
        if email:
            self.cleaned_data["username"] = email.strip().lower()
        return super().clean()


class CalendarFeedForm(forms.Form):
    calendar_url = forms.CharField(
        max_length=4096,
        widget=forms.URLInput(attrs={
            "autocomplete": "url",
            "placeholder": "webcal://...",
            "class": "form-control",
        }),
    )

    def clean_calendar_url(self):
        try:
            return normalize_ical_url(self.cleaned_data["calendar_url"])
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error


class ManualFlightForm(forms.Form):
    flight_number = forms.CharField(max_length=16, label="Flight number")
    origin_code = forms.CharField(max_length=3, label="Origin airport (IATA)")
    dest_code = forms.CharField(max_length=3, label="Destination airport (IATA)")
    departure_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Departure date",
    )
    departure_time = forms.TimeField(
        widget=forms.TimeInput(attrs={"type": "time"}),
        label="Departure time",
    )
    arrival_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Arrival date",
    )
    arrival_time = forms.TimeField(
        widget=forms.TimeInput(attrs={"type": "time"}),
        label="Arrival time",
    )
    equipment = forms.CharField(max_length=16, required=False, label="Equipment")
    dep_gate = forms.CharField(max_length=8, required=False, label="Departure gate")
    arr_gate = forms.CharField(max_length=8, required=False, label="Arrival gate")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean_flight_number(self):
        value = re.sub(r"\s+", "", self.cleaned_data["flight_number"]).upper()
        if not re.fullmatch(r"[A-Z]{2,3}\d{1,4}", value):
            raise forms.ValidationError("Enter a flight number with a 2- or 3-letter airline code and 1–4 digits.")
        return value

    def clean_equipment(self):
        value = self.cleaned_data["equipment"].strip().upper()
        if value and not re.fullmatch(r"[A-Z0-9]+(?:/[A-Z0-9]+)?", value):
            raise forms.ValidationError("Use letters and numbers, with an optional slash-separated equipment code.")
        return value

    def clean_dep_gate(self):
        return self._clean_gate("dep_gate")

    def clean_arr_gate(self):
        return self._clean_gate("arr_gate")

    def _clean_gate(self, field_name):
        value = self.cleaned_data[field_name].strip().upper()
        if value and not re.fullmatch(r"[A-Z0-9]{1,8}", value):
            raise forms.ValidationError("Use 1–8 letters or numbers for the gate.")
        return value

    def _airport_code(self, field_name):
        value = self.cleaned_data[field_name].strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", value) or value not in AIRPORTS:
            raise forms.ValidationError("Enter a valid three-letter IATA airport code.")
        airport = AIRPORTS[value]
        if not airport.get("tz"):
            raise forms.ValidationError("This airport does not have a known time zone.")
        try:
            ZoneInfo(airport["tz"])
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise forms.ValidationError("This airport's time zone is unavailable.") from error
        return value

    def clean_origin_code(self):
        return self._airport_code("origin_code")

    def clean_dest_code(self):
        return self._airport_code("dest_code")

    @staticmethod
    def _airport_local_datetime(code, date, time):
        zone = ZoneInfo(AIRPORTS[code]["tz"])
        local_value = datetime.combine(date, time)
        # For repeated fall-back times, use the first occurrence (fold=0).
        aware_value = local_value.replace(tzinfo=zone, fold=0)
        round_trip = aware_value.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if round_trip != local_value:
            raise forms.ValidationError(
                "This local time does not exist because of a daylight-saving clock change."
            )
        return aware_value

    def clean(self):
        cleaned_data = super().clean()
        origin = cleaned_data.get("origin_code")
        dest = cleaned_data.get("dest_code")
        departure_date = cleaned_data.get("departure_date")
        departure_time = cleaned_data.get("departure_time")
        arrival_date = cleaned_data.get("arrival_date")
        arrival_time = cleaned_data.get("arrival_time")

        if origin and departure_date and departure_time:
            try:
                cleaned_data["departure_at"] = self._airport_local_datetime(
                    origin, departure_date, departure_time
                )
            except forms.ValidationError as error:
                self.add_error("departure_time", error)
        if dest and arrival_date and arrival_time:
            try:
                cleaned_data["arrival_at"] = self._airport_local_datetime(
                    dest, arrival_date, arrival_time
                )
            except forms.ValidationError as error:
                self.add_error("arrival_time", error)

        departure_at = cleaned_data.get("departure_at")
        arrival_at = cleaned_data.get("arrival_at")
        if (
            departure_at
            and arrival_at
            and arrival_at.astimezone(UTC) <= departure_at.astimezone(UTC)
        ):
            self.add_error("arrival_time", "Arrival must be later than departure.")

        return cleaned_data
