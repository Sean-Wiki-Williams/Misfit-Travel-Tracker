from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("register/", views.register, name="register"),
    path("account/", views.account, name="account"),
    path("share/", views.share, name="share"),
    path("shared/<str:token>/", views.shared_dashboard, name="shared_dashboard"),
    path("shared/<str:token>/api/flights/", views.shared_flights_api, name="shared_flights_api"),
    path("shared/<str:token>/api/opensky/states/", views.shared_opensky_states, name="shared_opensky_states"),
    path("flights/add/", views.add_flight, name="add_flight"),
    path("flights/lookup/", views.lookup_flight, name="lookup_flight"),
    path("flights/<int:flight_id>/edit/", views.edit_flight, name="edit_flight"),
    path("flights/<int:flight_id>/delete/", views.delete_flight, name="delete_flight"),
    path("api/flights/", views.flights_api, name="flights_api"),
    path("api/data/", views.flights_api, name="data_api"),
    path("api/opensky/states/", views.opensky_states, name="opensky_states"),
]
