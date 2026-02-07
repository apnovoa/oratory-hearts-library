"""Liturgical calendar utilities.

Computes the current liturgical season of the Roman Rite based on
approximate date ranges.  Easter is calculated using the anonymous
Gregorian algorithm (Meeus / Oudin).
"""

from datetime import date, timedelta


def get_easter_date(year):
    """Return the date of Easter Sunday for the given year.

    Uses the anonymous Gregorian algorithm (also known as the
    Meeus/Jones/Butcher algorithm).
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _advent_start(year):
    """Return the date of the first Sunday of Advent for the given year.

    Advent begins on the Sunday nearest to the feast of St. Andrew
    (November 30), which is the Sunday falling on or after November 27.
    Equivalently, it is the fourth Sunday before Christmas Day.
    """
    christmas = date(year, 12, 25)
    # Weekday of Christmas: 0=Monday ... 6=Sunday
    # We want the fourth Sunday before Christmas
    christmas_weekday = christmas.isoweekday()  # 1=Mon ... 7=Sun
    # Days from Sunday to Christmas day-of-week
    days_after_sunday = christmas_weekday % 7  # Sun=0, Mon=1 ... Sat=6
    # The Sunday before (or on) Christmas:
    fourth_sunday_before = christmas - timedelta(days=days_after_sunday + 21)
    return fourth_sunday_before


def get_current_season(today=None):
    """Return the current liturgical season as a string slug.

    Possible values:
        "advent"         -- ~4 Sundays before Dec 25 until Dec 24
        "christmas"      -- Dec 25 through the Sunday after Epiphany (Jan 6),
                            approximated as Jan 13
        "ordinary_early" -- after Christmas season until Ash Wednesday - 1
        "lent"           -- Ash Wednesday (46 days before Easter) through
                            Holy Saturday (Easter - 1)
        "easter"         -- Easter Sunday through Pentecost (49 days later)
        "ordinary_late"  -- after Pentecost until the start of Advent
    """
    if today is None:
        today = date.today()

    year = today.year
    easter = get_easter_date(year)

    # Key dates
    ash_wednesday = easter - timedelta(days=46)
    pentecost = easter + timedelta(days=49)
    advent_start = _advent_start(year)

    # Christmas season: Dec 25 of previous year through ~Jan 13
    # If today is Jan 1-13, check if we're still in Christmas season
    christmas_end = date(year, 1, 13)
    christmas_start_prev = date(year - 1, 12, 25)

    if today >= date(year, 12, 25):
        return "christmas"

    if today >= advent_start:
        return "advent"

    if today >= pentecost:
        return "ordinary_late"

    if today >= easter:
        return "easter"

    if today >= ash_wednesday:
        return "lent"

    if today <= christmas_end:
        return "christmas"

    return "ordinary_early"


def get_season_display_name(season):
    """Return a human-readable display name for a liturgical season."""
    names = {
        "advent": "Advent",
        "christmas": "Christmastide",
        "ordinary_early": "Ordinary Time",
        "lent": "Lent",
        "easter": "Eastertide",
        "ordinary_late": "Ordinary Time",
    }
    return names.get(season, "Ordinary Time")


def get_season_description(season):
    """Return a brief description or traditional quote for the season."""
    descriptions = {
        "advent": (
            "A season of joyful expectation and preparation for the coming "
            "of Christ. \"Rorate caeli desuper, et nubes pluant iustum.\""
        ),
        "christmas": (
            "The Church celebrates the Nativity of Our Lord and the "
            "manifestation of God made flesh. \"Verbum caro factum est, "
            "et habitavit in nobis.\""
        ),
        "ordinary_early": (
            "A time of growth in the faith, meditating on the public life "
            "and teachings of Our Lord."
        ),
        "lent": (
            "A penitential season of prayer, fasting, and almsgiving in "
            "preparation for the Paschal mystery. \"Memento, homo, quia "
            "pulvis es, et in pulverem reverteris.\""
        ),
        "easter": (
            "The Church rejoices in the Resurrection of Christ and the "
            "promise of eternal life. \"Haec dies quam fecit Dominus; "
            "exsultemus et laetemur in ea.\""
        ),
        "ordinary_late": (
            "A time of growth in the faith, meditating on the Kingdom of "
            "God and the call to holiness."
        ),
    }
    return descriptions.get(season, "")
