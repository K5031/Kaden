from rates import old_rate, rate


def tax(amount, country, year):
    """The tax on `amount` in `country` in `year`."""
    return amount * (old_rate(country) if year < 2020 else rate(country))
