"""
Django settings. Most of what you'll want to change is at the top.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent  # the CITS5553/team folder

# .env lives at the project root (copied from .env.example by setup.sh), not
# next to this file - it's shared with the ETL scripts, not Django-only.
load_dotenv(PROJECT_ROOT / ".env")

# --- things you might actually change -------------------------------------

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-not-secret-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]  # fine for local dev; lock down before deploying

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "hylogger"),
        "USER": os.getenv("POSTGRES_USER", "hylogger"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "hylogger"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}

# Where CSVs are read from and where trained models are saved
DATA_DIR = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data"))
MODEL_DIR = Path(os.getenv("MODEL_DIR", BASE_DIR / "ml_models"))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# The ETL4 handoff package (database5553/) - full per-depth VSWIR/TIR spectra
# for 5 holes, restored into its own Postgres per database5553/docs/OPERATIONS.md.
# A sibling of PROJECT_ROOT, same pattern as load_core_trays' data5553/. Only
# affects holes/etl4_bridge.py; the rest of the site works fine without it.
ETL4_DATA_DIR = Path(os.getenv("ETL4_DATA_DIR", PROJECT_ROOT.parent / "database5553" / "etl4"))
ETL4_READ_DSN = os.getenv(
    "ETL4_READ_DSN",
    "host=localhost port=5434 dbname=etl4_core user=etl4_reader password=etl4reader sslmode=disable",
)
ETL4_RELEASE_ID = "fd02659c-dc3d-52ea-a8eb-c032b91e7624"

# The broader WA drill-hole catalog (~2,263 holes, position/name/depth only) -
# see holes/management/commands/load_catalog_holes.py. Also optional.
CATALOG_GEOJSON_PATH = Path(os.getenv(
    "CATALOG_GEOJSON_PATH",
    PROJECT_ROOT.parent / "database5553" / "wa-drillhole-map" / "public" / "holes.geojson",
))

# Raw TSG packages for 15 hand-collected exploration holes - see
# holes/tsg_bridge.py. A sibling of PROJECT_ROOT, like ETL4_DATA_DIR above,
# but note the name: data5553 (this), not database5553 (ETL4_DATA_DIR).
TSG_DATA_DIR = Path(os.getenv("TSG_DATA_DIR", PROJECT_ROOT.parent / "data5553"))

# Real core-tray photos, copied in by load_core_trays. Served directly by
# Django in dev (see hylogger/urls.py) - fine for a handful of holes; if this
# grows to the whole catalog, swap for an object store per CUSTOMISE.md.
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", DATA_DIR / "media"))
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)

# The Next.js dev server. Add your teammates' URLs here if they need access.
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
CORS_ALLOW_ALL_ORIGINS = DEBUG  # convenient locally, turn off in production

# --- standard Django wiring ------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "holes",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "hylogger.urls"
WSGI_APPLICATION = "hylogger.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",  # nice for exploring in a browser
    ]
}

LANGUAGE_CODE = "en-au"
TIME_ZONE = "Australia/Perth"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
