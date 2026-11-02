from __future__ import annotations

import fnmatch
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

from oedis.handler.lib.utils import resolve_inbox_path
from oedis.task_result import TaskResult


logger = logging.getLogger(__name__)


# normalize a value to a list of strings with a default fallback.
def _ensure_list(value: Any, *, default: Iterable[str]) -> List[str]:
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


# resolve a user-provided directory path relative to the inbox.
def _resolve_dir(inbox: Path, path_value: Any) -> Path:
    resolved = resolve_inbox_path(inbox, path_value)
    logger.debug(
        "_resolve_dir resolved path: inbox=%s input=%r resolved=%s",
        inbox,
        path_value,
        resolved,
    )
    return resolved


# check whether a relative path matches any glob pattern.
def _matches_any(rel_path: str, patterns: Iterable[str]) -> bool:
    patterns_list = list(patterns)
    matched = any(fnmatch.fnmatch(rel_path, pattern) for pattern in patterns_list)
    logger.debug(
        "_matches_any rel_path=%s patterns=%s matched=%s",
        rel_path,
        patterns_list,
        matched,
    )
    return matched


# select files under a source directory based on include/exclude rules.
def _select_files(
    base_dir: Path,
    include: Iterable[str],
    exclude: Iterable[str],
    recursive: bool,
    allow_dirs: bool = False,
) -> List[Path]:
    include_patterns = list(include)
    exclude_patterns = list(exclude)
    logger.debug(
        "_select_files start: base_dir=%s include=%s exclude=%s recursive=%s allow_dirs=%s",
        base_dir,
        include_patterns,
        exclude_patterns,
        recursive,
        allow_dirs,
    )

    if recursive:
        candidates = [
            path
            for path in base_dir.rglob("*")
            if path.is_file() or (allow_dirs and path.is_dir())
        ]
    else:
        candidates = [
            path
            for path in base_dir.iterdir()
            if path.is_file() or (allow_dirs and path.is_dir())
        ]

    matches: List[Path] = []
    for path in candidates:
        rel = path.relative_to(base_dir).as_posix()
        if not _matches_any(rel, include_patterns):
            logger.debug(
                "_select_files skipping path due to include mismatch: base_dir=%s path=%s rel=%s",
                base_dir,
                path,
                rel,
            )
            continue
        if _matches_any(rel, exclude_patterns):
            logger.debug(
                "_select_files excluding path: base_dir=%s path=%s rel=%s",
                base_dir,
                path,
                rel,
            )
            continue
        matches.append(path)
        logger.debug(
            "_select_files matched path: base_dir=%s path=%s rel=%s",
            base_dir,
            path,
            rel,
        )

    if allow_dirs:
        result = sorted(
            matches,
            key=lambda path: len(path.relative_to(base_dir).parts),
            reverse=True,
        )
    else:
        result = sorted(matches)

    logger.debug(
        "_select_files complete: base_dir=%s candidate_count=%d matched_count=%d matches=%s",
        base_dir,
        len(candidates),
        len(result),
        result,
    )
    return result


# build the destination path for a matched source file.
def _build_dest_path(
    src_path: Path,
    *,
    base_dir: Path,
    dst_root: Path,
    dst_name: str | None,
) -> Path:
    relative_path = src_path.relative_to(base_dir)
    if dst_name:
        return dst_root / relative_path.parent / dst_name
    return dst_root / relative_path


# validate a transfer spec for copy/move.
def _validate_transfer_spec(spec: Dict[str, Any], index: int, action: str) -> None:
    dst_name = spec.get("dst_name")
    if dst_name in ("", None):
        return
    if not isinstance(dst_name, str) or not dst_name.strip():
        raise ValueError(
            f"requests[{index}] action '{action}' dst_name must be a non-empty string"
        )


# validate a delete spec.
def _validate_delete_spec(spec: Dict[str, Any], index: int) -> None:
    if spec.get("dst_dir") or spec.get("dst_name"):
        raise ValueError(f"requests[{index}] action 'delete' forbids dst_dir or dst_name")


def _normalize_relative_dir(inbox: Path, path_value: Any, *, field_name: str) -> str:
    resolved = resolve_inbox_path(inbox, path_value, field_name=field_name)
    relative = resolved.relative_to(inbox.resolve()).as_posix()
    logger.debug(
        "_normalize_relative_dir resolved field=%s input=%r relative=%s",
        field_name,
        path_value,
        relative,
    )
    return relative


def _normalize_operation(value: Any, *, index: int) -> str:
    if value in (None, ""):
        return "move"
    if value not in {"move", "copy"}:
        raise ValueError(
            f"requests[{index}].operation must be one of ['move', 'copy']"
        )
    return str(value)


def _normalize_on_collision(value: Any, *, index: int) -> str:
    if value in (None, ""):
        return "error"
    if value not in {"error", "overwrite", "rename"}:
        raise ValueError(
            "requests[{index}].on_collision must be one of "
            "['error', 'overwrite', 'rename']".format(index=index)
        )
    return str(value)


def _flatten_sort_key(base_dir: Path, src_path: Path) -> str:
    return src_path.relative_to(base_dir).as_posix()


def _rename_with_suffix(file_name: str, suffix_index: int) -> str:
    path = Path(file_name)
    suffix = "".join(path.suffixes)
    stem = file_name[: -len(suffix)] if suffix else file_name
    return f"{stem}_{suffix_index}{suffix}"


def _plan_flatten_paths(
    *,
    base_dir: Path,
    dst_root: Path,
    matches: Iterable[Path],
    on_collision: str,
) -> List[Dict[str, Any]]:
    ordered_matches = sorted(matches, key=lambda path: _flatten_sort_key(base_dir, path))
    logger.debug(
        "_plan_flatten_paths start: base_dir=%s dst_root=%s on_collision=%s ordered_matches=%s",
        base_dir,
        dst_root,
        on_collision,
        ordered_matches,
    )

    if on_collision == "error":
        seen_names: Dict[str, Path] = {}
        duplicate_names: List[str] = []
        for src_path in ordered_matches:
            source_name = src_path.name
            if source_name in seen_names:
                duplicate_names.append(source_name)
                continue
            seen_names[source_name] = src_path
        if duplicate_names:
            duplicate_list = ", ".join(sorted(set(duplicate_names)))
            raise RuntimeError(
                "Flatten destination name collision(s) detected: "
                f"{duplicate_list}. Use on_collision='overwrite' or 'rename'."
            )

    plan: List[Dict[str, Any]] = []
    collision_counts: Dict[str, int] = {}

    for src_path in ordered_matches:
        relative_path = _flatten_sort_key(base_dir, src_path)
        source_name = src_path.name

        if on_collision == "rename":
            suffix_index = collision_counts.get(source_name, 0)
            dest_name = (
                source_name
                if suffix_index == 0
                else _rename_with_suffix(source_name, suffix_index)
            )
            collision_counts[source_name] = suffix_index + 1
        else:
            dest_name = source_name

        plan.append(
            {
                "src_path": src_path,
                "dest_path": dst_root / dest_name,
                "relative_path": relative_path,
            }
        )

    logger.debug("_plan_flatten_paths complete: plan=%s", plan)
    return plan


def _overwrite_destination(dest: Path) -> None:
    if not dest.exists():
        return
    if dest.is_dir():
        raise IsADirectoryError(f"Destination exists as a directory: {dest}")
    dest.unlink()


def _copy_file(src: Path, dest: Path) -> None:
    if src == dest:
        logger.debug("_copy_file skipping same-file copy for path=%s", src)
        return
    _overwrite_destination(dest)
    shutil.copy2(src, dest)


def _move_file(src: Path, dest: Path) -> None:
    if src == dest:
        logger.debug("_move_file skipping same-file move for path=%s", src)
        return
    _overwrite_destination(dest)
    shutil.move(str(src), str(dest))


# execute a file transfer operation such as copy or move.
def _execute_transfer(
    *,
    ctx,
    action: str,
    handler_name: str,
    op: Callable[[Path, Path], None],
) -> TaskResult:
    task_param = getattr(ctx, "task_params", None) or {}
    specs = task_param.get("requests") or []

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")
    inbox = Path(ctx.inbox)

    logger.info(
        "Starting file %s: task_id=%s handler=%s inbox=%s spec_count=%d",
        action,
        getattr(ctx, "task_id", None),
        handler_name,
        inbox,
        len(specs),
    )
    logger.debug("File %s raw task_params=%s", action, task_param)

    handler_start = time.perf_counter()
    outputs: List[Dict[str, Any]] = []

    for index, spec in enumerate(specs):
        logger.info("Processing file %s spec index=%d", action, index)
        logger.debug("File %s spec[%d]=%s", action, index, spec)
        _validate_transfer_spec(spec, index, action)

        base_dir = _resolve_dir(inbox, spec.get("src_dir"))
        if not base_dir.exists():
            raise FileNotFoundError(
                f"requests[{index}] action '{action}' src_dir not found: {base_dir}"
            )
        if not base_dir.is_dir():
            raise NotADirectoryError(
                f"requests[{index}] action '{action}' src_dir is not a directory: {base_dir}"
            )

        include = _ensure_list(spec.get("include"), default=["*"])
        exclude = _ensure_list(spec.get("exclude"), default=[])
        recursive = bool(spec.get("recursive", False))
        dst_name = spec.get("dst_name")

        logger.debug(
            "Resolved file %s spec[%d]: base_dir=%s include=%s exclude=%s recursive=%s dst_name=%r",
            action,
            index,
            base_dir,
            include,
            exclude,
            recursive,
            dst_name,
        )

        matches = _select_files(
            base_dir,
            include=include,
            exclude=exclude,
            recursive=recursive,
        )

        logger.info(
            "File %s spec[%d] matched %d items from base_dir=%s",
            action,
            index,
            len(matches),
            base_dir,
        )
        logger.debug("File %s spec[%d] matches=%s", action, index, matches)

        if dst_name and len(matches) != 1:
            raise RuntimeError(
                f"requests[{index}] action '{action}' dst_name requires exactly one matched file"
            )

        dst_root = _resolve_dir(inbox, spec.get("dst_dir"))
        dst_root.mkdir(parents=True, exist_ok=True)
        logger.debug(
            "Prepared file %s destination root for spec[%d]: dst_root=%s",
            action,
            index,
            dst_root,
        )

        for src_path in matches:
            op_start = time.perf_counter()
            dest = _build_dest_path(
                src_path,
                base_dir=base_dir,
                dst_root=dst_root,
                dst_name=dst_name,
            )
            dest.parent.mkdir(parents=True, exist_ok=True)

            logger.debug(
                "Executing file %s operation: src_path=%s dest=%s",
                action,
                src_path,
                dest,
            )
            op(src_path, dest)
            duration_sec = round(time.perf_counter() - op_start, 3)
            logger.info(
                "Completed file %s operation: src_path=%s dest=%s duration_sec=%s",
                action,
                src_path,
                dest,
                duration_sec,
            )

            outputs.append(
                {
                    "src": {"type": "file", "path": str(src_path)},
                    "dest": {"type": "file", "path": str(dest)},
                    "duration_sec": duration_sec,
                }
            )

    total_duration_sec = round(time.perf_counter() - handler_start, 3)
    logger.info(
        "Completed file %s: task_id=%s transferred_items=%d total_duration_sec=%s",
        action,
        getattr(ctx, "task_id", None),
        len(outputs),
        total_duration_sec,
    )
    logger.debug("File %s outputs=%s", action, outputs)

    return TaskResult(
        task_id=ctx.task_id or "",
        handler=handler_name,
        status="success",
        outputs=outputs,
        metrics={
            "total_items": len(outputs),
            "total_duration_sec": total_duration_sec,
        },
    )


def copy(ctx) -> TaskResult:
    return _execute_transfer(
        ctx=ctx,
        action="copy",
        handler_name="oedis.handler.file.copy",
        op=lambda src, dest: _copy_file(src, dest),
    )


def move(ctx) -> TaskResult:
    return _execute_transfer(
        ctx=ctx,
        action="move",
        handler_name="oedis.handler.file.move",
        op=lambda src, dest: _move_file(src, dest),
    )


def flatten(ctx) -> TaskResult:
    task_param = getattr(ctx, "task_params", None) or {}
    specs = task_param.get("requests") or []

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")
    inbox = Path(ctx.inbox)

    logger.info(
        "Starting file flatten: task_id=%s handler=%s inbox=%s spec_count=%d",
        getattr(ctx, "task_id", None),
        "oedis.handler.file.flatten",
        inbox,
        len(specs),
    )
    logger.debug("File flatten raw task_params=%s", task_param)

    handler_start = time.perf_counter()
    outputs: List[Dict[str, Any]] = []

    for index, spec in enumerate(specs):
        logger.info("Processing file flatten spec index=%d", index)
        logger.debug("File flatten spec[%d]=%s", index, spec)

        src_relative = _normalize_relative_dir(
            inbox,
            spec.get("src_dir"),
            field_name="src_dir",
        )
        dst_relative = _normalize_relative_dir(
            inbox,
            spec.get("dst_dir"),
            field_name="dst_dir",
        )
        if src_relative == dst_relative:
            raise ValueError(
                f"requests[{index}] src_dir and dst_dir must resolve to different inbox paths"
            )

        base_dir = _resolve_dir(inbox, spec.get("src_dir"))
        if not base_dir.exists():
            raise FileNotFoundError(
                f"requests[{index}] action 'flatten' src_dir not found: {base_dir}"
            )
        if not base_dir.is_dir():
            raise NotADirectoryError(
                f"requests[{index}] action 'flatten' src_dir is not a directory: {base_dir}"
            )

        dst_root = _resolve_dir(inbox, spec.get("dst_dir"))
        include = _ensure_list(spec.get("include"), default=["*"])
        exclude = _ensure_list(spec.get("exclude"), default=[])
        operation = _normalize_operation(spec.get("operation"), index=index)
        on_collision = _normalize_on_collision(
            spec.get("on_collision"),
            index=index,
        )
        op = _move_file if operation == "move" else _copy_file

        logger.debug(
            "Resolved file flatten spec[%d]: base_dir=%s dst_root=%s include=%s exclude=%s operation=%s on_collision=%s",
            index,
            base_dir,
            dst_root,
            include,
            exclude,
            operation,
            on_collision,
        )

        matches = _select_files(
            base_dir,
            include=include,
            exclude=exclude,
            recursive=True,
        )
        matches = sorted(matches, key=lambda path: _flatten_sort_key(base_dir, path))

        logger.info(
            "File flatten spec[%d] matched %d items from base_dir=%s",
            index,
            len(matches),
            base_dir,
        )
        logger.debug("File flatten spec[%d] matches=%s", index, matches)

        plan = _plan_flatten_paths(
            base_dir=base_dir,
            dst_root=dst_root,
            matches=matches,
            on_collision=on_collision,
        )
        logger.debug("File flatten spec[%d] planned writes=%s", index, plan)

        for item in plan:
            src_path = item["src_path"]
            dest = item["dest_path"]
            op_start = time.perf_counter()
            dest.parent.mkdir(parents=True, exist_ok=True)

            logger.debug(
                "Executing file flatten operation: src_path=%s dest=%s operation=%s",
                src_path,
                dest,
                operation,
            )
            op(src_path, dest)
            duration_sec = round(time.perf_counter() - op_start, 3)
            logger.info(
                "Completed file flatten operation: src_path=%s dest=%s operation=%s duration_sec=%s",
                src_path,
                dest,
                operation,
                duration_sec,
            )

            outputs.append(
                {
                    "src": {"type": "file", "path": str(src_path)},
                    "dest": {"type": "file", "path": str(dest)},
                    "duration_sec": duration_sec,
                }
            )

    total_duration_sec = round(time.perf_counter() - handler_start, 3)
    logger.info(
        "Completed file flatten: task_id=%s processed_items=%d total_duration_sec=%s",
        getattr(ctx, "task_id", None),
        len(outputs),
        total_duration_sec,
    )
    logger.debug("File flatten outputs=%s", outputs)

    return TaskResult(
        task_id=ctx.task_id or "",
        handler="oedis.handler.file.flatten",
        status="success",
        outputs=outputs,
        metrics={
            "total_items": len(outputs),
            "total_duration_sec": total_duration_sec,
        },
    )


def delete(ctx) -> TaskResult:
    task_param = getattr(ctx, "task_params", None) or {}
    specs = task_param.get("requests") or []

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")
    inbox = Path(ctx.inbox)

    logger.info(
        "Starting file delete: task_id=%s handler=%s inbox=%s spec_count=%d",
        getattr(ctx, "task_id", None),
        "oedis.handler.file.delete",
        inbox,
        len(specs),
    )
    logger.debug("File delete raw task_params=%s", task_param)

    handler_start = time.perf_counter()
    outputs: List[Dict[str, Any]] = []

    for index, spec in enumerate(specs):
        logger.info("Processing file delete spec index=%d", index)
        logger.debug("File delete spec[%d]=%s", index, spec)
        _validate_delete_spec(spec, index)

        base_dir = _resolve_dir(inbox, spec.get("src_dir"))
        if not base_dir.exists():
            raise FileNotFoundError(
                f"requests[{index}] action 'delete' src_dir not found: {base_dir}"
            )
        if not base_dir.is_dir():
            raise NotADirectoryError(
                f"requests[{index}] action 'delete' src_dir is not a directory: {base_dir}"
            )

        include = _ensure_list(spec.get("include"), default=["*"])
        exclude = _ensure_list(spec.get("exclude"), default=[])
        recursive = bool(spec.get("recursive", False))

        logger.debug(
            "Resolved file delete spec[%d]: base_dir=%s include=%s exclude=%s recursive=%s",
            index,
            base_dir,
            include,
            exclude,
            recursive,
        )

        matches = _select_files(
            base_dir,
            include=include,
            exclude=exclude,
            recursive=recursive,
            allow_dirs=True,
        )

        logger.info(
            "File delete spec[%d] matched %d items from base_dir=%s",
            index,
            len(matches),
            base_dir,
        )
        logger.debug("File delete spec[%d] matches=%s", index, matches)

        for src_path in matches:
            op_start = time.perf_counter()
            logger.debug("Executing file delete operation: src_path=%s", src_path)
            if src_path.is_dir():
                shutil.rmtree(src_path)
            else:
                src_path.unlink()
            duration_sec = round(time.perf_counter() - op_start, 3)
            logger.info(
                "Completed file delete operation: src_path=%s duration_sec=%s",
                src_path,
                duration_sec,
            )

            outputs.append(
                {
                    "src": {"type": "file", "path": str(src_path)},
                    "dest": {"type": "file", "path": ""},
                    "duration_sec": duration_sec,
                }
            )

    total_duration_sec = round(time.perf_counter() - handler_start, 3)
    logger.info(
        "Completed file delete: task_id=%s deleted_items=%d total_duration_sec=%s",
        getattr(ctx, "task_id", None),
        len(outputs),
        total_duration_sec,
    )
    logger.debug("File delete outputs=%s", outputs)

    return TaskResult(
        task_id=ctx.task_id or "",
        handler="oedis.handler.file.delete",
        status="success",
        outputs=outputs,
        metrics={
            "total_items": len(outputs),
            "total_duration_sec": total_duration_sec,
        },
    )
