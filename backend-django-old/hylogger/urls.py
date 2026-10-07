"""Top-level URL map. Everything useful lives under /api/ (see holes/urls.py)."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("holes.urls")),
]
