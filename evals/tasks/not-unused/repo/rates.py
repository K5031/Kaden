def rate(country):
    """The tax rate for `country`."""
    return {"NL": 0.21, "DE": 0.19}.get(country, 0.0)


def old_rate(country):
    """The tax rate for `country`, as it was before 2020."""
    return {"NL": 0.19, "DE": 0.16}.get(country, 0.0)
