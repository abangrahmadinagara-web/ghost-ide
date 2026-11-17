"""Calendar helpers."""
from datetime import datetime


def parse_iso_date(text):
    return datetime.strptime(text, "%Y-%m-%d").date()


def format_date(value):
    return value.strftime("%d %b %Y")
