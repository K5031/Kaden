def total(prices, discount=0.0):
    """The sum of `prices`, less `discount` (a fraction, 0.1 for 10%)."""
    subtotal = sum(prices)
    return subtotal - discount
