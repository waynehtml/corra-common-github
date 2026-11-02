from __future__ import annotations

from pathlib import Path
from datetime import date, datetime
from typing import Any
from uuid import UUID
import json
from dataclasses import asdict, is_dataclass


def concat_exception_messages(e: BaseException, sep: str = " | ") -> str:
    """Flatten chained exception messages from cause/context for client-facing errors."""
    msgs: list[str] = []
    cur: BaseException | None = e
    seen: set[int] = set()

    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        msg = str(cur).strip()
        msgs.append(msg if msg else type(cur).__name__)

        # Prefer explicit cause; if none, use context.
        cur = cur.__cause__ or cur.__context__

    return sep.join(msgs)

def ls_to_str(root: str)->str:
    base = Path(root)
    if not base.exists():
        return f"{root} does not exist"

    lines = []
    for path in sorted(base.rglob("*")):
        stat = path.stat()
        rel = path.relative_to(base).as_posix()
        kind = "DIR " if path.is_dir() else "FILE"
        size = stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(
            f"[{kind}] {rel:<40} size={size:<10} modified={mtime}"
        )

    result = "\n".join(lines)
    return result


def json_default(value: Any) -> Any:
    """Convert common Python objects into JSON-serializable values."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_dumps(obj: Any, **kwargs: Any) -> str:
    """
    Serialize an object to JSON using the project's default converter.

    Caller-supplied kwargs override the defaults below.
    """
    options = {
        "default": json_default,
        "ensure_ascii": False,
    }
    options.update(kwargs)
    return json.dumps(obj, **options)