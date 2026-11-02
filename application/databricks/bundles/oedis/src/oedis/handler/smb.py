from __future__ import annotations

import logging
import shutil
import stat as stat_module
import time
from fnmatch import fnmatch
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterator, List, Optional, Tuple

from smbclient import listdir, makedirs, open_file, register_session, remove, stat, walk

from oedis.handler.lib.utils import (
    apply_time_filter,
    matches_any,
    normalize_values,
    parse_datetime,
)
from oedis.task_result import TaskResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmbFile:
    path: str
    size: int
    mtime: float


@dataclass(frozen=True)
class DownloadItem:
    smb_file: SmbFile
    spec: Dict[str, Any]
    src_root: PureWindowsPath
    local_path: Path


@dataclass(frozen=True)
class UploadItem:
    local_path: Path
    remote_path: str
    spec: Dict[str, Any]


def _parse_unc_root(unc: Any) -> Tuple[str, PureWindowsPath]:
    unc_text = str(unc or "").strip()
    unc_path = PureWindowsPath(unc_text)

    if not unc_text:
        raise ValueError("connection.unc is required.")
    if not unc_text.startswith("\\\\"):
        raise ValueError(f"connection.unc must be a UNC path like \\\\server\\share, got: {unc_text}")

    drive = unc_path.drive.lstrip("\\")
    if not drive:
        raise ValueError(f"Unable to parse server/share from UNC path: {unc_text}")

    parts = drive.split("\\")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"connection.unc must include both server and share: {unc_text}")

    server = parts[0]
    return server, unc_path


def _parse_relative_dir(source_dir: Any) -> PureWindowsPath:
    normalized = str(source_dir or "").replace("/", "\\").strip("\\")
    return PureWindowsPath(normalized)


def _normalize_local_subdir(dst_dir: Any) -> Path:
    raw = str(dst_dir or "").strip()
    if not raw:
        return Path()

    normalized = raw.replace("\\", "/").strip("/")
    path = Path(normalized)

    if path.is_absolute():
        raise ValueError(f"dst_dir must be relative to ctx.inbox, got absolute path: {dst_dir}")

    if ".." in path.parts:
        raise ValueError(f"dst_dir must not contain '..': {dst_dir}")

    return path


def _validate_request_spec(spec: Any, index: int) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError(f"requests[{index}] must be an object.")

    include = spec.get("include")
    exclude = spec.get("exclude")
    recursive = spec.get("recursive")
    delete_source_file = spec.get("delete_source_file")
    dst_dir = spec.get("dst_dir")

    if include is not None and not isinstance(include, (str, list, tuple)):
        raise ValueError(f"requests[{index}].include must be a string or list of strings.")

    if exclude is not None and not isinstance(exclude, (str, list, tuple)):
        raise ValueError(f"requests[{index}].exclude must be a string or list of strings.")

    if recursive is not None and not isinstance(recursive, bool):
        raise ValueError(f"requests[{index}].recursive must be a boolean.")


    if delete_source_file is not None and not isinstance(delete_source_file, bool):
        raise ValueError(f"requests[{index}].delete_source_file must be a boolean.")

    if dst_dir is not None and not isinstance(dst_dir, str):
        raise ValueError(f"requests[{index}].dst_dir must be a string.")

    parse_datetime(spec.get("updated_after"), f"requests[{index}].updated_after")
    parse_datetime(spec.get("updated_before"), f"requests[{index}].updated_before")
    _normalize_local_subdir(dst_dir)

    return spec


def _validate_upload_request_spec(spec: Any, index: int) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError(f"requests[{index}] must be an object.")

    include = spec.get("include")
    exclude = spec.get("exclude")
    recursive = spec.get("recursive")
    delete_source_file = spec.get("delete_source_file")
    src_dir = spec.get("src_dir")

    if include is not None and not isinstance(include, (str, list, tuple)):
        raise ValueError(f"requests[{index}].include must be a string or list of strings.")

    if exclude is not None and not isinstance(exclude, (str, list, tuple)):
        raise ValueError(f"requests[{index}].exclude must be a string or list of strings.")

    if recursive is not None and not isinstance(recursive, bool):
        raise ValueError(f"requests[{index}].recursive must be a boolean.")


    if delete_source_file is not None and not isinstance(delete_source_file, bool):
        raise ValueError(f"requests[{index}].delete_source_file must be a boolean.")

    if src_dir is not None and not isinstance(src_dir, str):
        raise ValueError(f"requests[{index}].src_dir must be a string.")

    unsupported_filters = [
        field_name for field_name in ("updated_after", "updated_before") if field_name in spec
    ]
    if unsupported_filters:
        unsupported_list = ", ".join(f"requests[{index}].{field_name}" for field_name in unsupported_filters)
        raise ValueError(f"{unsupported_list} not supported for oedis.handler.smb.upload.")

    _normalize_local_subdir(src_dir)

    return spec


def _iter_smb_files(
    root: PureWindowsPath,
    recursive: bool,
    include: Optional[Any],
    exclude: Optional[Any],
    updated_after: Optional[datetime],
    updated_before: Optional[datetime],
) -> Iterator[SmbFile]:
    include_patterns = normalize_values(include) or ["*"]
    exclude_patterns = normalize_values(exclude)
    root_str = str(root)

    logger.debug(
        "Iterating SMB files: root=%s recursive=%s include=%s exclude=%s updated_after=%s updated_before=%s",
        root_str,
        recursive,
        include_patterns,
        exclude_patterns,
        updated_after,
        updated_before,
    )

    def handle_file(dir_path: str, filename: str) -> Optional[SmbFile]:
        file_path = str(PureWindowsPath(dir_path) / filename)
        file_stat = stat(file_path)
        if not stat_module.S_ISREG(file_stat.st_mode):
            logger.debug("Skipping non-regular SMB path: %s", file_path)
            return None

        relative_dir = PureWindowsPath(dir_path).relative_to(root)
        relative_path = PureWindowsPath(relative_dir, filename)
        relative_posix = relative_path.as_posix()

        if include_patterns and not (
            matches_any(relative_posix, include_patterns) or matches_any(filename, include_patterns)
        ):
            logger.debug(
                "Skipping SMB file not matched by include: file=%s relative=%s include=%s",
                filename,
                relative_posix,
                include_patterns,
            )
            return None

        if exclude_patterns and (
            matches_any(relative_posix, exclude_patterns) or matches_any(filename, exclude_patterns)
        ):
            logger.debug(
                "Skipping SMB file matched by exclude: file=%s relative=%s exclude=%s",
                filename,
                relative_posix,
                exclude_patterns,
            )
            return None

        if not apply_time_filter(file_stat.st_mtime, updated_after, updated_before):
            logger.debug(
                "Skipping SMB file by time filter: path=%s mtime=%s updated_after=%s updated_before=%s",
                file_path,
                datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
                updated_after,
                updated_before,
            )
            return None

        logger.debug(
            "Matched SMB file: path=%s size=%s mtime=%s",
            file_path,
            file_stat.st_size,
            datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
        )
        return SmbFile(
            path=file_path,
            size=file_stat.st_size,
            mtime=file_stat.st_mtime,
        )

    if recursive:
        for dir_path, _, filenames in walk(root_str):
            logger.debug("Walking SMB directory: %s (%s file(s))", dir_path, len(filenames))
            for filename in filenames:
                found = handle_file(dir_path, filename)
                if found:
                    yield found
    else:
        filenames = listdir(root_str)
        logger.debug("Listing SMB directory: %s (%s entry/entries)", root_str, len(filenames))
        for filename in filenames:
            found = handle_file(root_str, filename)
            if found:
                yield found


def _download_file(remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    logger.debug("Opening remote SMB file for download: %s", remote_path)
    with open_file(remote_path, mode="rb") as remote_handle:
        logger.debug("Opening local file for write: %s", local_path)
        with open(local_path, "wb") as local_handle:
            shutil.copyfileobj(remote_handle, local_handle)


def _upload_file(local_path: Path, remote_path: str) -> None:
    remote_parent = str(PureWindowsPath(remote_path).parent)
    if remote_parent:
        makedirs(remote_parent, exist_ok=True)

    logger.debug("Opening local file for SMB upload: %s", local_path)
    with open(local_path, "rb") as local_handle:
        logger.debug("Opening remote SMB file for write: %s", remote_path)
        with open_file(remote_path, mode="wb") as remote_handle:
            shutil.copyfileobj(local_handle, remote_handle)


def _ensure_local_download(local_path: Path, expected_size: Optional[int], remote_path: str) -> None:
    if not local_path.exists():
        raise RuntimeError(f"Local file missing after download for {remote_path}: {local_path}")

    if not local_path.is_file():
        raise RuntimeError(f"Local path is not a file after download for {remote_path}: {local_path}")

    local_size = local_path.stat().st_size
    logger.debug(
        "Validated local download candidate: remote=%s local=%s local_size=%s expected_size=%s",
        remote_path,
        local_path,
        local_size,
        expected_size,
    )

    if expected_size is not None and local_size != expected_size:
        raise RuntimeError(
            f"Downloaded file size mismatch for {remote_path}: "
            f"expected {expected_size}, got {local_size} at {local_path}"
        )


def _resolve_local_path(
    inbox: Path,
    smb_file: SmbFile,
    src_root: PureWindowsPath,
    dst_dir: Path,
) -> Path:
    remote_windows = PureWindowsPath(smb_file.path)
    relative_path = remote_windows.relative_to(src_root)
    resolved = inbox / dst_dir / Path(*relative_path.parts)

    logger.debug(
        "Resolved local path: remote=%s src_root=%s dst_dir=%s local=%s",
        smb_file.path,
        src_root,
        dst_dir,
        resolved,
    )
    return resolved


def _discover_items(
    unc_root: PureWindowsPath,
    inbox: Path,
    requests: List[Dict[str, Any]],
) -> List[DownloadItem]:
    discovered_map: Dict[str, DownloadItem] = {}
    local_targets: Dict[Path, str] = {}

    for index, raw_spec in enumerate(requests):
        spec = _validate_request_spec(raw_spec, index)

        src_dir = _parse_relative_dir(spec.get("src_dir"))
        recursive = bool(spec.get("recursive", False))
        include = spec.get("include")
        exclude = spec.get("exclude")
        updated_after = parse_datetime(spec.get("updated_after"), f"requests[{index}].updated_after")
        updated_before = parse_datetime(spec.get("updated_before"), f"requests[{index}].updated_before")
        dst_dir = _normalize_local_subdir(spec.get("dst_dir"))

        src_root = unc_root / src_dir

        logger.info(
            "Discovering SMB files: request_index=%s root=%s include=%s recursive=%s exclude=%s dst_dir=%s",
            index,
            src_root,
            include,
            recursive,
            exclude,
            dst_dir,
        )

        for smb_file in _iter_smb_files(
            root=src_root,
            recursive=recursive,
            include=include,
            exclude=exclude,
            updated_after=updated_after,
            updated_before=updated_before,
        ):
            if smb_file.path in discovered_map:
                logger.debug("Skipping duplicate discovered SMB file: %s", smb_file.path)
                continue

            local_path = _resolve_local_path(
                inbox=inbox,
                smb_file=smb_file,
                src_root=src_root,
                dst_dir=dst_dir,
            )

            existing_remote = local_targets.get(local_path)
            if existing_remote and existing_remote != smb_file.path:
                raise RuntimeError(
                    f"Multiple SMB files would be downloaded to the same local path {local_path}: "
                    f"{existing_remote} and {smb_file.path}"
                )

            item = DownloadItem(
                smb_file=smb_file,
                spec=spec,
                src_root=src_root,
                local_path=local_path,
            )
            discovered_map[smb_file.path] = item
            local_targets[local_path] = smb_file.path

            logger.debug(
                "Queued SMB download item: remote=%s local=%s size=%s",
                smb_file.path,
                local_path,
                smb_file.size,
            )

    return list(discovered_map.values())


def _iter_local_files(
    local_root: Path,
    recursive: bool,
    include: Optional[Any],
    exclude: Optional[Any],
) -> Iterator[Path]:
    include_patterns = normalize_values(include) or ["*"]
    exclude_patterns = normalize_values(exclude)

    if not local_root.exists():
        raise RuntimeError(f"Local source directory does not exist: {local_root}")
    if not local_root.is_dir():
        raise RuntimeError(f"Local source path is not a directory: {local_root}")

    iterator = local_root.rglob("*") if recursive else local_root.glob("*")
    for local_path in iterator:
        if not local_path.is_file():
            continue

        relative_posix = local_path.relative_to(local_root).as_posix()
        if include_patterns and not (
            any(fnmatch(relative_posix, pattern) for pattern in include_patterns)
            or any(fnmatch(local_path.name, pattern) for pattern in include_patterns)
        ):
            continue

        if exclude_patterns and (
            any(fnmatch(relative_posix, pattern) for pattern in exclude_patterns)
            or any(fnmatch(local_path.name, pattern) for pattern in exclude_patterns)
        ):
            continue

        yield local_path


def _discover_upload_items(
    unc_root: PureWindowsPath,
    inbox: Path,
    requests: List[Dict[str, Any]],
) -> List[UploadItem]:
    items: List[UploadItem] = []
    remote_targets: Dict[str, Path] = {}

    for index, raw_spec in enumerate(requests):
        spec = _validate_upload_request_spec(raw_spec, index)

        remote_dir = _parse_relative_dir(spec.get("dst_dir"))
        local_dir = _normalize_local_subdir(spec.get("src_dir"))
        include = spec.get("include")
        exclude = spec.get("exclude")
        recursive = bool(spec.get("recursive", False))

        remote_root = unc_root / remote_dir
        local_root = inbox / local_dir

        for local_path in _iter_local_files(
            local_root=local_root,
            recursive=recursive,
            include=include,
            exclude=exclude,
        ):
            relative_path = local_path.relative_to(local_root)
            remote_path = str(PureWindowsPath(remote_root, *relative_path.parts))

            existing_source = remote_targets.get(remote_path)
            if existing_source and existing_source != local_path:
                raise RuntimeError(
                    f"Multiple local files would be uploaded to the same remote path {remote_path}: "
                    f"{existing_source} and {local_path}"
                )

            items.append(
                UploadItem(
                    local_path=local_path,
                    remote_path=remote_path,
                    spec=spec,
                )
            )
            remote_targets[remote_path] = local_path

    return items


def download(ctx) -> TaskResult:
    start = time.perf_counter()
    task_params = getattr(ctx, "task_params", None) or {}
    logger.info("task params: %s", task_params)

    connection = task_params.get("connection") or {}
    requests = task_params.get("requests") or []

    if not isinstance(requests, list):
        raise ValueError("requests must be a list.")

    server, unc_root = _parse_unc_root(connection.get("unc"))
    auth = connection.get("auth") or {}
    username = auth.get("username")
    password = auth.get("password")

    logger.debug(
        "SMB connection config: server=%s share_root=%s username=%s request_count=%s",
        server,
        unc_root,
        username,
        len(requests),
    )

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")

    inbox = Path(ctx.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    logger.debug("Using local inbox: %s", inbox)

    register_session(server, username=username, password=password)
    logger.info("Registered SMB session for %s", server)

    items = _discover_items(unc_root=unc_root, inbox=inbox, requests=requests)
    logger.info("Discovered %s unique SMB file(s)", len(items))
    logger.info(
        "SMB delete_source_file requested: %s",
        any(bool(spec.get("delete_source_file", False)) for spec in requests),
    )

    outputs: List[Dict[str, Any]] = []

    for item in items:
        smb_file = item.smb_file
        spec = item.spec
        remote_path = smb_file.path
        local_path = item.local_path

        logger.info("Downloading %s -> %s", remote_path, local_path)
        download_start = time.perf_counter()
        _download_file(remote_path, local_path)
        duration_sec = round(time.perf_counter() - download_start, 3)

        _ensure_local_download(local_path, smb_file.size, remote_path)

        logger.info(
            "Downloaded SMB file: remote=%s local=%s size=%s duration_sec=%s",
            remote_path,
            local_path,
            smb_file.size,
            duration_sec,
        )

        if bool(spec.get("delete_source_file", False)):
            try:
                logger.info("Deleting remote SMB file after download: %s", remote_path)
                remove(remote_path)
                logger.info("Deleted remote SMB file: %s", remote_path)
            except Exception as exc:
                logger.exception("Failed to delete remote file %s", remote_path)
                raise RuntimeError(f"Failed to delete remote file {remote_path}") from exc

        outputs.append(
            {
                "src": {
                    "type": "file",
                    "path": remote_path,
                    "size": smb_file.size,
                    "mtime": datetime.fromtimestamp(smb_file.mtime).isoformat(),
                },
                "dest": {
                    "type": "file",
                    "path": str(local_path),
                    "size": local_path.stat().st_size,
                },
                "duration_sec": duration_sec,
            }
        )

    total_duration_sec = round(time.perf_counter() - start, 3)
    logger.info(
        "SMB download completed: total_items=%s total_duration_sec=%s",
        len(outputs),
        total_duration_sec,
    )

    return TaskResult(
        task_id=ctx.task_id or "",
        handler=ctx.task_handler or "oedis.handler.smb.download",
        status="success",
        outputs=outputs,
        metrics={
            "total_items": len(outputs),
            "total_duration_sec": total_duration_sec,
        },
    )


def upload(ctx) -> TaskResult:
    start = time.perf_counter()
    task_params = getattr(ctx, "task_params", None) or {}
    logger.info("task params: %s", task_params)

    connection = task_params.get("connection") or {}
    requests = task_params.get("requests") or []

    if not isinstance(requests, list):
        raise ValueError("requests must be a list.")

    server, unc_root = _parse_unc_root(connection.get("unc"))
    auth = connection.get("auth") or {}
    username = auth.get("username")
    password = auth.get("password")

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")
    inbox = Path(ctx.inbox)
    inbox.mkdir(parents=True, exist_ok=True)

    register_session(server, username=username, password=password)
    logger.info("Registered SMB session for %s", server)

    items = _discover_upload_items(unc_root=unc_root, inbox=inbox, requests=requests)
    logger.info("Discovered %s local file(s) for SMB upload", len(items))

    outputs: List[Dict[str, Any]] = []
    for item in items:
        logger.info("Uploading %s -> %s", item.local_path, item.remote_path)
        logger.debug("SMB upload item spec=%s", item.spec)
        upload_start = time.perf_counter()
        _upload_file(item.local_path, item.remote_path)
        duration_sec = round(time.perf_counter() - upload_start, 3)

        local_stat = item.local_path.stat()
        remote_stat = stat(item.remote_path)
        local_size = local_stat.st_size
        if local_size != remote_stat.st_size:
            raise RuntimeError(
                f"Uploaded file size mismatch for {item.remote_path}: expected {local_size}, got {remote_stat.st_size}"
            )
        logger.info(
            "Uploaded %s -> %s size=%s duration_sec=%s",
            item.local_path,
            item.remote_path,
            local_size,
            duration_sec,
        )

        if bool(item.spec.get("delete_source_file", False)):
            logger.info("Deleting local source file after SMB upload: %s", item.local_path)
            item.local_path.unlink()
            logger.info("Deleted local source file after SMB upload: %s", item.local_path)

        outputs.append(
            {
                "src": {
                    "type": "file",
                    "path": str(item.local_path),
                    "size": local_size,
                    "mtime": datetime.fromtimestamp(local_stat.st_mtime).isoformat(),
                },
                "dest": {
                    "type": "file",
                    "path": item.remote_path,
                    "size": remote_stat.st_size,
                },
                "duration_sec": duration_sec,
            }
        )

    total_duration_sec = round(time.perf_counter() - start, 3)
    logger.info("SMB upload completed: total_items=%s total_duration_sec=%s", len(outputs), total_duration_sec)
    return TaskResult(
        task_id=ctx.task_id or "",
        handler=ctx.task_handler or "oedis.handler.smb.upload",
        status="success",
        outputs=outputs,
        metrics={
            "total_items": len(outputs),
            "total_duration_sec": total_duration_sec,
        },
    )
