from datetime import date


def parse(text):
    """A date written day/month/year, as "25/12/2024"."""
    first, second, year = (int(part) for part in text.split("/"))
    return date(year, first, second)
