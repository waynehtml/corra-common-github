from __future__ import annotations

import logging
import shutil
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from oedis.handler.lib.utils import find_files, resolve_inbox_path
from oedis.task_result import TaskResult

logger = logging.getLogger(__name__)


class ArchiveError(RuntimeError):
    """Base archive error."""


class ExtractError(ArchiveError):
    """Raised when archive extraction fails."""


class CompressError(ArchiveError):
    """Raised when archive compression fails."""


SUPPORTED_FORMATS = {"zip", "tar", "gztar", "7z"}


def _normalize_format(path: Path, fmt: str, *, for_extract: bool) -> str:
    value = str(fmt or "auto").strip().lower()
    if value and value != "auto":
        if value == "tgz":
            value = "gztar"
        if value not in SUPPORTED_FORMATS:
            error_cls = ExtractError if for_extract else CompressError
            raise error_cls(f"Unsupported archive format: {fmt}")
        return value

    name = path.name.lower()
    if name.endswith(".7z"):
        return "7z"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "gztar"
    if name.endswith(".tar"):
        return "tar"

    error_cls = ExtractError if for_extract else CompressError
    raise error_cls(f"Unable to infer archive format from extension: {path}")


def _archive_extension(fmt: str) -> str:
    if fmt == "7z":
        return ".7z"
    if fmt == "zip":
        return ".zip"
    if fmt == "tar":
        return ".tar"
    if fmt == "gztar":
        return ".tar.gz"
    raise CompressError(f"Unsupported archive format: {fmt}")


def _ensure_archive_name(
    fmt: str,
    base_dir: Path,
    source_paths: List[Path],
) -> str:
    ext = _archive_extension(fmt)
    if len(source_paths) == 1:
        return f"{source_paths[0].name}{ext}"
    if base_dir.name:
        return f"{base_dir.name}{ext}"
    return f"archive{ext}"


def _validate_archive_member_path(name: str) -> Path:
    member = Path(name)

    if member.is_absolute():
        raise ExtractError(f"Archive member has absolute path: {name}")

    clean_parts: List[str] = []
    for part in member.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ExtractError(f"Archive member contains parent traversal: {name}")
        clean_parts.append(part)

    if not clean_parts:
        raise ExtractError(f"Archive member resolves to empty path: {name}")

    return Path(*clean_parts)


def _resolve_extract_target(rel_path: Path, dest_root: Path) -> Path:
    return dest_root / rel_path


def _move_extracted(temp_root: Path, dest_root: Path) -> List[Path]:
    dest_root.mkdir(parents=True, exist_ok=True)

    extracted: List[Path] = []

    for file_path in sorted(temp_root.rglob("*"), key=lambda p: str(p)):
        if not file_path.is_file():
            continue

        rel = file_path.relative_to(temp_root)
        target = _resolve_extract_target(rel, dest_root)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            target.unlink()

        shutil.move(str(file_path), str(target))
        extracted.append(target.resolve())

    return extracted


def _extract_zip(
    archive_path: Path,
    dest_root: Path,
    *,
    passphrase: Optional[str],
) -> List[Path]:
    pwd = passphrase.encode() if passphrase else None

    with tempfile.TemporaryDirectory(prefix="extract_zip_") as tmp:
        tmp_root = Path(tmp)

        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue

                is_encrypted = bool(info.flag_bits & 0x1)
                if is_encrypted and not pwd:
                    raise ExtractError(f"Zip archive requires passphrase: {archive_path}")

                rel = _validate_archive_member_path(info.filename)
                target = tmp_root / rel
                target.parent.mkdir(parents=True, exist_ok=True)

                try:
                    with zf.open(info, pwd=pwd) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                except RuntimeError as e:
                    message = str(e).lower()
                    if "password required" in message or "bad password" in message:
                        raise ExtractError(f"Invalid or missing zip passphrase: {archive_path}") from e
                    raise ExtractError(f"Failed to extract zip member {info.filename}: {archive_path}") from e

        return _move_extracted(tmp_root, dest_root)


def _extract_tar(
    archive_path: Path,
    dest_root: Path,
    *,
    fmt: str,
) -> List[Path]:
    mode = "r:gz" if fmt == "gztar" else "r"

    with tempfile.TemporaryDirectory(prefix="extract_tar_") as tmp:
        tmp_root = Path(tmp)

        with tarfile.open(archive_path, mode) as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue

                rel = _validate_archive_member_path(member.name)
                target = tmp_root / rel
                target.parent.mkdir(parents=True, exist_ok=True)

                src = tf.extractfile(member)
                if src is None:
                    continue

                with src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

        return _move_extracted(tmp_root, dest_root)


def _extract_7z(
    archive_path: Path,
    dest_root: Path,
    *,
    passphrase: Optional[str],
) -> List[Path]:
    try:
        import py7zr  # type: ignore
    except Exception as e:
        raise ExtractError(
            "py7zr is required for .7z archives; install via wheel deps or cluster library."
        ) from e

    with tempfile.TemporaryDirectory(prefix="extract_7z_") as tmp:
        tmp_root = Path(tmp)

        try:
            with py7zr.SevenZipFile(archive_path, mode="r", password=passphrase or None) as archive:
                archive.extractall(path=tmp_root)
        except Exception as e:
            raise ExtractError(f"Failed to extract 7z archive: {archive_path}") from e

        return _move_extracted(tmp_root, dest_root)


def _compress_zip(
    archive_path: Path,
    files: Iterable[Path],
    *,
    base_dir: Path,
    passphrase: Optional[str],
) -> None:
    if passphrase:
        try:
            import pyzipper  # type: ignore
        except Exception as e:
            raise CompressError(
                "pyzipper is required to write encrypted zip archives; install via wheel deps or cluster library."
            ) from e

        with pyzipper.AESZipFile(
            archive_path,
            "w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as zf:
            zf.setpassword(passphrase.encode())
            for file_path in files:
                zf.write(file_path, arcname=file_path.relative_to(base_dir).as_posix())
        return

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in files:
            zf.write(file_path, arcname=file_path.relative_to(base_dir).as_posix())


def _compress_tar(
    archive_path: Path,
    files: Iterable[Path],
    *,
    base_dir: Path,
    fmt: str,
) -> None:
    mode = "w:gz" if fmt == "gztar" else "w"
    with tarfile.open(archive_path, mode) as tf:
        for file_path in files:
            tf.add(file_path, arcname=file_path.relative_to(base_dir).as_posix())


def _compress_7z(
    archive_path: Path,
    files: Iterable[Path],
    *,
    base_dir: Path,
    passphrase: Optional[str],
) -> None:
    try:
        import py7zr  # type: ignore
    except Exception as e:
        raise CompressError(
            "py7zr is required for .7z archives; install via wheel deps or cluster library."
        ) from e

    with py7zr.SevenZipFile(archive_path, mode="w", password=passphrase or None) as archive:
        for file_path in files:
            archive.write(file_path, arcname=file_path.relative_to(base_dir).as_posix())


def extract(ctx) -> TaskResult:
    handler_start = time.perf_counter()
    task_param = getattr(ctx, "task_params", None) or {}
    specs = task_param.get("requests") or []
    credential = task_param.get("credential") or {}
    task_passphrase = credential.get("passphrase") or None

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")

    inbox = Path(ctx.inbox)
    inbox.mkdir(parents=True, exist_ok=True)

    logger.info("Starting archive extract handler task_id=%s spec_count=%s inbox=%s", ctx.task_id, len(specs), inbox)
    outputs: List[Dict[str, Any]] = []

    for index, spec in enumerate(specs):
        base_dir = resolve_inbox_path(inbox, spec.get("src_dir"), field_name="src_dir")
        include = spec.get("include") or ["*"]
        exclude = spec.get("exclude")
        fmt = str(spec.get("format") or "auto")
        recursive = bool(spec.get("recursive", False))
        delete_src = bool(spec.get("delete_source_file", False))
        dst_dir = str(spec.get("dst_dir") or "")
        passphrase = spec.get("passphrase") or task_passphrase

        logger.info(
            "Extracting archives for spec[%s] from %s include=%s exclude=%s recursive=%s format=%s delete_source_file=%s dst_dir=%s",
            index,
            base_dir,
            include,
            exclude,
            recursive,
            fmt,
            delete_src,
            dst_dir or "<archive-parent>",
        )
        logger.debug(
            "Resolved extract spec[%s] base_dir=%s passphrase_provided=%s",
            index,
            base_dir.resolve() if base_dir.exists() else base_dir,
            bool(passphrase),
        )

        archive_paths = find_files(
            base_dir,
            include=include,
            exclude=exclude,
            recursive=recursive,
        )
        logger.info("Found %s archive candidate(s) for spec[%s]", len(archive_paths), index)
        if archive_paths:
            if len(archive_paths) <= 25:
                logger.debug("Archive candidates for spec[%s]: %s", index, [str(path) for path in archive_paths])
            else:
                logger.debug(
                    "Archive candidates for spec[%s] (first 25 of %s): %s",
                    index,
                    len(archive_paths),
                    [str(path) for path in archive_paths[:25]],
                )

        for archive_path in archive_paths:
            archive_format = _normalize_format(archive_path, fmt, for_extract=True)
            dest_root = resolve_inbox_path(
                inbox,
                dst_dir,
                field_name="dst_dir",
                default=archive_path.parent,
            )

            logger.info(
                "Processing archive %s format=%s dest=%s",
                archive_path,
                archive_format,
                dest_root,
            )
            logger.debug(
                "Resolved extract operation archive=%s archive_exists=%s dest_exists=%s passphrase_provided=%s",
                archive_path.resolve(),
                archive_path.exists(),
                dest_root.exists(),
                bool(passphrase),
            )

            extract_start = time.perf_counter()

            if archive_format == "7z":
                extracted_paths = _extract_7z(
                    archive_path,
                    dest_root,
                    passphrase=passphrase,
                )
            elif archive_format == "zip":
                extracted_paths = _extract_zip(
                    archive_path,
                    dest_root,
                    passphrase=passphrase,
                )
            elif archive_format in {"tar", "gztar"}:
                extracted_paths = _extract_tar(
                    archive_path,
                    dest_root,
                    fmt=archive_format,
                )
            else:
                raise ExtractError(f"Unsupported archive format: {archive_format}")

            archive_duration = round(time.perf_counter() - extract_start, 6)
            logger.info(
                "Extracted %s files from %s in %ss",
                len(extracted_paths),
                archive_path,
                archive_duration,
            )
            if extracted_paths:
                if len(extracted_paths) <= 25:
                    logger.debug("Extracted files from %s: %s", archive_path, [str(path) for path in extracted_paths])
                else:
                    logger.debug(
                        "Extracted files from %s (first 25 of %s): %s",
                        archive_path,
                        len(extracted_paths),
                        [str(path) for path in extracted_paths[:25]],
                    )

            for extracted_path in extracted_paths:
                outputs.append(
                    {
                        "src": {"type": "file", "path": str(archive_path.resolve())},
                        "dest": {"type": "file", "path": str(extracted_path)},
                        "duration_sec": archive_duration,
                    }
                )

            if delete_src:
                archive_path.unlink()
                logger.info("Deleted source archive %s", archive_path)

    handler_name = getattr(ctx, "task_handler", None) or "oedis.handler.archive.extract"
    total_duration = round(time.perf_counter() - handler_start, 3)
    logger.info(
        "Completed archive extract handler task_id=%s output_count=%s total_duration_sec=%s",
        ctx.task_id,
        len(outputs),
        total_duration,
    )
    return TaskResult(
        task_id=ctx.task_id or "",
        handler=handler_name,
        status="success",
        outputs=outputs,
        metrics={
            "total_items": len(outputs),
            "total_duration_sec": total_duration,
        },
    )


def compress(ctx) -> TaskResult:
    handler_start = time.perf_counter()
    task_param = getattr(ctx, "task_params", None) or {}
    specs = task_param.get("requests") or []
    credential = task_param.get("credential") or {}
    task_passphrase = credential.get("passphrase") or None

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")

    inbox = Path(ctx.inbox)
    inbox.mkdir(parents=True, exist_ok=True)

    logger.info("Starting archive compress handler task_id=%s spec_count=%s inbox=%s", ctx.task_id, len(specs), inbox)
    outputs: List[Dict[str, Any]] = []

    for index, spec in enumerate(specs):
        base_dir = resolve_inbox_path(inbox, spec.get("src_dir"), field_name="src_dir")
        include = spec.get("include") or ["*"]
        exclude = spec.get("exclude")
        fmt = str(spec.get("format") or "zip")
        recursive = bool(spec.get("recursive", False))
        delete_src = bool(spec.get("delete_source_file", False))
        dst_dir = str(spec.get("dst_dir") or "")
        passphrase = spec.get("passphrase") or task_passphrase

        logger.info(
            "Compressing files for spec[%s] from %s include=%s exclude=%s recursive=%s format=%s delete_source_file=%s dst_dir=%s",
            index,
            base_dir,
            include,
            exclude,
            recursive,
            fmt,
            delete_src,
            dst_dir or "<source-dir>",
        )
        logger.debug(
            "Resolved compress spec[%s] base_dir=%s passphrase_provided=%s",
            index,
            base_dir.resolve() if base_dir.exists() else base_dir,
            bool(passphrase),
        )

        source_paths = find_files(
            base_dir,
            include=include,
            exclude=exclude,
            recursive=recursive,
        )
        logger.info("Found %s source file(s) for spec[%s]", len(source_paths), index)

        if not source_paths:
            logger.info("No files matched for %s with include=%s", base_dir, include)
            continue

        if str(fmt).strip().lower() == "auto":
            archive_format = "zip"
        else:
            archive_format = _normalize_format(Path("archive"), fmt, for_extract=False)
        logger.debug(
            "Resolved archive format for spec[%s]: requested=%s effective=%s",
            index,
            fmt,
            archive_format,
        )

        if passphrase and archive_format not in {"zip", "7z"}:
            logger.warning("Passphrase ignored for archive format %s", archive_format)
            passphrase = None

        output_dir = resolve_inbox_path(
            inbox,
            dst_dir,
            field_name="dst_dir",
            default=base_dir,
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        archive_name = _ensure_archive_name(archive_format, base_dir, source_paths)
        archive_path = output_dir / archive_name
        logger.debug(
            "Resolved archive output for spec[%s] output_dir=%s archive_name=%s archive_exists=%s",
            index,
            output_dir,
            archive_name,
            archive_path.exists(),
        )

        logger.info(
            "Writing archive %s format=%s file_count=%s",
            archive_path,
            archive_format,
            len(source_paths),
        )

        if len(source_paths) <= 25:
            logger.debug("Files to archive: %s", [str(p) for p in source_paths])
        else:
            logger.debug(
                "Files to archive (first 25 of %s): %s",
                len(source_paths),
                [str(p) for p in source_paths[:25]],
            )

        if archive_path.exists():
            logger.debug("Removing existing archive before rewrite: %s", archive_path)
            archive_path.unlink()

        compress_start = time.perf_counter()

        if archive_format == "7z":
            _compress_7z(archive_path, source_paths, base_dir=base_dir, passphrase=passphrase)
        elif archive_format == "zip":
            _compress_zip(archive_path, source_paths, base_dir=base_dir, passphrase=passphrase)
        elif archive_format in {"tar", "gztar"}:
            _compress_tar(archive_path, source_paths, base_dir=base_dir, fmt=archive_format)
        else:
            raise CompressError(f"Unsupported archive format: {archive_format}")

        archive_duration = round(time.perf_counter() - compress_start, 6)
        logger.info(
            "Archived %s files to %s in %ss",
            len(source_paths),
            archive_path,
            archive_duration,
        )

        outputs.append(
            {
                "src": {"type": "dir", "path": str(base_dir.resolve())},
                "dest": {"type": "file", "path": str(archive_path.resolve())},
                "duration_sec": archive_duration,
                "metrics": {"source_file_count": len(source_paths)},
            }
        )

        if delete_src:
            for source_path in source_paths:
                source_path.unlink()
            logger.info("Deleted %s source files after archiving", len(source_paths))

    handler_name = getattr(ctx, "task_handler", None) or "oedis.handler.archive.compress"
    total_duration = round(time.perf_counter() - handler_start, 3)
    logger.info(
        "Completed archive compress handler task_id=%s output_count=%s total_duration_sec=%s",
        ctx.task_id,
        len(outputs),
        total_duration,
    )
    return TaskResult(
        task_id=ctx.task_id or "",
        handler=handler_name,
        status="success",
        outputs=outputs,
        metrics={
            "total_items": len(outputs),
            "total_duration_sec": total_duration,
        },
    )
