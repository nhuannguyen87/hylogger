"""
Django settings. Most of what you'll want to change is at the top.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent  # the CITS5553/team folder

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
