#!/usr/bin/env python
"""Django's command-line utility. Run `python manage.py help` to see everything."""
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hylogger.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
