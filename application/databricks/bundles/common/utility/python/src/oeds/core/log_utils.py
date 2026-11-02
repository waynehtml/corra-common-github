"""Databricks-friendly logging helpers."""

# logutils.py  — Databricks-friendly minimal setup
import logging
from typing import Optional


def setup_logger(
    *,
    level: str = "INFO",
    fmt: Optional[str] = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt: Optional[str] = "%Y-%m-%d %H:%M:%S",
) -> None:
    """Configure root logging without removing existing handlers.

    Args:
        level: Logging level name to set on the root logger. Defaults to ``"INFO"``.
        fmt: Log format string. When provided with ``datefmt``, the formatter is applied to
            existing handlers.
        datefmt: Date format string. When provided with ``fmt``, the formatter is applied to
            existing handlers.

    Returns:
        None. Logging handlers are configured as a side effect.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = None
    if fmt and datefmt:
        formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

    # If no handlers (rare on Databricks), add one console handler
    if not root.handlers:
        h = logging.StreamHandler()  # defaults to sys.stderr
        if formatter:
            h.setFormatter(formatter)
        root.addHandler(h)

    # If a formatter was requested, apply it to ALL existing handlers
    if formatter:
        for h in root.handlers:
            try:
                h.setFormatter(formatter)
            except Exception:
                # Some special handlers may reject custom formatters; ignore safely
                pass

    # Optional: quiet Py4J chatter when root is INFO
    logging.getLogger("py4j").setLevel(logging.WARNING)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a logger by name.

    Args:
        name: Logger name to retrieve.

    Returns:
        Logger instance for the provided name.

    Raises:
        ValueError: If ``name`` is not provided.
    """
    if not name:
        raise ValueError("Logger name is required. Example: get_logger('corra.pipeline')")
    return logging.getLogger(name)
