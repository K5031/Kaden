from dates import parse


def busiest_month(entries):
    """The month (1-12) with the most entries; each entry is a day/month/year date."""
    counts = {}
    for text in entries:
        month = parse(text).month
        counts[month] = counts.get(month, 0) + 1
    return max(counts, key=counts.get)
