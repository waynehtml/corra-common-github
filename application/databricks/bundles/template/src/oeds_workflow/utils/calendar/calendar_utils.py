from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import IntEnum
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np
from dateutil.relativedelta import relativedelta


class Weekday(IntEnum):
    """
    Weekday values using Python's convention:

    Monday = 0
    Tuesday = 1
    Wednesday = 2
    Thursday = 3
    Friday = 4
    Saturday = 5
    Sunday = 6
    """
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


@dataclass(frozen=True)
class CalendarUtils:
    """
    Utility class for date logic.

    Function groups
    ---------------
    1. day_*:
       Normal calendar day operations.

    2. weekday_*:
       Weekday operations using Monday-Friday only.
       Holidays are ignored.

    3. business_day_*:
       Business-day operations using Monday-Friday excluding configured holidays.

    4. is_*:
       Boolean checks such as is_weekday, is_holiday, is_monday, etc.

    5. month_*:
       Month-based helpers such as first day of month, nth weekday of month,
       or last Friday of month.

    Notes
    -----
    - All functions use `datetime.date`.
    - "weekday" means Monday-Friday only.
    - "business day" means weekday and not holiday.
    """

    holidays: tuple[date, ...] = ()
    tz_name: str = "America/Toronto"

    _holiday_set: frozenset[date] = field(init=False, repr=False)
    _buscal: np.busdaycalendar = field(init=False, repr=False)

    def __post_init__(self) -> None:
        holiday_values = tuple(sorted(set(self.holidays)))
        holiday_set = frozenset(holiday_values)

        holiday_arr = np.array(
            [d.isoformat() for d in holiday_values],
            dtype="datetime64[D]"
        )

        object.__setattr__(self, "_holiday_set", holiday_set)
        object.__setattr__(
            self,
            "_buscal",
            np.busdaycalendar(
                weekmask="1111100",
                holidays=holiday_arr
            )
        )

    @classmethod
    def from_holidays(
        cls,
        holidays: Iterable[date],
        tz_name: str = "America/Toronto"
    ) -> "CalendarUtils":
        """
        Create a CalendarUtils instance from any iterable of holiday dates.
        """
        return cls(holidays=tuple(holidays), tz_name=tz_name)

    def today(self) -> date:
        """
        Return today's date using the configured timezone.
        """
        return datetime.now(ZoneInfo(self.tz_name)).date()

    @staticmethod
    def _to_date(value: np.datetime64) -> date:
        return date.fromisoformat(str(value))

    @staticmethod
    def _validate_weekday(weekday: int | Weekday) -> int:
        weekday_int = int(weekday)
        if weekday_int < 0 or weekday_int > 6:
            raise ValueError("weekday must be between 0 (Monday) and 6 (Sunday)")
        return weekday_int

    @staticmethod
    def _validate_n(n: int) -> None:
        if n < 1:
            raise ValueError("n must be >= 1")

    # ============================================================
    # 1. CALENDAR DAY FUNCTIONS
    # ============================================================

    @staticmethod
    def day_add(d: date, n: int) -> date:
        """
        Add or subtract normal calendar days.

        Examples
        --------
        >>> CalendarUtils.day_add(date(2026, 4, 10), 3)
        datetime.date(2026, 4, 13)
        """
        return d + timedelta(days=n)

    @staticmethod
    def day_prev(d: date) -> date:
        """
        Return the previous calendar day.
        """
        return d - timedelta(days=1)

    @staticmethod
    def day_next(d: date) -> date:
        """
        Return the next calendar day.
        """
        return d + timedelta(days=1)

    # ============================================================
    # 2. WEEKDAY FUNCTIONS
    # ============================================================

    @staticmethod
    def weekday_add(d: date, n: int) -> date:
        """
        Add or subtract weekdays.

        Weekdays mean Monday-Friday only.
        Holidays are ignored.

        Examples
        --------
        >>> CalendarUtils.weekday_add(date(2026, 4, 10), 1)  # Friday + 1 weekday
        datetime.date(2026, 4, 13)
        """
        if n == 0:
            return d

        step = 1 if n > 0 else -1
        remaining = abs(n)
        x = d

        while remaining > 0:
            x += timedelta(days=step)
            if x.weekday() < 5:
                remaining -= 1

        return x

    @staticmethod
    def weekday_prev(d: date) -> date:
        """
        Return the previous weekday before d.

        Strictly previous. If d is Monday, returns previous Friday.
        """
        x = d - timedelta(days=1)
        while x.weekday() >= 5:
            x -= timedelta(days=1)
        return x

    @staticmethod
    def weekday_next(d: date) -> date:
        """
        Return the next weekday after d.

        Strictly next. If d is Friday, returns next Monday.
        """
        x = d + timedelta(days=1)
        while x.weekday() >= 5:
            x += timedelta(days=1)
        return x

    @staticmethod
    def weekday_this(d: date, weekday: int | Weekday) -> date:
        """
        Return the given weekday in the same Monday-based week as d.

        Example:
        If d is Sunday 2026-04-12, then weekday_this(d, TUESDAY)
        returns 2026-04-07.
        """
        weekday_int = CalendarUtils._validate_weekday(weekday)
        week_start = d - timedelta(days=d.weekday())
        return week_start + timedelta(days=weekday_int)

    @staticmethod
    def weekday_prev_occurrence(d: date, weekday: int | Weekday) -> date:
        """
        Return the strictly previous occurrence of the given weekday.

        If d is already that weekday, go back 7 days.
        """
        weekday_int = CalendarUtils._validate_weekday(weekday)
        days_back = (d.weekday() - weekday_int) % 7
        if days_back == 0:
            days_back = 7
        return d - timedelta(days=days_back)

    @staticmethod
    def weekday_next_occurrence(d: date, weekday: int | Weekday) -> date:
        """
        Return the strictly next occurrence of the given weekday.

        If d is already that weekday, go forward 7 days.
        """
        weekday_int = CalendarUtils._validate_weekday(weekday)
        days_forward = (weekday_int - d.weekday()) % 7
        if days_forward == 0:
            days_forward = 7
        return d + timedelta(days=days_forward)

    @staticmethod
    def week_monday(d: date) -> date:
        """
        Return the Monday of the same week as d.
        """
        return d - timedelta(days=d.weekday())

    # ============================================================
    # 3. BUSINESS DAY FUNCTIONS
    # ============================================================

    def business_day_prev(self, d: date) -> date:
        """
        Return the previous business day before d.
        """
        x = np.busday_offset(
            np.datetime64(d),
            offsets=-1,
            roll="forward",
            busdaycal=self._buscal
        )
        return self._to_date(x)

    def business_day_next(self, d: date) -> date:
        """
        Return the next business day after d.
        """
        x = np.busday_offset(
            np.datetime64(d),
            offsets=1,
            roll="backward",
            busdaycal=self._buscal
        )
        return self._to_date(x)

    def business_day_add(self, d: date, n: int) -> date:
        """
        Add or subtract business days.

        If n = 0:
        - return d if d is already a business day
        - otherwise return the next business day
        """
        if n == 0:
            if self.business_day_is(d):
                return d
            x = np.busday_offset(
                np.datetime64(d),
                offsets=0,
                roll="forward",
                busdaycal=self._buscal
            )
            return self._to_date(x)

        roll = "forward" if n > 0 else "backward"
        x = np.busday_offset(
            np.datetime64(d),
            offsets=n,
            roll=roll,
            busdaycal=self._buscal
        )
        return self._to_date(x)

    # ============================================================
    # 4. CHECK FUNCTIONS
    # ============================================================
    
    def is_business_day(self, d: date) -> bool:
        """
        Return True if d is a business day.

        Business day = Monday-Friday excluding configured holidays.
        """
        return bool(np.is_busday(np.datetime64(d), busdaycal=self._buscal))

    def is_holiday(self, d: date) -> bool:
        """
        Return True if d is in the configured holiday list.
        """
        return d in self._holiday_set

    @staticmethod
    def is_weekday(d: date) -> bool:
        """
        Return True if d is Monday-Friday.
        """
        return d.weekday() < 5

    @staticmethod
    def is_weekend(d: date) -> bool:
        """
        Return True if d is Saturday or Sunday.
        """
        return d.weekday() >= 5

    @staticmethod
    def is_day_of_week(d: date, weekday: int | Weekday) -> bool:
        """
        Return True if d matches the given weekday.
        """
        return d.weekday() == CalendarUtils._validate_weekday(weekday)

    @staticmethod
    def is_monday(d: date) -> bool:
        return d.weekday() == Weekday.MONDAY

    @staticmethod
    def is_tuesday(d: date) -> bool:
        return d.weekday() == Weekday.TUESDAY

    @staticmethod
    def is_wednesday(d: date) -> bool:
        return d.weekday() == Weekday.WEDNESDAY

    @staticmethod
    def is_thursday(d: date) -> bool:
        return d.weekday() == Weekday.THURSDAY

    @staticmethod
    def is_friday(d: date) -> bool:
        return d.weekday() == Weekday.FRIDAY

    @staticmethod
    def is_saturday(d: date) -> bool:
        return d.weekday() == Weekday.SATURDAY

    @staticmethod
    def is_sunday(d: date) -> bool:
        return d.weekday() == Weekday.SUNDAY

    # ============================================================
    # 5. MONTH FUNCTIONS
    # ============================================================

    @staticmethod
    def month_first_day(d: date) -> date:
        """
        Return the first day of the month containing d.
        """
        return d.replace(day=1)

    @staticmethod
    def month_last_day(d: date) -> date:
        """
        Return the last day of the month containing d.
        """
        return d + relativedelta(day=31)

    @staticmethod
    def month_nth_weekday(d: date, weekday: int | Weekday, n: int) -> date:
        """
        Return the nth occurrence of a weekday in the month containing d.

        Examples:
        - first Monday
        - second Tuesday
        - third Friday
        """
        CalendarUtils._validate_n(n)
        weekday_int = CalendarUtils._validate_weekday(weekday)

        first = d.replace(day=1)
        days_until = (weekday_int - first.weekday()) % 7
        result = first + timedelta(days=days_until + (n - 1) * 7)

        if result.month != d.month:
            raise ValueError(f"The {n} occurrence of weekday {weekday_int} does not exist in this month")

        return result

    @staticmethod
    def month_first_weekday(d: date, weekday: int | Weekday) -> date:
        """
        Return the first occurrence of the given weekday in the month.
        """
        return CalendarUtils.month_nth_weekday(d, weekday, 1)

    @staticmethod
    def month_second_weekday(d: date, weekday: int | Weekday) -> date:
        """
        Return the second occurrence of the given weekday in the month.
        """
        return CalendarUtils.month_nth_weekday(d, weekday, 2)

    @staticmethod
    def month_third_weekday(d: date, weekday: int | Weekday) -> date:
        """
        Return the third occurrence of the given weekday in the month.
        """
        return CalendarUtils.month_nth_weekday(d, weekday, 3)

    @staticmethod
    def month_fourth_weekday(d: date, weekday: int | Weekday) -> date:
        """
        Return the fourth occurrence of the given weekday in the month.
        """
        return CalendarUtils.month_nth_weekday(d, weekday, 4)

    @staticmethod
    def month_last_weekday(d: date, weekday: int | Weekday) -> date:
        """
        Return the last occurrence of the given weekday in the month.
        """
        weekday_int = CalendarUtils._validate_weekday(weekday)

        last = d + relativedelta(day=31)
        days_back = (last.weekday() - weekday_int) % 7
        return last - timedelta(days=days_back)