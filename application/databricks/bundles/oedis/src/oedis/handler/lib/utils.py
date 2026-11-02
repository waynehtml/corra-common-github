import fnmatch
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Optional, Sequence

def normalize_values(values: Optional[Any]) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        return [values]
    if isinstance(values, Sequence):
        return [str(value) for value in values if value]
    return [str(values)]

def matches_any(value: str, patterns: Sequence[str]) -> bool:
    if "/" in value:
        rel_posix = PurePosixPath(value)
        return any(rel_posix.match(pattern) for pattern in patterns)
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)

def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def parse_datetime(value: Optional[Any], label: str) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return normalize_datetime(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{label} must be ISO-8601 datetime string") from exc
        return normalize_datetime(parsed)
    raise ValueError(f"{label} must be a datetime or ISO-8601 string")

def apply_time_filter(mtime: float, updated_after: Optional[datetime], updated_before: Optional[datetime]) -> bool:
    if updated_after and mtime < updated_after.timestamp():
        return False
    if updated_before and mtime >= updated_before.timestamp():
        return False
    return True

def truncate_text(value: str, limit: int = 512) -> str:
    return value[:limit]

_INBOX_EXTERNAL_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|//|[A-Za-z][A-Za-z0-9+.-]*://)")

def resolve_inbox_path(
    inbox: Path,
    path_value: Any,
    *,
    field_name: str = "path",
    default: Path | None = None,
) -> Path:
    inbox_resolved = inbox.resolve()

    if path_value in (None, ""):
        candidate = default if default is not None else inbox_resolved
        resolved = Path(candidate).resolve()
        if resolved != inbox_resolved and inbox_resolved not in resolved.parents:
            raise ValueError(f"{field_name} escapes ctx.inbox: {candidate}")
        return resolved

    raw_value = str(path_value).strip()
    normalized = raw_value.replace("\\", "/")
    if _INBOX_EXTERNAL_PATH_RE.match(normalized):
        raise ValueError(f"{field_name} must be relative to ctx.inbox: {raw_value}")
    relative_candidate = Path(normalized.lstrip("/"))

    if relative_candidate.is_absolute():
        raise ValueError(f"{field_name} must be relative to ctx.inbox: {raw_value}")

    clean_parts = [part for part in relative_candidate.parts if part not in ("", ".")]
    if not clean_parts:
        raise ValueError(f"{field_name} path is invalid: {raw_value}")
    if ".." in clean_parts:
        raise ValueError(f"{field_name} must not contain '..': {raw_value}")

    resolved = (inbox_resolved / Path(*clean_parts)).resolve()
    if resolved != inbox_resolved and inbox_resolved not in resolved.parents:
        raise ValueError(f"{field_name} escapes ctx.inbox: {raw_value}")
    return resolved

def resolve_inbox_relative_path(
    inbox: Path,
    path_value: Any,
    *,
    field_name: str = "path",
) -> str:
    resolved = resolve_inbox_path(inbox, path_value, field_name=field_name)
    return resolved.relative_to(inbox.resolve()).as_posix()

def _normalize_globs(value: Optional[str | Sequence[str]], default: Sequence[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    raise TypeError("Glob patterns must be a string or sequence of strings.")

def _is_excluded(path: Path, excludes: Iterable[str], *, root: Path, recursive: bool) -> bool:
    if any(fnmatch.fnmatch(path.name, pattern) for pattern in excludes):
        return True

    if not recursive:
        return False

    rel_path = path.relative_to(root).as_posix()
    rel_posix = PurePosixPath(rel_path)
    return any(rel_posix.match(pattern) for pattern in excludes)

def find_files(
    path: Path,
    include: str | Sequence[str] = "*",
    exclude: Optional[str | Sequence[str]] = None,
    recursive: bool = False,
) -> list[Path]:
    """
    Strict mode: raises if `path` doesn't exist.

    - include: glob(s) passed to pathlib glob/rglob. Prefer `recursive=True` with simple patterns like `"*.gpg"` when basename matching is sufficient.
    - exclude: glob(s) matched against filename; when recursive=true,
      patterns are also matched against the relative path from `path`
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Search root does not exist: {path}")

    include_patterns = _normalize_globs(include, ["*"])
    exclude_patterns = _normalize_globs(exclude, [])

    out_map: dict[str, Path] = {}
    iterator = path.rglob if recursive else path.glob

    for pattern in include_patterns:
        for p in iterator(pattern):
            if p.is_dir():
                continue
            if exclude_patterns and _is_excluded(p, exclude_patterns, root=path, recursive=recursive):
                continue
            out_map[str(p)] = p

    return sorted(out_map.values(), key=lambda p: str(p))

_DBUTILS_URI_RE = re.compile(
    r"^/Volumes/|^(?:dbfs|file):/+|^(?:abfs|abfss|s3|gs|wasbs)://",
    re.IGNORECASE,
)
def to_dbutils_path(path: str) -> str:
    value = str(path or "").strip()
    if not value:
        return value
    if _DBUTILS_URI_RE.match(value):
        return value
    if value.startswith("/"):
        return f"file:{value}"
    return value
