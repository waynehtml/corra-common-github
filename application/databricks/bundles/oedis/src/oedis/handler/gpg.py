from __future__ import annotations

import base64
import logging
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import pgpy

from oedis.handler.lib.utils import find_files, resolve_inbox_path
from oedis.task_result import TaskResult


logger = logging.getLogger(__name__)

SUPPORTED_ENCRYPTED_SUFFIXES = {".gpg", ".pgp", ".asc"}


class DecryptError(RuntimeError):
    """Raised when OpenPGP decryption fails."""


def _b64_to_bytes(b64_text: str) -> bytes:
    """Decode base64 text into raw bytes."""
    try:
        return base64.b64decode(b64_text, validate=True)
    except Exception as exc:
        raise DecryptError(f"Invalid base64 private_key: {exc}") from exc


def _plain_path(cipher_path: Path) -> Path:
    """Return the plaintext path for an encrypted file path."""
    if cipher_path.suffix.lower() in SUPPORTED_ENCRYPTED_SUFFIXES:
        return cipher_path.with_suffix("")
    return cipher_path


def _ensure_parent_dir(path: Path) -> None:
    """Create parent directory for a file path if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)


def _normalize_patterns(value: Any, default: list[str]) -> list[str]:
    """Normalize include/exclude config to a list of strings."""
    if value in (None, ""):
        return list(default)
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _resolve_dst_root(inbox: Path, dst_dir: Optional[str]) -> Optional[Path]:
    """Resolve dst_dir under inbox, or return None to use source directory."""
    if dst_dir in (None, ""):
        logger.debug("No dst_dir configured; plaintext files will be written beside source files.")
        return None

    resolved = resolve_inbox_path(inbox, dst_dir, field_name="dst_dir")
    logger.debug("Resolved dst_dir=%s under inbox=%s to %s", dst_dir, inbox, resolved)
    return resolved


def decrypt_file_gnupg(
    cipher_file: str,
    plain_file: str,
    *,
    private_key_b64: str,
    passphrase: Optional[str] = None,
    gpg_binary: str = "gpg",
) -> str:
    """
    Decrypt an OpenPGP file using python-gnupg and system gpg.
    """
    try:
        import gnupg
    except Exception as exc:
        raise DecryptError(f"python-gnupg not available: {exc}") from exc

    cipher_path = Path(cipher_file)
    plain_path = Path(plain_file)
    _ensure_parent_dir(plain_path)

    if not cipher_path.is_file():
        raise DecryptError(f"cipher_file not found: {cipher_path}")

    key_bytes = _b64_to_bytes(private_key_b64)

    try:
        key_text = key_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DecryptError(
            "private_key_b64 decoded to non-UTF8 bytes. "
            "For python-gnupg, store base64 of the ASCII-armored key text "
            "(-----BEGIN PGP PRIVATE KEY BLOCK----- ...)."
        ) from exc

    logger.debug("Trying gnupg backend for file %s", cipher_path)

    with tempfile.TemporaryDirectory(prefix="gnupg_home_") as gnupg_home:
        logger.debug("Created temporary gnupg home %s for %s", gnupg_home, cipher_path)
        gpg = gnupg.GPG(
            gnupghome=gnupg_home,
            gpgbinary=gpg_binary,
            use_agent=False,
            options=["--pinentry-mode", "loopback"],
        )

        imported = gpg.import_keys(key_text)
        logger.debug(
            "Imported private key(s) for %s with result count=%s fingerprints=%s",
            cipher_path,
            getattr(imported, "count", None),
            getattr(imported, "fingerprints", None),
        )
        if getattr(imported, "count", 0) <= 0:
            stderr = getattr(imported, "stderr", "") or ""
            raise DecryptError(f"Failed to import private key. stderr={stderr!r}")

        with cipher_path.open("rb") as f_in:
            result = gpg.decrypt_file(f_in, passphrase=passphrase, output=str(plain_path))
            logger.debug(
                "gnupg decrypt result for %s: ok=%s status=%r stderr=%r",
                cipher_path,
                getattr(result, "ok", None),
                getattr(result, "status", None),
                getattr(result, "stderr", None),
            )

        if not getattr(result, "ok", False):
            raise DecryptError(
                f"gpg decrypt failed: status={getattr(result, 'status', None)!r} "
                f"stderr={getattr(result, 'stderr', None)!r}"
            )

    return str(plain_path)


def decrypt_file_pgpy(
    cipher_file: str,
    plain_file: str,
    *,
    private_key_b64: str,
    passphrase: Optional[str] = None,
) -> str:
    """
    Decrypt an OpenPGP file using PGPy.
    """
    cipher_path = Path(cipher_file)
    plain_path = Path(plain_file)
    _ensure_parent_dir(plain_path)

    if not cipher_path.is_file():
        raise DecryptError(f"cipher_file not found: {cipher_path}")

    key_bytes = _b64_to_bytes(private_key_b64)

    logger.debug("Trying PGPy backend for file %s", cipher_path)

    try:
        pgp_key, _ = pgpy.PGPKey.from_blob(key_bytes)
    except Exception as exc:
        raise DecryptError(f"Failed to parse PGP private key: {exc}") from exc

    logger.debug(
        "Loaded PGP private key for %s: protected=%s",
        cipher_path,
        pgp_key.is_protected,
    )

    try:
        pgp_msg = pgpy.PGPMessage.from_blob(cipher_path.read_bytes())
    except Exception as exc:
        raise DecryptError(f"Input is not valid OpenPGP data: {exc}") from exc

    if not pgp_msg.is_encrypted:
        raise DecryptError("Input OpenPGP message is not encrypted.")

    logger.debug("Parsed encrypted PGP message for %s", cipher_path)

    try:
        if pgp_key.is_protected:
            if passphrase is None:
                raise DecryptError("Private key is protected but passphrase is None.")
            with pgp_key.unlock(passphrase):
                decrypted_msg = pgp_key.decrypt(pgp_msg)
        else:
            decrypted_msg = pgp_key.decrypt(pgp_msg)
    except DecryptError:
        raise
    except Exception as exc:
        raise DecryptError(f"PGPy decrypt failed: {exc}") from exc

    plaintext = decrypted_msg.message
    if isinstance(plaintext, (bytes, bytearray)):
        plaintext_bytes = bytes(plaintext)
    else:
        plaintext_bytes = str(plaintext).encode("utf-8")

    plain_path.write_bytes(plaintext_bytes)
    return str(plain_path)


def decrypt_file(
    cipher_file: str,
    plain_file: str,
    *,
    private_key_b64: str,
    passphrase: Optional[str] = None,
    gpg_binary: str = "gpg",
) -> str:
    """
    Try gnupg first, then fall back to PGPy.
    """
    gnupg_error: Optional[Exception] = None

    try:
        result = decrypt_file_gnupg(
            cipher_file,
            plain_file,
            private_key_b64=private_key_b64,
            passphrase=passphrase,
            gpg_binary=gpg_binary,
        )
        logger.debug("Decryption succeeded with gnupg: %s", cipher_file)
        return result
    except Exception as exc:
        gnupg_error = exc
        logger.warning("gnupg decryption failed for %s: %s", cipher_file, exc)

    try:
        result = decrypt_file_pgpy(
            cipher_file,
            plain_file,
            private_key_b64=private_key_b64,
            passphrase=passphrase,
        )
        logger.debug("Decryption succeeded with PGPy: %s", cipher_file)
        return result
    except Exception as exc:
        logger.error(
            "Decryption failed with both backends for %s. gnupg_error=%r pgpy_error=%r",
            cipher_file,
            gnupg_error,
            exc,
        )
        raise DecryptError(
            f"Decryption failed with both backends.\n"
            f"- gnupg error: {gnupg_error!r}\n"
            f"- pgpy error:  {exc!r}"
        ) from exc


def decrypt(ctx) -> TaskResult:
    """
    Decrypt task handler.

    Expected ctx.task_params example:
      {
        "credential": {
          "private_key": "<base64 private key>",
          "passphrase": null | "<string>"
        },
        "requests": [
          {
            "src_dir": "unit_data",
            "include": ["*test.csv.gpg"],
            "exclude": ["*_test.csv.gpg"],
            "recursive": false,
            "delete_source_file": true
          }
        ]
      }
    """
    handler_start = time.perf_counter()

    task_params = getattr(ctx, "task_params", None) or {}
    specs = task_params.get("requests") or []

    credential = task_params.get("credential") or {}
    private_key_b64 = credential.get("private_key")
    passphrase = credential.get("passphrase")

    if not private_key_b64:
        raise DecryptError("Missing credential.private_key.")

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")

    inbox = Path(ctx.inbox)
    inbox.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Starting decrypt handler: task_id=%s inbox=%s specs=%s",
        getattr(ctx, "task_id", ""),
        inbox,
        len(specs),
    )
    logger.debug(
        "Decrypt handler credential summary: private_key_present=%s passphrase_present=%s",
        bool(private_key_b64),
        passphrase is not None,
    )

    decrypted: list[dict[str, Any]] = []

    for index, spec in enumerate(specs):
        spec_decrypted_start = len(decrypted)
        src_dir_value = spec.get("src_dir", "")
        base_dir = resolve_inbox_path(inbox, src_dir_value, field_name="src_dir")

        include = _normalize_patterns(spec.get("include"), ["*"])
        exclude = _normalize_patterns(spec.get("exclude"), [])
        recursive = bool(spec.get("recursive", False))
        delete_src = bool(spec.get("delete_source_file", False))
        dst_root = _resolve_dst_root(inbox, spec.get("dst_dir"))

        logger.info(
            "Processing spec #%s: base_dir=%s include=%s exclude=%s recursive=%s "
            "delete_source_file=%s dst_dir=%s",
            index,
            base_dir,
            include,
            exclude,
            recursive,
            delete_src,
            dst_root if dst_root else "(same directory as source)",
        )
        logger.debug("Spec #%s raw config: %s", index, spec)

        cipher_paths = find_files(
            base_dir,
            include=include,
            exclude=exclude,
            recursive=recursive,
        )

        logger.debug("Spec #%s matched %s files", index, len(cipher_paths))
        if cipher_paths:
            logger.debug(
                "Spec #%s matched paths: %s",
                index,
                [str(path) for path in cipher_paths],
            )
        else:
            logger.info("Spec #%s matched no files under %s", index, base_dir)

        for cipher_path in cipher_paths:
            if cipher_path.suffix.lower() not in SUPPORTED_ENCRYPTED_SUFFIXES:
                logger.debug("Skipping non-encrypted file: %s", cipher_path)
                continue

            if dst_root:
                relative_cipher_path = cipher_path.relative_to(base_dir)
                plain_path = _plain_path(dst_root / relative_cipher_path)
                logger.debug(
                    "Spec #%s preserving relative path %s under dst_root %s",
                    index,
                    relative_cipher_path,
                    dst_root,
                )
            else:
                plain_path = _plain_path(cipher_path)

            _ensure_parent_dir(plain_path)

            logger.info("Decrypting %s -> %s", cipher_path, plain_path)
            decrypt_start = time.perf_counter()

            decrypt_file(
                str(cipher_path),
                str(plain_path),
                private_key_b64=private_key_b64,
                passphrase=passphrase,
            )

            decrypt_duration = round(time.perf_counter() - decrypt_start, 6)
            logger.info(
                "Decrypted %s -> %s in %.6fs",
                cipher_path,
                plain_path,
                decrypt_duration,
            )

            if delete_src:
                cipher_path.unlink()
                logger.info("Deleted source cipher file %s", cipher_path)

            decrypted.append(
                {
                    "cipher_file": str(cipher_path),
                    "plain_file": str(plain_path),
                    "duration_sec": decrypt_duration,
                }
            )

        logger.info(
            "Finished spec #%s: decrypted_items=%s",
            index,
            len(decrypted) - spec_decrypted_start,
        )

    outputs = [
        {
            "src": {"type": "file", "path": entry["cipher_file"]},
            "dest": {"type": "file", "path": entry["plain_file"]},
            "duration_sec": entry["duration_sec"],
        }
        for entry in decrypted
    ]

    total_duration_sec = round(time.perf_counter() - handler_start, 3)

    logger.info(
        "Completed decrypt handler: task_id=%s total_items=%s total_duration_sec=%s",
        getattr(ctx, "task_id", ""),
        len(outputs),
        total_duration_sec,
    )

    return TaskResult(
        task_id=ctx.task_id or "",
        handler="oedis.handler.gpg.decrypt",
        status="success",
        outputs=outputs,
        metrics={
            "total_items": len(outputs),
            "total_duration_sec": total_duration_sec,
        },
    )
