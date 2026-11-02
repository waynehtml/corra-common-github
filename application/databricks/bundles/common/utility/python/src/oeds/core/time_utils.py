from __future__ import annotations
from datetime import date, datetime, timedelta, timezone, time
from typing import Generator, Iterator, Optional
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Default package-level constants and setter/getter
# ---------------------------------------------------------------------------
DEFAULT_TZ = "America/Toronto"
BANK_DATE_FMT = "%Y-%m-%d"  # bank format: yyyy-MM-dd

def get_default_tz() -> str:
    """
    Return the current default timezone string.

    Example:
        >>> get_default_tz()
        'America/Toronto'
    """
    return DEFAULT_TZ

def set_default_tz(tz: str) -> None:
    """
    Set the package-level default timezone.

    Args:
        tz (str): IANA timezone name (e.g., 'America/Toronto', 'UTC').

    Example:
        >>> set_default_tz("UTC")
        >>> get_default_tz()
        'UTC'
    """
    global DEFAULT_TZ
    # Validate timezone name early
    try:
        ZoneInfo(tz)
    except Exception as e:
        raise ValueError(f"Invalid timezone '{tz}': {e}")
    DEFAULT_TZ = tz


# ---------------------------------------------------------------------------
# Core "now" helpers
# ---------------------------------------------------------------------------

def now_utc(time_mode: str = "now", ms: bool = False) -> datetime:
    """
    Return a timezone-aware UTC datetime for the current date and selected time mode.

    Args:
        time_mode (str, optional): 
            Which time to use:
              - "now": current UTC time (default)
              - "min": start of the current UTC day (00:00:00)
              - "max": end of the current UTC day (23:59:59.999999)
        ms (bool, optional):
            Whether to keep microseconds.
            Defaults to False (truncate microseconds to 0).

    Returns:
        datetime: A UTC datetime object representing the chosen time.
    
    Example:
        >>> now_utc()
        datetime.datetime(2025, 10, 17, 14, 45, 32, tzinfo=datetime.timezone.utc)
        >>> now_utc("now", ms=True)
        datetime.datetime(2025, 10, 17, 14, 45, 32, 123456, tzinfo=datetime.timezone.utc)
        >>> now_utc("min")
        datetime.datetime(2025, 10, 17, 0, 0, tzinfo=datetime.timezone.utc)
        >>> now_utc("max")
        datetime.datetime(2025, 10, 17, 23, 59, 59, 999999, tzinfo=datetime.timezone.utc)
    """
    # Get today's date in UTC
    today_utc = datetime.now(timezone.utc).date()

    if time_mode == "now":
        # Current moment in UTC
        dt = datetime.now(timezone.utc)
    elif time_mode == "min":
        # Beginning of day (00:00:00 UTC)
        dt = datetime.combine(today_utc, time.min, tzinfo=timezone.utc)
    elif time_mode == "max":
        # End of day (23:59:59.999999 UTC)
        dt = datetime.combine(today_utc, time.max, tzinfo=timezone.utc)
    else:
        raise ValueError("time_mode must be one of: 'now', 'min', 'max'")

    # Drop microseconds if requested
    if not ms:
        dt = dt.replace(microsecond=0)

    return dt

def now_local(time_mode: str = "now", tz: str = DEFAULT_TZ, ms: bool = False) -> datetime:
    """
    Return a timezone-aware datetime for the current date and selected time mode
    in the specified timezone (default: Toronto).

    Args:
        time_mode (str, optional):
            Which time to use:
              - "now": current local time (default)
              - "min": start of the current local day (00:00:00)
              - "max": end of the current local day (23:59:59.999999)
        tz (str, optional): IANA timezone name. Defaults to "America/Toronto".
        ms (bool, optional):
            Whether to keep microseconds.
            Defaults to False (truncate microseconds to 0).

    Returns:
        datetime: A timezone-aware datetime object for the given timezone.

    Example:
        >>> now_local()
        datetime.datetime(2025, 10, 17, 10, 32, 45, tzinfo=ZoneInfo('America/Toronto'))
        >>> now_local(ms=True)
        datetime.datetime(2025, 10, 17, 10, 32, 45, 123456, tzinfo=ZoneInfo('America/Toronto'))
        >>> now_local(time_mode="min")
        datetime.datetime(2025, 10, 17, 0, 0, tzinfo=ZoneInfo('America/Toronto'))
        >>> now_local(time_mode="max")
        datetime.datetime(2025, 10, 17, 23, 59, 59, 999999, tzinfo=ZoneInfo('America/Toronto'))
    """
    tzinfo = ZoneInfo(tz)
    now_dt = datetime.now(tzinfo)
    today_local = now_dt.date()

    if time_mode == "now":
        dt = now_dt
    elif time_mode == "min":
        dt = datetime.combine(today_local, time.min, tzinfo=tzinfo)
    elif time_mode == "max":
        dt = datetime.combine(today_local, time.max, tzinfo=tzinfo)
    else:
        raise ValueError("time_mode must be one of: 'now', 'min', or 'max'")

    # Drop microseconds if requested
    if not ms:
        dt = dt.replace(microsecond=0)

    return dt

def today_local(tz: str = DEFAULT_TZ) -> date:
    """
    Return today's date in the specified local timezone (default: Toronto).

    Args:
        tz (str, optional): IANA timezone name. Defaults to "America/Toronto".

    Returns:
        date: The current date according to the specified local timezone.

    Example:
        >>> today_local()
        datetime.date(2025, 10, 17)
        >>> today_local("America/Vancouver")
        datetime.date(2025, 10, 17)
    """
    tzinfo = ZoneInfo(tz)
    return datetime.now(tzinfo).date()

def today_utc() -> date:
    """
    Return today's date in UTC.

    Returns:
        date: The current UTC date.

    Example:
        >>> today_utc()
        datetime.date(2025, 10, 17)
    """
    return datetime.now(timezone.utc).date()

# ---------------------------------------------------------------------------
# timezone conversion functions
# ---------------------------------------------------------------------------
def date_to_utc_datetime(local_date: date = None, time_mode: str = "min") -> datetime:
    """
    Convert a local date (in DEFAULT_TZ) to a UTC datetime according to a specified time mode.

    Args:
        local_date (date, optional): The local date in DEFAULT_TZ. Defaults to today's date.
        time_mode (str, optional): One of "min", "max", "noon", or "now".
            - "min": 00:00:00
            - "max": 23:59:59.999999
            - "noon": 12:00:00
            - "now": current local time on that date

    Returns:
        datetime: The UTC datetime corresponding to the given date/time in DEFAULT_TZ.

    Raises:
        ValueError: If time_mode is invalid.
    """
    if local_date is None:
        local_date = today_local()

    tz = ZoneInfo(DEFAULT_TZ)

    # Select time based on mode
    if time_mode == "min":
        local_time = time.min
    elif time_mode == "max":
        local_time = time.max
    elif time_mode == "noon":
        local_time = time(12, 0, 0)
    elif time_mode == "now":
        # Keep the current local time (but same date)
        now_local = datetime.now(tz)
        local_time = now_local.timetz()
    else:
        raise ValueError(f"Invalid time_mode: {time_mode}. Choose from 'min', 'max', 'noon', 'now'.")

    # Build local datetime with timezone
    local_dt = datetime.combine(local_date, local_time, tzinfo=tz)

    # Convert to UTC
    utc_dt = local_dt.astimezone(timezone.utc)

    return utc_dt


def to_utc(dt: datetime) -> datetime:
    """
    Convert a datetime to UTC.

    If the datetime is naive (no tzinfo), the system's local timezone is assumed,
    determined via datetime.now().astimezone().tzinfo.

    Args:
        dt (datetime): The datetime to convert.

    Returns:
        datetime: A timezone-aware datetime normalized to UTC.

    Example:
        >>> from datetime import datetime
        >>> from zoneinfo import ZoneInfo
        >>> dt_local = datetime(2025, 10, 23, 0, 0, tzinfo=ZoneInfo("America/Toronto"))
        >>> to_utc(dt_local)
        datetime.datetime(2025, 10, 23, 4, 0, tzinfo=zoneinfo.ZoneInfo(key='UTC'))
    """
    if not isinstance(dt, datetime):
        raise TypeError("dt must be a datetime object.")

    # Determine system timezone from current environment
    system_tz = datetime.now().astimezone().tzinfo or ZoneInfo("UTC")

    # If datetime is naive, assume system timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=system_tz)

    # Convert to UTC
    return dt.astimezone(ZoneInfo("UTC"))

# ---------------------------------------------------------------------------
# time conversion functions
# ---------------------------------------------------------------------------

def datetime_to_utc_iso_str(dt: datetime = None, *, ms: bool = False) -> str:
    """
    Convert a datetime to an ISO 8601 UTC string.

    Args:
        dt (datetime, optional): Datetime to convert. If None, uses current local time.
        ms (bool, optional): Whether to include microseconds. Defaults to False.

    Returns:
        str: ISO 8601 formatted UTC string (e.g., "2025-10-17T14:45:32Z").

    Behavior:
        - If `dt` is None → uses current time in DEFAULT_TZ.
        - If `dt` is naive (no tzinfo) → assumes it's in DEFAULT_TZ (local time).
        - If `dt` is aware → converts it to UTC.
        - The result is always UTC with 'Z' suffix.
        - Microseconds included only if ms=True.

    Example:
        >>> datetime_to_utc_iso_str()
        '2025-10-17T14:45:32Z'
        >>> datetime_to_utc_iso_str(ms=True)
        '2025-10-17T14:45:32.123456Z'
        >>> from datetime import datetime, timezone, timedelta
        >>> dt = datetime(2025, 10, 17, 10, 30, tzinfo=timezone(timedelta(hours=-4)))
        >>> datetime_to_utc_iso_str(dt)
        '2025-10-17T14:30:00Z'
        >>> datetime_to_utc_iso_str(datetime(2025, 10, 17, 10, 30))
        '2025-10-17T14:30:00Z'  # naive = local Toronto
    """
    # Use current local time if none provided
    if dt is None:
        dt = datetime.now(ZoneInfo(DEFAULT_TZ))

    # Treat naive datetime as local in DEFAULT_TZ
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(DEFAULT_TZ))

    # Convert to UTC
    dt_utc = dt.astimezone(timezone.utc)

    # Optionally truncate microseconds
    if not ms:
        dt_utc = dt_utc.replace(microsecond=0)

    # Return ISO format with 'Z' suffix for UTC
    return dt_utc.isoformat().replace("+00:00", "Z")

def utc_iso_str_to_datetime(iso_str: str) -> datetime:
    """
    Convert an ISO 8601 UTC string (e.g. "2025-10-17T14:45:32Z")
    back into a timezone-aware UTC datetime.

    Args:
        iso_str (str): ISO 8601 UTC string. Supports:
                       - With or without microseconds
                       - Ending with 'Z' or '+00:00'

    Returns:
        datetime: A timezone-aware UTC datetime object.

    Example:
        >>> utc_iso_str_to_datetime("2025-10-17T14:45:32Z")
        datetime.datetime(2025, 10, 17, 14, 45, 32, tzinfo=datetime.timezone.utc)

        >>> utc_iso_str_to_datetime("2025-10-17T14:45:32.123456Z")
        datetime.datetime(2025, 10, 17, 14, 45, 32, 123456, tzinfo=datetime.timezone.utc)
    """
    if not iso_str:
        raise ValueError("iso_str cannot be empty")

    # Normalize 'Z' suffix to '+00:00' for fromisoformat()
    normalized = iso_str.replace("Z", "+00:00")

    # Parse ISO string into datetime
    dt = datetime.fromisoformat(normalized)

    # Ensure it's timezone-aware in UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt


# ---------------------------------------------------------------------------
# Unix timestamp conversions
# ---------------------------------------------------------------------------
def to_unix(dt: datetime) -> float:
    """Convert a datetime to a Unix timestamp (seconds since epoch)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(DEFAULT_TZ))
    return dt.timestamp()


def from_unix(ts: float, *, tz: str = DEFAULT_TZ) -> datetime:
    """Convert a Unix timestamp to a timezone-aware datetime (default Toronto)."""
    base = datetime.fromtimestamp(ts, ZoneInfo(tz))
    return base


# -------------------------------------------------------------------------
# Bank-style date utilities
# -------------------------------------------------------------------------

def date_to_bank_str(d: date) -> str:
    """
    Convert a date or datetime to a bank-style string in 'YYYY-MM-DD' format.

    Args:
        d (date): A date or datetime object.

    Returns:
        str: Date string formatted as 'YYYY-MM-DD'.

    Example:
        >>> date_to_bank_str(date(2025, 10, 18))
        '2025-10-18'
        >>> date_to_bank_str(datetime(2025, 10, 18, 14, 30))
        '2025-10-18'
    """
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime(BANK_DATE_FMT)


def bank_str_to_date(s: str) -> date:
    """
    Convert a 'YYYY-MM-DD' formatted string to a date object.

    Args:
        s (str): String in 'YYYY-MM-DD' format.

    Returns:
        date: Corresponding date object.

    Example:
        >>> bank_str_to_date('2025-10-18')
        datetime.date(2025, 10, 18)
    """
    return datetime.strptime(s, BANK_DATE_FMT).date()
