def discount(total, member):
    """Members get 10% off orders over 100; everyone else pays full price."""
    if member is not None and total > 100:
        return int(total * 0.9)
    return total
