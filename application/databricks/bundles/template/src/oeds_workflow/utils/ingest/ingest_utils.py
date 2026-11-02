"""Utilities for packaged ingestion resources."""

from __future__ import annotations

import logging
from importlib.resources import files

import oeds.core.file_utils as fileutils

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_FILE = "default.json"
_DEFAULT_CONFIG_PACKAGE = "oeds_workflow.config.ingest"


def load_config(filename: str = _DEFAULT_CONFIG_FILE) -> str:
    """Load one packaged ingestion config file as a UTF-8 string."""
    if not filename or not str(filename).strip():
        raise ValueError("filename is required")

    config_name = str(filename).strip()
    package_name = _DEFAULT_CONFIG_PACKAGE
    logger.info("Loading ingestion config '%s' from package '%s'", config_name, package_name)

    config_text = files(package_name).joinpath(config_name).read_text(encoding="utf-8")
    logger.debug(
        "Loaded ingestion config '%s' from package '%s' with %s characters",
        config_name,
        package_name,
        len(config_text),
    )
    return config_text


def find_files(path: str, pattern: str = "*", recursive: bool = False) -> list[str]:
    """Return matched file paths under one root directory."""
    if not path or not str(path).strip():
        raise ValueError("path is required")

    root_path = str(path).strip()
    file_pattern = str(pattern or "*").strip() or "*"
    logger.info(
        "Finding files under '%s' with pattern '%s' recursive=%s",
        root_path,
        file_pattern,
        recursive,
    )

    matches = fileutils.ls(root_path, pattern=file_pattern, recursive=recursive, item_type="files")
    matched_paths = [item.path for item in matches]
    logger.debug("Matched %s files under '%s': %s", len(matched_paths), root_path, matched_paths)
    return matched_paths



def count_files(path: str, pattern: str = "*", recursive: bool = False) -> int:
    """Count matched file paths under one root directory."""
    matched_paths = find_files(path, pattern=pattern, recursive=recursive)
    count = len(matched_paths)
    logger.info(
        "Counted %s files under '%s' with pattern '%s' recursive=%s",
        count,
        str(path).strip(),
        str(pattern or "*").strip() or "*",
        recursive,
    )
    return count



def has_files(path: str, pattern: str = "*", recursive: bool = False) -> bool:
    """Return True when at least one matched file exists under the root directory."""
    count = count_files(path, pattern=pattern, recursive=recursive)
    has_match = count > 0
    logger.info(
        "File existence check under '%s' with pattern '%s' recursive=%s result=%s",
        str(path).strip(),
        str(pattern or "*").strip() or "*",
        recursive,
        has_match,
    )
    return has_match
