# src/oedis/handler/sftp.py
from __future__ import annotations

import base64
import hashlib
import os
import queue
import stat
import threading
import time
import tempfile
import logging
from datetime import datetime
from dataclasses import dataclass
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Optional, Iterator, Dict, Any, List, Tuple, Sequence

import paramiko

from oedis.task_result import TaskResult
from oedis.handler.lib.utils import (
    apply_time_filter,
    matches_any,
    normalize_values,
    parse_datetime,
    resolve_inbox_path,
)

logger = logging.getLogger(__name__)

# ----------------------------
# Connection factory
# ----------------------------
class SFTPConnectionFactory:
    def __init__(
        self,
        hostname: str,
        port: int,
        username: str,
        password: Optional[str] = None,
        pkey_b64: Optional[str] = None,
        passphrase: Optional[str] = None,
        keepalive_sec: int = 30,
        disabled_algorithms: Optional[Dict[str, Any]] = None,
        allow_auto_add_host_key: bool = True,
        known_hosts: Optional[str] = None,
        fingerprints: Optional[Sequence[str]] = None,
    ) -> None:
        # Capture connection/auth inputs for later connection creation.
        self.hostname = hostname
        self.port = int(port)
        self.username = username
        self.password = password

        # Store key material (base64 or PEM) and optional passphrase.
        self.pkey_b64 = pkey_b64
        self.passphrase = passphrase or None

        # Keepalive tuning and host verification settings.
        self.keepalive_sec = int(keepalive_sec)
        self.allow_auto_add_host_key = allow_auto_add_host_key

        if disabled_algorithms is None:
            self.disabled_algorithms = None
        elif isinstance(disabled_algorithms, dict):
            self.disabled_algorithms = disabled_algorithms
        else:
            raise ValueError("connection.disabled_algorithms must be an object/dict when provided.")

        self.known_hosts = known_hosts
        self.fingerprints = list(fingerprints or [])

    def _load_pkey(self) -> Optional[paramiko.PKey]:
        # Decode private key text from PEM or base64, then parse supported types.
        if not self.pkey_b64:
            return None

        pem_candidate = self.pkey_b64.strip()
        if "-----BEGIN" in pem_candidate:
            pem_text = pem_candidate
        else:
            pem_text = base64.b64decode(self.pkey_b64).decode("utf-8")
        buf = StringIO(pem_text)

        for key_cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey):
            buf.seek(0)
            try:
                return key_cls.from_private_key(buf, password=self.passphrase)
            except Exception:
                continue

        raise ValueError("Unsupported private key type or incorrect passphrase.")

    def create(self) -> paramiko.SFTPClient:
        logger.info(
            "Opening SFTP connection to %s:%s (user=%s, keepalive=%ss, known_hosts=%s, fingerprints=%s)",
            self.hostname,
            self.port,
            self.username,
            self.keepalive_sec,
            bool(self.known_hosts),
            bool(self.fingerprints),
        )
        # Build and configure the SSH client with host-key verification.
        ssh = paramiko.SSHClient()
        if self.known_hosts:
            with tempfile.NamedTemporaryFile("w", delete=False) as handle:
                handle.write(self.known_hosts)
                known_hosts_path = handle.name
            try:
                ssh.load_host_keys(known_hosts_path)
            finally:
                os.unlink(known_hosts_path)
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        elif self.fingerprints:
            ssh.set_missing_host_key_policy(_FingerprintPolicy(self.fingerprints))
        elif self.allow_auto_add_host_key:
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Parse the private key if provided.
        pkey = self._load_pkey()

        # Establish the SSH connection for SFTP usage.
        ssh.connect(
            hostname=self.hostname,
            port=self.port,
            username=self.username,
            pkey=pkey,
            password=self.password,
            look_for_keys=False,
            allow_agent=False,
            disabled_algorithms=self.disabled_algorithms,
        )

        # Enable keepalives on the SSH transport when available.
        tr = ssh.get_transport()
        if tr is not None:
            tr.set_keepalive(self.keepalive_sec)

        # Open the SFTP channel for file operations.
        sftp = ssh.open_sftp()

        # Close SFTP -> also closes SSH (caller only closes sftp)
        _orig_close = sftp.close

        def _close_both(*args, **kwargs):
            # Ensure the SSH session closes even if SFTP close raises.
            try:
                return _orig_close(*args, **kwargs)
            finally:
                ssh.close()

        sftp.close = _close_both  # type: ignore[attr-defined]
        return sftp


# ----------------------------
# Types + helpers
# ----------------------------
@dataclass
class SftpFile:
    # Normalized file metadata for discovered remote files.
    path: str
    size: int
    mtime: int
    atime: int


# normalize host key fingerprint values into a list of strings.
def _normalize_fingerprint_values(values: Optional[Sequence[Any]]) -> List[str]:
    # Normalize fingerprints from a validated list.
    if not values:
        return []
    return normalize_values(values)


# normalize known_hosts inputs into a newline-delimited string.
def _normalize_known_hosts(value: Optional[Sequence[Any]]) -> Optional[str]:
    if not value:
        return None
    lines = [str(item).rstrip() for item in value if item and str(item).strip()]
    return "\n".join(lines) if lines else None


# normalize a fingerprint string for consistent comparison.
def _normalize_fingerprint(value: str) -> str:
    # Standardize fingerprint representation (md5/sha256, colons, casing).
    normalized = value.strip().lower()
    if normalized.startswith("md5:"):
        normalized = normalized[4:]
    if normalized.startswith("sha256:"):
        return normalized
    return normalized.replace(":", "")


# compute supported fingerprints for a Paramiko host key.
def _key_fingerprints(key: paramiko.PKey) -> List[str]:
    # Compute MD5 and SHA256 fingerprints from a host key.
    md5_bytes = key.get_fingerprint()
    md5_hex = "".join(f"{byte:02x}" for byte in md5_bytes)
    md5_colon = ":".join(f"{byte:02x}" for byte in md5_bytes)
    fingerprints = {_normalize_fingerprint(md5_hex), _normalize_fingerprint(md5_colon)}

    try:
        key_bytes = base64.b64decode(key.get_base64().encode("utf-8"))
        sha256_b64 = base64.b64encode(hashlib.sha256(key_bytes).digest()).decode("utf-8").rstrip("=")
        fingerprints.add(_normalize_fingerprint(f"sha256:{sha256_b64}"))
        fingerprints.add(_normalize_fingerprint(sha256_b64))
    except Exception:
        pass

    return list(fingerprints)


class _FingerprintPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, fingerprints: Sequence[str]) -> None:
        # Cache the normalized expected fingerprint set.
        self.expected = {_normalize_fingerprint(fp) for fp in fingerprints if fp and str(fp).strip()}

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        # Accept the host only if any observed fingerprint is expected.
        observed = set(_key_fingerprints(key))
        if self.expected.intersection(observed):
            return
        raise paramiko.SSHException(
            f"Host key for {hostname} does not match expected fingerprints."
        )


# list files in an SFTP directory with optional recursion.
def sftp_list_files(
    sftp,
    root: str,
    base_root: Optional[str] = None,
    recursive: bool = True,
    include: Optional[Any] = None,
    exclude: Optional[Any] = None,
    updated_after: Optional[datetime] = None,
    updated_before: Optional[datetime] = None,
) -> Iterator[SftpFile]:
    # Walk an SFTP directory tree and yield matching files.
    include_patterns = normalize_values(include) or ["*"]
    exclude_patterns = normalize_values(exclude)
    if base_root is None:
        base_root = root
    for entry in sftp.listdir_attr(root):
        # Build the full remote path for each entry.
        path = (PurePosixPath(root) / entry.filename).as_posix()

        if stat.S_ISDIR(entry.st_mode):
            if recursive:
                # Recurse into subdirectories if enabled.
                yield from sftp_list_files(
                    sftp,
                    path,
                    base_root=base_root,
                    recursive=recursive,
                    include=include,
                    exclude=exclude,
                    updated_after=updated_after,
                    updated_before=updated_before,
                )
            continue

        filename = entry.filename
        relative_path = PurePosixPath(path).relative_to(PurePosixPath(base_root)).as_posix()

        # Apply include/exclude filename filters.
        if include_patterns and not (
            matches_any(filename, include_patterns) or matches_any(relative_path, include_patterns)
        ):
            continue

        if exclude_patterns and (
            matches_any(filename, exclude_patterns) or matches_any(relative_path, exclude_patterns)
        ):
            continue
        if not apply_time_filter(entry.st_mtime, updated_after, updated_before):
            continue

        # Yield normalized file metadata.
        yield SftpFile(
            path=path,
            size=entry.st_size,
            mtime=entry.st_mtime,
            atime=entry.st_atime,
        )


def _normalize_remote_dir(value: Any, field_name: str, *, allow_absolute: bool) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string when provided.")

    normalized = PurePosixPath(value.strip()).as_posix()
    if normalized in ("", "."):
        return ""
    if not allow_absolute and PurePosixPath(normalized).is_absolute():
        raise ValueError(f"{field_name} must be a relative remote path when provided.")
    return normalized


def _join_remote_dir(root_dir: str, src_dir: str) -> str:
    if root_dir and src_dir:
        return (PurePosixPath(root_dir) / src_dir).as_posix()
    if root_dir:
        return PurePosixPath(root_dir).as_posix()
    if src_dir:
        return PurePosixPath(src_dir).as_posix()
    return ""

# ----------------------------
# 1) Discover + dedupe (separate function)
# ----------------------------
# discover files matching include/exclude patterns on an SFTP server.
def sftp_discover_files(
    factory: SFTPConnectionFactory,
    file_specs: List[Dict[str, Any]],
    connection_root_dir: str = "",
) -> List[Tuple[SftpFile, Dict[str, Any], str]]:
    """
    Discover + dedupe remote files to download.

    Returns:
      List of (SftpFile, spec, remote_root) where spec contains remote/local mapping settings.
    """
    # Track remote path -> (file, spec, remote_root) to dedupe across specs.
    discovered_map: Dict[str, Tuple[SftpFile, Dict[str, Any], str]] = {}

    # Use a short-lived connection for discovery.
    sftp = factory.create()
    try:
        for index, spec in enumerate(file_specs):
            src_dir = _normalize_remote_dir(
                spec.get("src_dir", ""),
                f"requests[{index}].src_dir",
                allow_absolute=False,
            )
            remote_root = _join_remote_dir(connection_root_dir, src_dir)
            discovery_root = remote_root or "."
            include = spec.get("include") or ["*"]
            recursive = bool(spec.get("recursive", False))
            exclude = spec.get("exclude")
            updated_after = parse_datetime(spec.get("updated_after"), "updated_after")
            updated_before = parse_datetime(spec.get("updated_before"), "updated_before")
            logger.info(
                "Discovering SFTP files: root_dir=%s src_dir=%s effective_src_dir=%s include=%s recursive=%s exclude=%s updated_after=%s updated_before=%s",
                connection_root_dir,
                src_dir,
                remote_root,
                include,
                recursive,
                exclude,
                updated_after,
                updated_before,
            )

            # Enumerate and dedupe matching files for the spec.
            for f in sftp_list_files(
                sftp,
                root=discovery_root,
                recursive=recursive,
                include=include,
                exclude=exclude,
                updated_after=updated_after,
                updated_before=updated_before,
            ):
                # dedupe by remote path
                if f.path not in discovered_map:
                    discovered_map[f.path] = (f, spec, remote_root)
                    logger.debug("Discovered file %s (size=%s)", f.path, f.size)
    finally:
        # Always close the discovery connection.
        sftp.close()

    discovered = list(discovered_map.values())
    logger.info("Discovered %s unique files across %s file specs", len(discovered), len(file_specs))
    return discovered


# ----------------------------
# Shared helpers
# ----------------------------
# build a local destination path for a remote SFTP file.
def _build_local_path(remote_path: str, remote_root: str, inbox: Path, dst_dir: str = "") -> Path:
    remote_posix = PurePosixPath(remote_path)
    filename = remote_posix.name
    if not filename:
        raise ValueError(f"remote_path does not look like a file path: {remote_path!r}")

    if remote_root:
        source_root = PurePosixPath(remote_root)
        try:
            relative_path = remote_posix.relative_to(source_root)
        except ValueError as exc:
            raise RuntimeError(
                f"Remote path {remote_path!r} is not under configured source root {remote_root!r}"
            ) from exc
    else:
        source_root = PurePosixPath(".")
        relative_path = remote_posix if not remote_posix.is_absolute() else PurePosixPath(*remote_posix.parts[1:])

    local_root = resolve_inbox_path(inbox, dst_dir, field_name="dst_dir", default=inbox)
    local_path = local_root / Path(*relative_path.parts)
    logger.info(
        "Resolved SFTP local path relative to %s with dst_dir=%s: %s -> %s",
        source_root,
        dst_dir,
        remote_path,
        local_path,
    )
    return local_path


# validate downloaded file size and existence on local storage.
def _ensure_local_download(local_path: Path, expected_size: Optional[int], remote_path: str) -> None:
    if not local_path.exists():
        raise RuntimeError(f"Local file missing after download for {remote_path}: {local_path}")
    if not local_path.is_file():
        raise RuntimeError(f"Local path is not a file after download for {remote_path}: {local_path}")
    if expected_size is not None and expected_size > 0:
        local_size = local_path.stat().st_size
        if local_size <= 0:
            raise RuntimeError(
                f"Local file size is zero after download for {remote_path}: {local_path}"
            )


# ----------------------------
# 2) Worker thread (one connection per thread)
# ----------------------------
# download a single remote file to the local inbox and return metadata.
def sftp_download_worker(
    factory: SFTPConnectionFactory,
    inbox: Path,
    q: "queue.Queue[Optional[Tuple[SftpFile, Dict[str, Any], str]]]",
    downloaded: List[Dict[str, Any]],
    downloaded_lock: threading.Lock,
    errors: List[Dict[str, Any]],
    errors_lock: threading.Lock,
    retries: int,
    retry_interval_sec: int,
) -> None:
    """
    Worker thread:
      - open ONE SFTP connection
      - download until sentinel None
      - close connection when done
    """
    # Each worker lazily opens one SFTP connection after claiming work so
    # connection failures still decrement the queue and cannot deadlock q.join().
    sftp = None
    try:
        while True:
            item = q.get()
            try:
                if item is None:
                    # Sentinel indicates this worker should exit.
                    return

                f, spec, remote_root = item
                download_start = time.perf_counter()
                delete_source_file = bool(spec.get("delete_source_file", False))

                if sftp is None:
                    logger.info("Opening worker SFTP connection before processing %s", f.path)
                    sftp = factory.create()

                remote_path = f.path  # full path on SFTP server
                local_path = _build_local_path(remote_path, remote_root, inbox, spec.get("dst_dir", ""))

                # Ensure local parent directories exist.
                local_path.parent.mkdir(parents=True, exist_ok=True)

                # Download with retry support.
                for attempt in range(retries + 1):
                    try:
                        logger.info("Downloading %s -> %s (attempt %s)", remote_path, local_path, attempt + 1)
                        sftp.get(remote_path, str(local_path))
                        break
                    except Exception:
                        if attempt >= retries:
                            logger.exception("Failed downloading %s after %s attempts", remote_path, attempt + 1)
                            raise
                        time.sleep(retry_interval_sec)

                download_duration = round(time.perf_counter() - download_start, 3)

                # Record the successful download metadata.
                rec = {
                    "remote_path": remote_path,
                    "local_path": str(local_path),
                    "size": f.size,
                    "mtime": f.mtime,
                    "duration_sec": download_duration,
                }
                with downloaded_lock:
                    downloaded.append(rec)
                logger.info("Downloaded %s (size=%s) to %s", remote_path, f.size, local_path)

                if delete_source_file:
                    logger.info("Delete requested for remote file after download: %s", remote_path)
                    _ensure_local_download(local_path, f.size, remote_path)
                    try:
                        logger.info("Deleting remote file: %s", remote_path)
                        sftp.remove(remote_path)
                        logger.info("Deleted remote file: %s", remote_path)
                    except Exception as exc:
                        logger.exception("Failed to delete remote file %s", remote_path)
                        raise RuntimeError(f"Failed to delete remote file {remote_path}") from exc

            except Exception as e:
                # Capture errors with the remote path for reporting.
                remote_path = item[0].path if item is not None else None
                download_duration = round(time.perf_counter() - download_start, 3) if item is not None else None
                with errors_lock:
                    errors.append(
                        {
                            "remote_path": remote_path,
                            "error": str(e),
                            "duration_sec": download_duration,
                        }
                    )
                logger.error("Error processing %s: %s", remote_path, e)

            finally:
                # Signal completion for the current queue item.
                q.task_done()

    finally:
        # Ensure the worker connection is closed.
        if sftp is not None:
            sftp.close()


# ----------------------------
# 3) Download handler
# ----------------------------
# download files from SFTP into the inbox and return a TaskResult.
def download(ctx) -> TaskResult:
    """
    Download task handler.

    - Builds connection factory from params["connection"]
    - Discovers + dedupes remote files
    - Downloads using N worker threads (one SFTP connection per thread)
    """
    # Parse task parameters and validate file specs.
    task_param = ctx.task_params or {}
    logger.info("task param: %s", task_param)

    param_conn = task_param.get("connection") or {}
    file_specs = task_param.get("requests") or []
    logger.info(
        "SFTP download handler starting task_id=%s request_count=%s",
        getattr(ctx, "task_id", None),
        len(file_specs),
    )

    # Resolve auth strategy (key vs basic).
    auth = param_conn.get("auth") or {}
    auth_type = (auth.get("type") or "basic").lower()
    if auth_type == "key":
        username = auth.get("username")
        # Some servers require both a private key and a user password (2-step auth).
        # Keep password optional for key-based auth and pass it through when present.
        password = auth.get("password")
        pkey_b64 = auth.get("private_key")
        passphrase = auth.get("private_key_passphrase")
    else:
        username = auth.get("username")
        password = auth.get("password")
        pkey_b64 = None
        passphrase = None

    # Configure host verification options.
    host_verification = param_conn.get("host_verification") or {}
    verification_mode = (host_verification.get("mode") or "").lower()
    if verification_mode == "known_hosts":
        known_hosts = _normalize_known_hosts(host_verification.get("known_hosts"))
        allow_auto_add_host_key = False
        fingerprints = None
    elif verification_mode == "fingerprints":
        known_hosts = None
        allow_auto_add_host_key = False
        fingerprints = _normalize_fingerprint_values(host_verification.get("fingerprints"))
    elif verification_mode == "allow_auto_add_host_key":
        known_hosts = None
        allow_auto_add_host_key = True
        fingerprints = None
    else:
        known_hosts = None
        allow_auto_add_host_key = True
        fingerprints = None

    logger.info(
        "SFTP connection config hostname=%s port=%s auth_type=%s verification_mode=%s max_connections=%s",
        param_conn.get("hostname"),
        param_conn.get("port", 22),
        auth_type,
        verification_mode or "allow_auto_add_host_key",
        param_conn.get("max_connections", 1),
    )
    connection_root_dir = _normalize_remote_dir(param_conn.get("root_dir", ""), "connection.root_dir", allow_absolute=True)
    logger.debug(
        "SFTP auth details username=%s password_present=%s private_key_present=%s private_key_passphrase_present=%s root_dir=%s",
        username,
        bool(password),
        bool(pkey_b64),
        bool(passphrase),
        connection_root_dir,
    )
    logger.debug(
        "SFTP host verification details known_hosts_present=%s fingerprint_count=%s auto_add=%s keepalive_sec=%s disabled_algorithms=%s",
        bool(known_hosts),
        len(fingerprints or []),
        allow_auto_add_host_key,
        param_conn.get("keepalive_sec", 30),
        param_conn.get("disabled_algorithms"),
    )

    # Build the connection factory used by discovery and workers.
    factory = SFTPConnectionFactory(
        hostname=param_conn["hostname"],
        port=int(param_conn.get("port", 22)),
        username=username,
        password=password,
        pkey_b64=pkey_b64,
        passphrase=passphrase,
        keepalive_sec=int(param_conn.get("keepalive_sec", 30)),
        disabled_algorithms=param_conn.get("disabled_algorithms"),
        allow_auto_add_host_key=allow_auto_add_host_key,
        known_hosts=known_hosts,
        fingerprints=fingerprints,
    )
    
    # Prepare the inbox directory for downloads.
    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")
    inbox = Path(ctx.inbox)
    inbox.mkdir(parents=True, exist_ok=True)

    start_time = time.time()

    # Discover + dedupe
    items = sftp_discover_files(factory, file_specs, connection_root_dir=connection_root_dir)
    logger.info("SFTP discovery completed: %s files queued for download", len(items))
    logger.debug(
        "SFTP discovery results: %s",
        [
            {"remote_path": f.path, "size": f.size, "mtime": f.mtime, "atime": f.atime}
            for (f, _, _) in items
        ],
    )
    if not items:
        logger.info("SFTP discovery found no files to download")

    matched_records = []
    for f, spec, remote_root in items:
        local_path = _build_local_path(f.path, remote_root, inbox, spec.get("dst_dir", ""))
        matched_records.append(
            {
                "src": {"type": "file", "path": f.path, "size": f.size},
                "dest": {"type": "file", "path": str(local_path), "size": f.size},
                "duration_sec": 0.0,
            }
        )

    # Thread pool (one connection per thread)
    max_conn = int(param_conn.get("max_connections", 1) or 1)
    worker_count = max(1, min(max_conn, len(items)))
    logger.info("Starting SFTP downloads with %s worker(s)", worker_count)

    # Load retry settings for transient failures.
    fault_tolerant = param_conn.get("fault_tolerant") or {}
    retries = int(fault_tolerant.get("retries", 0) or 0)
    retry_interval_sec = int(fault_tolerant.get("retry_interval_sec", 0) or 0)
    logger.info(
        "Fault tolerance: retries=%s, retry_interval_sec=%s, delete_after=%s",
        retries,
        retry_interval_sec,
        any(spec.get("delete_source_file", False) for spec in file_specs),
    )

    # Populate queue with work and sentinel values.
    q: "queue.Queue[Optional[Tuple[SftpFile, Dict[str, Any], str]]]" = queue.Queue()
    for item in items:
        q.put(item)
    for _ in range(worker_count):
        q.put(None)

    # Shared result lists guarded by locks.
    downloaded: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    downloaded_lock = threading.Lock()
    errors_lock = threading.Lock()

    # Spawn worker threads.
    threads = [
        threading.Thread(
            target=sftp_download_worker,
            args=(
                factory,
                inbox,
                q,
                downloaded,
                downloaded_lock,
                errors,
                errors_lock,
                retries,
                retry_interval_sec,
            ),
            daemon=True,
        )
        for _ in range(worker_count)
    ]

    for t in threads:
        t.start()

    # 3) Wait for all work, then join threads
    q.join()
    for t in threads:
        t.join()

    if errors:
        logger.error("Completed downloads with %s error(s)", len(errors))
        logger.error("SFTP download errors: %s", errors)
        first_error = errors[0]
        raise RuntimeError(
            "SFTP download failed: "
            f"{len(errors)} error(s), first error for {first_error.get('remote_path')}: "
            f"{first_error.get('error')}"
        )

    logger.info("Completed downloads successfully: %s file(s) downloaded", len(downloaded))
    logger.debug("SFTP downloaded records: %s", downloaded)

    duration_by_source = {
        rec["remote_path"]: {
            "duration_sec": rec.get("duration_sec"),
            "size": rec.get("size"),
        }
        for rec in downloaded
    }
    outputs = []
    for record in matched_records:
        source = record["src"]["path"]
        if source in duration_by_source:
            record["duration_sec"] = duration_by_source[source].get("duration_sec") or 0.0
            downloaded_size = duration_by_source[source].get("size")
            if downloaded_size is not None:
                record["src"]["size"] = downloaded_size
                record["dest"]["size"] = downloaded_size
        outputs.append(record)

    return TaskResult(
        task_id=ctx.task_id or "",
        handler="oedis.handler.sftp.download",
        status="success",
        outputs=outputs,
        metrics={"total_items": len(outputs), "total_duration_sec": round(time.time() - start_time, 3)},
    )
