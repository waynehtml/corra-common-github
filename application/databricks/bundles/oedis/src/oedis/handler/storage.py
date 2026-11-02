from __future__ import annotations

import fnmatch
import logging
import time
from pathlib import Path, PurePosixPath
import re
from typing import Any, Dict, Iterable, List

from databricks.sdk.runtime import dbutils
from oedis.handler.lib.utils import to_dbutils_path
from oedis.task_result import TaskResult


logger = logging.getLogger(__name__)

_FULL_STORAGE_PATH_RE = re.compile(
    r"^/|^(?:dbfs|file):/+|^(?:abfs|abfss|s3|gs|wasbs)://",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[a-zA-Z]:[\\/]")


def _ensure_list(value: Any, *, default: Iterable[str]) -> List[str]:
    """Normalize a value to a list of strings with a default fallback."""
    if value in (None, ""):
        result = list(default)
        logger.debug("_ensure_list using default=%s for value=%r", result, value)
        return result
    if isinstance(value, list):
        result = [str(item) for item in value]
        logger.debug("_ensure_list normalized list value=%r -> %s", value, result)
        return result
    result = [str(value)]
    logger.debug("_ensure_list wrapped scalar value=%r -> %s", value, result)
    return result


def _normalize_relative_request_dir(path_value: Any, *, field_name: str) -> str:
    """Normalize an optional request directory and reject absolute or parent paths."""
    path_str = str(path_value or "").strip()
    if not path_str:
        logger.debug(
            "_normalize_relative_request_dir using base root because input was empty: field_name=%s",
            field_name,
        )
        return ""

    if _FULL_STORAGE_PATH_RE.match(path_str) or _WINDOWS_ABSOLUTE_PATH_RE.match(path_str):
        raise ValueError(f"{field_name} must be a relative path, not an absolute path or URI: {path_value}")

    normalized = path_str.replace("\\", "/").strip("/")
    if normalized in ("", "."):
        logger.debug(
            "_normalize_relative_request_dir normalized current directory to base root: field_name=%s input=%r",
            field_name,
            path_value,
        )
        return ""

    relative_path = PurePosixPath(normalized)
    if ".." in relative_path.parts:
        raise ValueError(f"{field_name} must not contain '..': {path_value}")

    resolved = relative_path.as_posix()
    logger.debug(
        "_normalize_relative_request_dir normalized path: field_name=%s input=%r resolved=%s",
        field_name,
        path_value,
        resolved,
    )
    return resolved


def _resolve_inbox_relative_storage_path(inbox: Path, path_value: Any, *, field_name: str) -> str:
    """Resolve a validated relative path under the inbox and convert it to a dbutils-compatible URI."""
    path_str = _normalize_relative_request_dir(path_value, field_name=field_name)
    if not path_str:
        resolved = to_dbutils_path(inbox.as_posix())
        logger.debug(
            "_resolve_inbox_relative_storage_path using inbox path because input was empty: inbox=%s resolved=%s",
            inbox,
            resolved,
        )
        return resolved
    resolved = to_dbutils_path((inbox / path_str).as_posix())
    logger.debug(
        "_resolve_inbox_relative_storage_path using inbox-relative path: inbox=%s input=%r resolved=%s",
        inbox,
        path_value,
        resolved,
    )
    return resolved


def _resolve_full_storage_path(path_value: Any, *, field_name: str) -> str:
    """Validate and pass through a full storage path or URI."""
    path_str = str(path_value or "").strip()
    if not path_str:
        raise ValueError(f"{field_name} is required")
    if not _FULL_STORAGE_PATH_RE.match(path_str):
        raise ValueError(
            f"{field_name} must be a full storage path or URI (for example /Volumes/... or abfss://...)."
        )

    resolved = to_dbutils_path(path_str)
    logger.debug(
        "_resolve_full_storage_path using pass-through path: field_name=%s input=%r resolved=%s",
        field_name,
        path_value,
        resolved,
    )
    return resolved


def _resolve_remote_storage_path(base_uri: Any, request_dir: Any, *, base_field_name: str, request_field_name: str) -> str:
    """Resolve an optional relative request directory under a required storage base URI."""
    base_root = _resolve_full_storage_path(base_uri, field_name=base_field_name)
    relative_dir = _normalize_relative_request_dir(request_dir, field_name=request_field_name)
    if not relative_dir:
        logger.debug(
            "_resolve_remote_storage_path using base root directly: base_field_name=%s base_root=%s",
            base_field_name,
            base_root,
        )
        return base_root

    resolved = to_dbutils_path(f"{base_root.rstrip('/')}/{relative_dir}")
    logger.debug(
        "_resolve_remote_storage_path joined base root and relative dir: base_root=%s request_field_name=%s relative_dir=%s resolved=%s",
        base_root,
        request_field_name,
        relative_dir,
        resolved,
    )
    return resolved


def _matches_any(values: Iterable[str], patterns: Iterable[str]) -> bool:
    """Check whether any candidate value matches one of the glob patterns."""
    values_list = list(values)
    patterns_list = list(patterns)
    matched = any(
        fnmatch.fnmatch(value, pattern)
        for value in values_list
        for pattern in patterns_list
    )
    logger.debug(
        "_matches_any values=%s patterns=%s matched=%s",
        values_list,
        patterns_list,
        matched,
    )
    return matched


def _list_files(
    root: str,
    recursive: bool,
    include: Iterable[str],
    exclude: Iterable[str],
) -> List[tuple[str, str, int]]:
    """List matched file paths under a root path according to recursive mode."""
    include_patterns = list(include)
    exclude_patterns = list(exclude)
    files: List[tuple[str, str, int]] = []

    logger.debug(
        "_list_files start: root=%s recursive=%s include=%s exclude=%s",
        root,
        recursive,
        include_patterns,
        exclude_patterns,
    )

    def file_match_values(rel_path: str, name: str) -> List[str]:
        return [name, rel_path] if recursive else [name]

    def dir_match_values(rel_path: str, name: str) -> List[str]:
        return [name, rel_path]

    def is_excluded(rel_path: str, name: str, *, is_dir: bool) -> bool:
        values = dir_match_values(rel_path, name) if is_dir else file_match_values(rel_path, name)
        excluded = _matches_any(values, exclude_patterns)
        if excluded:
            logger.debug(
                "_list_files excluded %s: rel_path=%s name=%s values=%s",
                "directory" if is_dir else "file",
                rel_path,
                name,
                values,
            )
        return excluded

    def walk(path: str, rel_prefix: str = "", include_by_parent: bool = False) -> None:
        logger.debug(
            "_list_files walking path=%s rel_prefix=%s include_by_parent=%s",
            path,
            rel_prefix,
            include_by_parent,
        )
        for item in dbutils.fs.ls(path):
            item_path = str(getattr(item, "path"))
            is_dir = bool(getattr(item, "isDir", lambda: False)())
            name = PurePosixPath(item_path.rstrip("/")).name
            rel_path = f"{rel_prefix}{name}" if rel_prefix else name

            logger.debug(
                "_list_files found item: item_path=%s is_dir=%s name=%s rel_path=%s",
                item_path,
                is_dir,
                name,
                rel_path,
            )

            if is_dir:
                if not recursive:
                    logger.debug(
                        "_list_files skipping directory because recursive is disabled: %s",
                        item_path,
                    )
                    continue
                if is_excluded(rel_path, name, is_dir=True):
                    continue
                include_dir = include_by_parent or _matches_any(
                    dir_match_values(rel_path, name),
                    include_patterns,
                )
                logger.debug(
                    "_list_files descending into directory: item_path=%s include_dir=%s",
                    item_path,
                    include_dir,
                )
                walk(item_path, f"{rel_path}/", include_dir)
                continue

            if is_excluded(rel_path, name, is_dir=False):
                continue

            if recursive:
                if not (include_by_parent or _matches_any(file_match_values(rel_path, name), include_patterns)):
                    logger.debug(
                        "_list_files skipping file due to include mismatch: item_path=%s rel_path=%s",
                        item_path,
                        rel_path,
                    )
                    continue
            elif not _matches_any(file_match_values(rel_path, name), include_patterns):
                logger.debug(
                    "_list_files skipping file due to include mismatch: item_path=%s rel_path=%s",
                    item_path,
                    rel_path,
                )
                continue

            size = int(getattr(item, "size", 0) or 0)
            files.append((item_path, rel_path, size))
            logger.debug(
                "_list_files matched file: source_path=%s rel_path=%s size=%s",
                item_path,
                rel_path,
                size,
            )

    walk(root)

    logger.debug(
        "_list_files complete: root=%s matched_count=%d matched_files=%s",
        root,
        len(files),
        files,
    )
    return files


def _run_transfer(ctx, action: str) -> TaskResult:
    """Transfer files between storage and inbox using dbutils.fs.cp."""
    task_param = getattr(ctx, "task_params", None) or {}
    specs = task_param.get("requests") or []
    connection = task_param.get("connection") or {}

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")
    if not isinstance(connection, dict):
        raise ValueError("connection must be an object.")
    inbox = Path(ctx.inbox)
    connection_uri = connection.get("uri")

    logger.info(
        "Starting storage %s: task_id=%s handler=%s inbox=%s spec_count=%d",
        getattr(ctx, "task_id", None),
        getattr(ctx, "task_handler", None) or f"oedis.handler.storage.{action}",
        inbox,
        inbox,
        len(specs),
    )
    logger.debug("Storage %s raw task_params=%s", action, task_param)

    handler_start = time.perf_counter()
    outputs: List[Dict[str, Any]] = []

    for index, spec in enumerate(specs):
        logger.info("Processing storage %s spec index=%d", action, index)
        logger.debug("Storage %s spec[%d]=%s", action, index, spec)
        if not isinstance(spec, dict):
            raise ValueError(f"requests[{index}] must be an object.")

        src_dir = spec.get("src_dir")
        dst_dir = spec.get("dst_dir")

        if action == "download":
            src_root = _resolve_remote_storage_path(
                connection_uri,
                src_dir,
                base_field_name="connection.uri",
                request_field_name=f"requests[{index}].src_dir",
            )
            dst_root = _resolve_inbox_relative_storage_path(
                inbox,
                dst_dir,
                field_name=f"requests[{index}].dst_dir",
            )
        else:
            src_root = _resolve_inbox_relative_storage_path(
                inbox,
                src_dir,
                field_name=f"requests[{index}].src_dir",
            )
            dst_root = _resolve_remote_storage_path(
                connection_uri,
                dst_dir,
                base_field_name="connection.uri",
                request_field_name=f"requests[{index}].dst_dir",
            )

        include = _ensure_list(spec.get("include"), default=["*"])
        exclude = _ensure_list(spec.get("exclude"), default=[])
        recursive = bool(spec.get("recursive", False))

        logger.debug(
            "Resolved storage %s spec[%d]: src_root=%s dst_root=%s include=%s exclude=%s recursive=%s",
            action,
            index,
            src_root,
            dst_root,
            include,
            exclude,
            recursive,
        )

        matched_entries = _list_files(
            src_root,
            recursive=recursive,
            include=include,
            exclude=exclude,
        )

        logger.info(
            "Storage %s spec[%d] matched %d files from src_root=%s",
            action,
            index,
            len(matched_entries),
            src_root,
        )
        logger.debug(
            "Storage %s spec[%d] matched_entries=%s",
            action,
            index,
            matched_entries,
        )

        for source_path, rel_path, size in matched_entries:
            op_start = time.perf_counter()
            destination_path = f"{dst_root.rstrip('/')}/{rel_path}"

            logger.debug(
                "Copying storage %s file: source_path=%s destination_path=%s rel_path=%s size=%s",
                action,
                source_path,
                destination_path,
                rel_path,
                size,
            )

            ok = dbutils.fs.cp(source_path, destination_path)
            if ok is False:
                logger.error(
                    "Storage %s copy failed: source_path=%s destination_path=%s",
                    action,
                    source_path,
                    destination_path,
                )
                raise RuntimeError(
                    f"requests[{index}] action {action!r} failed: {source_path} -> {destination_path}"
                )

            duration_sec = round(time.perf_counter() - op_start, 3)
            logger.info(
                "Storage %s copy succeeded: source_path=%s destination_path=%s duration_sec=%s",
                action,
                source_path,
                destination_path,
                duration_sec,
            )

            outputs.append(
                {
                    "src": {"type": "file", "path": source_path, "size": size},
                    "dest": {"type": "file", "path": destination_path, "size": size},
                    "duration_sec": duration_sec,
                }
            )

    total_duration_sec = round(time.perf_counter() - handler_start, 3)
    logger.info(
        "Completed storage %s: task_id=%s transferred_items=%d total_duration_sec=%s",
        action,
        getattr(ctx, "task_id", None),
        len(outputs),
        total_duration_sec,
    )
    logger.debug("Storage %s outputs=%s", action, outputs)

    return TaskResult(
        task_id=ctx.task_id or "",
        handler=getattr(ctx, "task_handler", None) or f"oedis.handler.storage.{action}",
        status="success",
        outputs=outputs,
        metrics={
            "total_items": len(outputs),
            "total_duration_sec": total_duration_sec,
        },
    )


def download(ctx) -> TaskResult:
    """Transfer files from storage into the staging inbox."""
    logger.debug("download called for task_id=%s", getattr(ctx, "task_id", None))
    return _run_transfer(ctx, "download")


def upload(ctx) -> TaskResult:
    """Transfer files from staging inbox into storage."""
    logger.debug("upload called for task_id=%s", getattr(ctx, "task_id", None))
    return _run_transfer(ctx, "upload")
