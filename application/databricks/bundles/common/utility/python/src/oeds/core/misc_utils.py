"""Miscellaneous helper utilities used across OEDS modules."""

import inspect
import logging
import importlib

logger = logging.getLogger(__name__)


def fn():
    """Return the current function or method name.

    On Python 3.11+ the qualified name is returned; earlier versions approximate the class
    context using ``self`` or ``cls`` when present.

    Returns:
        Function or method name as a string.
    """
    frame = inspect.currentframe().f_back
    try:
        code = frame.f_code
        # Prefer qualified name when available (3.11+)
        name = getattr(code, "co_qualname", code.co_name)

        # If not qualified (pre-3.11), try to add class context
        if name == code.co_name:
            if "self" in frame.f_locals:
                return f"{frame.f_locals['self'].__class__.__name__}.{code.co_name}"
            if "cls" in frame.f_locals and isinstance(frame.f_locals["cls"], type):
                return f"{frame.f_locals['cls'].__name__}.{code.co_name}"
        return name
    finally:
        del frame


def param(name, default=None):
    """Retrieve a Databricks job/notebook parameter.

    This helper reads parameters from two common Databricks sources:
    1. Job notebook bindings from ``dbutils.notebook.entry_point.getCurrentBindings()``.
    2. Interactive widgets from ``dbutils.widgets.get(name)``.

    If neither source is available or the parameter is missing, ``default`` is returned.

    Args:
        name: Parameter name.
        default: Fallback value when parameter cannot be resolved.

    Returns:
        Parameter value with surrounding whitespace removed, or ``default``.

    Raises:
        ImportError: If Databricks runtime ``dbutils`` is unavailable.
    """
    try:
        from databricks.sdk.runtime import dbutils
    except ImportError as exc:
        raise ImportError(
            "Databricks runtime dbutils is unavailable. "
            "Run this utility in a Databricks runtime or install databricks-sdk."
        ) from exc

    try:
        args = dbutils.notebook.entry_point.getCurrentBindings()
        if name in args:
            value = args[name]
            return value.strip() if isinstance(value, str) else value
    except Exception:
        pass

    try:
        value = dbutils.widgets.get(name)
        return value.strip() if isinstance(value, str) else value
    except Exception:
        return default

def is_valid_json(value):
    """Return ``True`` when ``value`` is a valid JSON string, else ``False``."""
    
    if not isinstance(value, str):
        return False
    try:
        import json

        json.loads(value)
        return True
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
