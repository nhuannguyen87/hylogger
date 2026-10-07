"""URL -> view map. Adding an endpoint? Add the function in views.py, then a line here."""
from django.urls import path

from . import views

urlpatterns = [
    path("holes/", views.hole_list),
    path("holes/<str:hole_id>/", views.hole_detail),
    path("holes/<str:hole_id>/measurements/", views.hole_measurements),
    path("holes/<str:hole_id>/anomalies/", views.hole_anomalies),
    path("holes/<str:hole_id>/trace/", views.hole_trace),
    path("holes/<str:hole_id>/nearby/", views.hole_nearby),
    path("distance/", views.distance),
    path("stats/", views.stats),
]
