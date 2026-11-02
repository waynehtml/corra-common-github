from __future__ import annotations
from typing import Any
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pendulum

from oedis.common.template_resolver import resolve_config

try:
    # Available in Databricks notebooks / jobs that support databricks.sdk.runtime
    from databricks.sdk.runtime import dbutils  # type: ignore
except Exception:
    dbutils = None  # running outside Databricks


def delta(*,
    base: date = None,
    format: str = "YYYYMMDD",     # Pendulum tokens only
    days: int = 0,
    months: int = 0,
    years: int = 0,
    bday: bool = False,           # True => business days, False => calendar days
    tz: str = "America/Toronto",
    uppercase: bool = True
) -> str:
    """
    Jinja macro that formats a date after applying calendar offsets.

    Behavior (by design):
    - If base is omitted, "today" is computed in the provided tz.
    - Month/year offsets use pandas DateOffset (calendar-aware).
    - Day offsets use either business days (BDay) or calendar days.
    - Formatting supports Pendulum tokens (default) or strftime when format
      contains "%" tokens.

    Examples:
      {{ delta() }}                          -> defaults to today in tz, format YYYYMMDD
      {{ delta(days=-1) }}                   -> yesterday (calendar days)
      {{ delta(bday=True, days=-1) }}        -> previous business day
      {{ delta(months=1, format="YYYY-MM") }}-> next month, YYYY-MM (Pendulum tokens)
      {{ delta(format="%Y-%m-%d") }}          -> today with strftime tokens
      {{ delta(base=runtime.rundate, years=1) }}
    """
    if base is not None and not isinstance(base, date):
        raise TypeError(f"base must be date or None, got {type(base)}")
    if not isinstance(format, str) or not format.strip():
        raise TypeError("format must be a non-empty string")
    if not isinstance(days, int):
        raise TypeError("days must be an int")
    if not isinstance(months, int):
        raise TypeError("months must be an int")
    if not isinstance(years, int):
        raise TypeError("years must be an int")
    if not isinstance(bday, bool):
        raise TypeError("bday must be a bool")
    if not isinstance(tz, str) or not tz.strip():
        raise TypeError("tz must be a non-empty string")
    if not isinstance(uppercase, bool):
        raise TypeError("uppercase must be a bool")

    # If no base provided, pick "today" in tz for reproducible template runs.
    base_date: date = base if base is not None else datetime.now(ZoneInfo(tz)).date()

    # Shift using pandas for reliable calendar arithmetic.
    ts = pd.Timestamp(base_date)

    if months or years:
        ts = ts + pd.DateOffset(months=months, years=years)

    if days:
        ts = ts + (pd.offsets.BDay(days) if bday else pd.Timedelta(days=days))

    # Format using pendulum tokens by default. If the format string includes
    # strftime tokens ("%"), fall back to standard datetime.strftime so callers
    # can opt out of Pendulum.
    if "%" in format:
        out = ts.to_pydatetime().strftime(format)
    else:
        dt = pendulum.date(ts.year, ts.month, ts.day)
        out = dt.format(format)

    return out.upper() if uppercase else out.lower()



def timestamp(*,
    format: str = "YYYY-MM-DD_HH-mm-ss",
    tz: str = "America/Toronto",
    uppercase: bool = True,
) -> str:
    """
    Jinja macro that formats the current timestamp in the provided timezone.

    Examples:
      {{ timestamp() }}
      {{ timestamp(format="YYYYMMDDHHmmss") }}
      {{ timestamp(tz="UTC") }}
    """
    if not isinstance(format, str) or not format.strip():
        raise TypeError("format must be a non-empty string")
    if not isinstance(tz, str) or not tz.strip():
        raise TypeError("tz must be a non-empty string")
    if not isinstance(uppercase, bool):
        raise TypeError("uppercase must be a bool")

    out = pendulum.now(tz).format(format)
    return out.upper() if uppercase else out.lower()



def epoch(*, unit: str = "seconds") -> str:
    """
    Jinja macro that returns the current Unix epoch time.

    Examples:
      {{ epoch() }}
      {{ epoch(unit="milliseconds") }}
    """
    if not isinstance(unit, str) or not unit.strip():
        raise TypeError("unit must be a non-empty string")

    normalized_unit = unit.strip().lower()
    if normalized_unit not in {"seconds", "milliseconds"}:
        raise ValueError("unit must be either 'seconds' or 'milliseconds'")

    now = datetime.now(ZoneInfo("UTC"))
    if normalized_unit == "milliseconds":
        value = int(now.timestamp() * 1000)
    else:
        value = int(now.timestamp())
    return str(value)



def keyvault(*, scope: str, key: str) -> str:
    """
    Jinja macro:
      {{ keyvault(scope="my-scope", key="my-secret") }}

    Relies on Databricks runtime import of dbutils.
    Raises on any failure. No defaults.
    """
    if not isinstance(scope, str) or not scope.strip():
        raise TypeError("keyvault.scope must be a non-empty string")
    if not isinstance(key, str) or not key.strip():
        raise TypeError("keyvault.key must be a non-empty string")

    if dbutils is None:
        raise RuntimeError(
            "dbutils is not available (not running in Databricks runtime)."
        )

    try:
        return str(dbutils.secrets.get(scope=scope, key=key))
    except Exception as e:
        raise RuntimeError(f"Failed to read secret: scope={scope!r}, key={key!r}") from e


_TEMPLATE_FUNCTIONS = {
    "delta": delta,
    "timestamp": timestamp,
    "epoch": epoch,
    "keyvault": keyvault,
}


if __name__ == "__main__":
    runtime = {"rundate": date(2026, 1, 25)}
    template = {
        "path": "/mnt/{{ delta(base=runtime.rundate, days=-1) }}/x",
        "timestamp": "{{ timestamp() }}",
        "epoch": "{{ epoch() }}",
    }
    print(
        resolve_config(
            template,
            render_context={
                "runtime": runtime,
                **_TEMPLATE_FUNCTIONS,
            },
        )
    )
