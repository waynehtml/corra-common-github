"""Utilities for inspecting and manipulating DBFS or ADLS files."""

from __future__ import annotations

import fnmatch
import json
import posixpath
import logging
from typing import Any, List, Callable, Literal
import re

from databricks.sdk.runtime import dbutils
from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)

def _normalize_dir_path(path: str) -> str:
    return path.rstrip("/") + "/"


def _ensure_dir_exists(path: str) -> str:
    normalized = path.rstrip("/")
    if is_file(normalized):
        raise NotADirectoryError(f"Path is a file; expected directory: {path}")
    if not exists(normalized):
        raise FileNotFoundError(f"Directory not found: {path}")
    return normalized


def is_file(path: str) -> bool:
    """Determine whether a DBFS/ADLS path points to a file.

    Args:
        path: Path to check (for example, ``"dbfs:/mnt/raw/data.csv"``).

    Returns:
        True if the path exists and is a file, otherwise False.
    """
    try:
        # Get file info directly - this will raise exception if path doesn't exist
        info = dbutils.fs.ls(path)
        
        # If ls() succeeds on a file path, it returns a list with one FileInfo object
        # If it's a file, we get the file itself; if it's a directory, ls() lists its contents
        if len(info) == 1 and info[0].path == path.rstrip('/'):
            return not info[0].isDir()
        
        # If ls() returns multiple items or path mismatch, it's a directory
        return False
        
    except Exception:
        # Path doesn't exist or is inaccessible
        return False


def is_dir(path: str) -> bool:
    """Determine whether a DBFS/ADLS path points to a directory.

    Args:
        path: Path to check (for example, ``"dbfs:/mnt/raw/"``).

    Returns:
        True if the path exists and is a directory, otherwise False.
    """
    try:
        # Try to list the path - if it's a directory, this will work
        dbutils.fs.ls(path)
        return True
    except Exception:
        return False


def exists(path: str) -> bool:
    """Check whether a DBFS/ADLS path exists.

    Args:
        path: Path to check.

    Returns:
        True if the path exists (file or directory), otherwise False.
    """
    try:
        dbutils.fs.ls(path)
        return True
    except Exception:
        return False


def ls(path: str, pattern: str = "*", recursive: bool = False, item_type: str = "files"):
    """List DBFS/ADLS files and directories that match a glob pattern.

    Args:
        path: Root directory or URI to search (for example, ``"dbfs:/mnt/raw"``).
        pattern: Glob pattern applied to the file or directory name only (default ``"*"``,
            such as ``"*.csv"``, ``"water*.json"``, or ``"temp_*"``).
        recursive: Whether to search subdirectories recursively. If False, only list the
            immediate children.
        item_type: Item types to include: ``"files"``, ``"dirs"``/``"directories"``, or ``"all"``.

    Returns:
        A list of Databricks ``FileInfo`` objects for matching items, sorted by ``.path``.

    Raises:
        ValueError: If ``item_type`` is not one of ``"files"``, ``"dirs"``, or ``"all"``.
    """
    results = []
    stack = [path]
    
    # Normalize item_type
    item_type = item_type.lower()
    if item_type in ["dirs", "directories"]:
        item_type = "dirs"
    elif item_type not in ["files", "dirs", "all"]:
        raise ValueError(f"item_type must be 'files', 'dirs', or 'all', got: {item_type}")
    
    while stack:
        current = stack.pop()
        
        try:
            items = dbutils.fs.ls(current)
        except Exception:
            # Skip directories that can't be accessed
            continue
            
        for info in items:
            if info.isDir():
                # Handle directories
                if recursive:
                    stack.append(info.path)
                
                # Include directory in results if we want dirs and pattern matches
                if (item_type in ["dirs", "all"] and 
                    fnmatch.fnmatch(info.name.rstrip('/'), pattern)):
                    results.append(info)
            else:
                # Handle files - include if we want files and pattern matches
                if (item_type in ["files", "all"] and 
                    fnmatch.fnmatch(info.name, pattern)): 
                    results.append(info)
        
        if not recursive:
            break
    
    # Sort results by path
    results.sort(key=lambda fi: fi.path)
    return results

def delete_selected_files(path: str, pattern: str = "*", recursive: bool = False) -> list[str]:
    """Delete files using the same semantics as :func:`ls`.

    Behavior:
    * If ``path`` is a file, delete that file only (ignoring ``pattern`` and ``recursive``).
    * If ``path`` is a directory, delete files whose names match ``pattern``; search
      subdirectories when ``recursive`` is True.
    * Directories themselves are not removed.

    Args:
        path: File or directory path (for example, ``"dbfs:/mnt/raw/file.csv"`` or ``"dbfs:/mnt/raw"``).
        pattern: Glob pattern on file names only (default ``"*"``, such as ``"*.csv"`` or ``"water*.json"``).
            Ignored when ``path`` is a file.
        recursive: Whether to traverse subdirectories when ``path`` is a directory.

    Returns:
        List of deleted file paths.
    """
    # File case → delete just this file
    if is_file(path):
        dbutils.fs.rm(path, recurse=False)
        return [path]

    # Directory case → reuse your ls (name-only glob) to find targets
    targets = [fi.path for fi in ls(path, pattern, recursive)]
    for p in targets:
        dbutils.fs.rm(p, recurse=False)
    return targets

def delete_folder(path, recursive=True):
    """Delete a directory using ``dbutils.fs.rm``.

    Only directories are supported.

    Args:
        path: Path to the folder to delete.
        recursive: Whether to remove all contents recursively. Defaults to True.

    Returns:
        None. The folder is removed as a side effect.

    Raises:
        ValueError: If ``path`` is not a directory.
    """
    
    # Check if path is a directory
    if not is_dir(path):
        raise ValueError(f"Path is not a directory: {path}")
    
    dbutils.fs.rm(path, recurse=recursive)

def copy_single_file(src, dst):
    """Copy a single file using ``dbutils.fs.cp``.

    Args:
        src: Source file path, which must be an existing file.
        dst: Destination path. If it ends with ``'/'``, it is treated as a directory and
            the source file name is appended; otherwise it is used as the target file path.

    Returns:
        None. The file is copied as a side effect.

    Raises:
        ValueError: If ``src`` is not an existing file.
    """
    
    # Validate source is an existing file
    if not is_file(src):
        raise ValueError(f"Source is not an existing file: {src}")
    

    # Determine destination path
    if dst.endswith('/'):
        # Destination is a directory - copy file into it
        filename = src.split('/')[-1]  # Extract filename from source
        final_dst = dst + filename
    else:
        # Destination is a file path
        final_dst = dst
    
        # Copy the file
    dbutils.fs.cp(src, final_dst, recurse=False)


def copy_selected_files(src_dir: str, dst_dir: str, pattern: str = "*", recursive: bool = False) -> list[str]:
    """Copy matched files from one directory to another using ``dbutils.fs.cp``.

    Behavior:
    * ``src_dir`` must exist and be a directory.
    * ``dst_dir`` may not exist (``cp`` will create parents) but must not be an existing file.
    * Matched files are discovered via :func:`ls` with name-only glob matching.
    * Copies preserve subfolder structure when ``recursive`` is True.

    Args:
        src_dir: Source directory (for example, ``"dbfs:/mnt/source"``).
        dst_dir: Destination directory (for example, ``"dbfs:/mnt/target"``). If missing, it
            will be created by ``cp``.
        pattern: Glob on file names only (default ``"*"``, such as ``"*.csv"`` or ``"water*.json"``).
        recursive: Whether to search subdirectories and preserve structure.

    Returns:
        List of destination paths for copied files.

    Raises:
        NotADirectoryError: If ``src_dir`` is a file or ``dst_dir`` is an existing file.
        FileNotFoundError: If ``src_dir`` does not exist.
    """
    src_root = _normalize_dir_path(_ensure_dir_exists(src_dir))
    dst_root = _normalize_dir_path(dst_dir)

    matches = ls(src_root, pattern=pattern, recursive=recursive)
    copied: list[str] = []

    for fi in matches:
        rel = fi.path[len(src_root) :] if fi.path.startswith(src_root) else fi.name
        dst_path = dst_root + rel
        dbutils.fs.cp(fi.path, dst_path, recurse=False)
        copied.append(dst_path)

    return copied

def copy_full_folder(src_dir: str, dst_dir: str) -> None:
    """Copy an entire directory tree using ``dbutils.fs.cp`` with recursion.

    Behavior:
    * ``src_dir`` must be an existing directory.
    * ``dst_dir`` must not be an existing file; if it does not exist, ``cp`` creates parents.
    * Relies on ``dbutils.fs.cp`` to create parents and overwrite existing files.

    Args:
        src_dir: Source directory (for example, ``"dbfs:/mnt/source"``).
        dst_dir: Destination directory (for example, ``"dbfs:/mnt/target"``).

    Returns:
        None. The directory tree is copied as a side effect.

    Raises:
        NotADirectoryError: If ``src_dir`` is a file or ``dst_dir`` is an existing file.
        FileNotFoundError: If ``src_dir`` does not exist.
    """
    s = _ensure_dir_exists(src_dir)
    d = dst_dir.rstrip("/")

    # Thin wrap over cp: always recursive for directories
    dbutils.fs.cp(s, d, recurse=True)

def move_single_file(src, dst):
    """Move a single file using ``dbutils.fs.mv``.

    Args:
        src: Source file path, which must be an existing file.
        dst: Destination path. If it ends with ``'/'``, it is treated as a directory and the
            source file name is appended; otherwise it is used as the target file path.

    Returns:
        None. The file is moved as a side effect.

    Raises:
        ValueError: If ``src`` is not an existing file.
    """
    
    # Validate source is an existing file
    if not is_file(src):
        raise ValueError(f"Source is not an existing file: {src}")
    
    # Determine destination path
    if dst.endswith('/'):
        # Destination is a directory - move file into it
        filename = src.split('/')[-1]  # Extract filename from source
        final_dst = dst + filename
    else:
        # Destination is a file path
        final_dst = dst
    
    # Move the file
    dbutils.fs.mv(src, final_dst)


def move_selected_files(src_dir: str, dst_dir: str, pattern: str = "*", recursive: bool = False) -> list[str]:
    """Move matched files from one directory to another using ``dbutils.fs.mv``.

    Behavior:
    * ``src_dir`` must exist and be a directory.
    * ``dst_dir`` may not exist (``mv`` will create parents) but must not be an existing file.
    * Matched files are discovered via :func:`ls` with name-only glob matching.
    * Moves preserve subfolder structure when ``recursive`` is True.

    Args:
        src_dir: Source directory (for example, ``"dbfs:/mnt/source"``).
        dst_dir: Destination directory (for example, ``"dbfs:/mnt/target"``). If missing, it
            will be created by ``mv``.
        pattern: Glob on file names only (default ``"*"``, such as ``"*.csv"`` or ``"water*.json"``).
        recursive: Whether to search subdirectories and preserve structure.

    Returns:
        List of destination paths for moved files.

    Raises:
        NotADirectoryError: If ``src_dir`` is a file or ``dst_dir`` is an existing file.
        FileNotFoundError: If ``src_dir`` does not exist.
    """
    src_root = _normalize_dir_path(_ensure_dir_exists(src_dir))
    dst_root = _normalize_dir_path(dst_dir)

    matches = ls(src_root, pattern=pattern, recursive=recursive)
    moved: list[str] = []

    for fi in matches:
        rel = fi.path[len(src_root) :] if fi.path.startswith(src_root) else fi.name
        dst_path = dst_root + rel
        dbutils.fs.mv(fi.path, dst_path)
        moved.append(dst_path)

    return moved


def move_full_folder(src_dir: str, dst_dir: str) -> None:
    """Move an entire directory tree using ``dbutils.fs.mv`` with recursion.

    Behavior:
    * ``src_dir`` must be an existing directory.
    * ``dst_dir`` must not be an existing file; if it does not exist, ``mv`` creates parents.
    * Uses ``dbutils.fs.mv`` for atomic directory moves.

    Args:
        src_dir: Source directory (for example, ``"dbfs:/mnt/source"``).
        dst_dir: Destination directory (for example, ``"dbfs:/mnt/target"``).

    Returns:
        None. The directory tree is moved as a side effect.

    Raises:
        NotADirectoryError: If ``src_dir`` is a file or ``dst_dir`` is an existing file.
        FileNotFoundError: If ``src_dir`` does not exist.
    """
    s = _ensure_dir_exists(src_dir)
    d = dst_dir.rstrip("/")

    # Move the entire directory tree atomically
    dbutils.fs.mv(s, d, True)

from typing import Callable, Literal
import fnmatch
import posixpath


def move_files_by_group(
    src_dir: str,
    dst_dir: str,
    resolver: Callable[[str], str | None],
    file_pattern: str = "*",
    policy: Literal["default", "skip", "fail"] = "default",
    recursive: bool = True,
) -> list[str]:
    """Move files into resolver-derived subfolders under a destination directory.

    Behavior:
    * ``src_dir`` must exist and be a directory.
    * Files are discovered using :func:`ls`.
    * ``file_pattern`` is a glob pattern applied to the file path relative to ``src_dir``.
      Examples: ``"*.csv"``, ``"inbound/*.csv"``, ``"*/2026/*.json"``
    * ``resolver`` receives the relative path and returns:
      - a relative subfolder path such as ``"a/b/c"``
      - ``""`` to place the file directly under ``dst_dir``
      - ``None`` if unresolved
    * If resolver returns ``None``:
      - ``policy="default"``: move directly under ``dst_dir``
      - ``policy="skip"``: skip the file
      - ``policy="fail"``: raise ``ValueError``

    Args:
        src_dir: Source directory.
        dst_dir: Destination root directory.
        resolver: Function that maps a relative source path to a relative target subfolder.
        file_pattern: Glob pattern applied to relative source paths. Defaults to ``"*"``
            which matches all files.
        policy: How to handle unresolved files.
        recursive: Whether to scan subdirectories recursively. Defaults to True.

    Returns:
        List of destination paths for moved files.

    Raises:
        ValueError: If ``policy`` is invalid or resolver returns an invalid relative path.
        FileNotFoundError: If ``src_dir`` does not exist.
        NotADirectoryError: If ``src_dir`` is a file.
    """
    if policy not in {"default", "skip", "fail"}:
        raise ValueError("policy must be 'default', 'skip', or 'fail'")

    src_root = _normalize_dir_path(_ensure_dir_exists(src_dir))
    dst_root = _normalize_dir_path(dst_dir)

    moved: list[str] = []

    for fi in ls(src_root, pattern="*", recursive=recursive, item_type="files"):
        rel_path = fi.path[len(src_root):] if fi.path.startswith(src_root) else fi.name

        if not fnmatch.fnmatch(rel_path, file_pattern):
            continue

        resolved_dir = resolver(rel_path)

        if resolved_dir is None:
            if policy == "skip":
                continue
            if policy == "fail":
                raise ValueError(f"Resolver returned None for file: {rel_path}")
            resolved_dir = ""

        resolved_dir = str(resolved_dir).strip()

        if resolved_dir:
            if resolved_dir.startswith("/"):
                raise ValueError(
                    f"Resolver must return a relative path, got: {resolved_dir}"
                )

            parts = [part.strip() for part in resolved_dir.split("/")]
            if any(not part or part in {".", ".."} for part in parts):
                raise ValueError(f"Invalid resolver path: {resolved_dir}")

            resolved_dir = "/".join(parts)

        file_name = posixpath.basename(fi.path)
        target_dir = (
            posixpath.join(dst_root.rstrip("/"), resolved_dir)
            if resolved_dir
            else dst_root.rstrip("/")
        )
        target_path = posixpath.join(target_dir, file_name)

        move_single_file(fi.path, target_path)
        moved.append(target_path)

    return moved

# The following functions 
def adsl_url(storage_account: str, container: str, path: str = "") -> str:
    """Build an Azure Data Lake Storage URL.

    Args:
        storage_account: Storage account name (for example, ``AZURE.lake``).
        container: Container name (for example, ``"restricted"``).
        path: Optional file path within the container.

    Returns:
        Full ADLS URL (for example,
        ``'abfss://restricted@lakecacnpdstdev01.dfs.core.windows.net/raw/data/file.json'``).
    """
    path = path.strip("/")
    path_part = f"/{path}" if path else ""
    return f"abfss://{container}@{storage_account}.dfs.core.windows.net{path_part}"

def workspace_url(path: str) -> str:
    """Normalize a workspace url."""

    path = (path or "").strip()
    if not path:
        raise ValueError("path must be a non-empty string")

    if path.startswith("/Workspace"):
        return f"file:{posixpath.normpath(path)}"
    else:
        joined = posixpath.join("/Workspace", path.lstrip("/"))
        return f"file:{posixpath.normpath(joined)}"

def _normalize_spark_file_uri(uri: str) -> str:
    """Normalize supported Databricks file paths for Spark-based reads.

    Supported path types:
    * Workspace paths as ``/Workspace/...`` or ``file:/Workspace/...``
    * Unity Catalog volume paths as ``/Volumes/...`` or ``dbfs:/Volumes/...``
    * ADLS Gen2 paths as ``abfss://...``

    Args:
        uri: Input path or URI.

    Returns:
        A Spark-readable URI or path.

    Raises:
        ValueError: If the URI is empty or uses an unsupported path type.
    """
    value = str(uri or "").strip()
    if not value:
        raise ValueError("Empty URI.")

    if value.startswith("abfss://"):
        return value

    if value.startswith("/Workspace"):
        return workspace_url(value)

    if value.startswith("file:"):
        workspace_path = "/" + value[len("file:"):].lstrip("/")
        if not workspace_path.startswith("/Workspace"):
            raise ValueError(
                "Only Databricks workspace file paths are supported for file: URIs."
            )
        return f"file:{posixpath.normpath(workspace_path)}"

    if value.startswith("/Volumes/"):
        return posixpath.normpath(value)

    if value.startswith("dbfs:/Volumes/"):
        volume_path = "/" + value[len("dbfs:"):].lstrip("/")
        return posixpath.normpath(volume_path)

    raise ValueError(
        "Unsupported URI. Expected /Workspace/..., file:/Workspace/..., "
        "/Volumes/..., dbfs:/Volumes/..., or abfss://..."
    )

def read_text_file(uri: str, *, encoding: str = "utf-8", errors: str = "strict") -> str:
    """Read a text or HTML file into a string using Spark-backed file access.

    Supported schemes:
    * ``/Workspace/...`` or ``file:/Workspace/...`` for workspace files.
    * ``/Volumes/...`` or ``dbfs:/Volumes/...`` for Unity Catalog volumes.
    * ``abfss://...`` for ADLS Gen2 (Spark must be configured with credentials).

    Args:
        uri: Workspace, volume, or ADLS path.
        encoding: Text encoding used to decode bytes. Defaults to ``"utf-8"``.
        errors: Decoding error policy (for example, ``"strict"``, ``"ignore"``, ``"replace"``).

    Returns:
        File contents as a string.

    Raises:
        ValueError: If the URI is empty or the scheme is unsupported.
        RuntimeError: If there is no active Spark session.
        FileNotFoundError: If no file matches the URI.
    """
    normalized_uri = _normalize_spark_file_uri(uri)
    logger.debug("Reading text file via Spark from %s", normalized_uri)

    # Spark session required
    spark = SparkSession.builder.getOrCreate()
    if spark is None:
        raise RuntimeError("An active Spark session is required.")

    # Read as a DataFrame with a 'content' (binary) column (no RDD usage)
    df = spark.read.format("binaryFile").load(normalized_uri).select("content").limit(1)
    row = df.first()
    if row is None:
        raise FileNotFoundError(f"No file found at: {normalized_uri}")

    data = row["content"]  # bytes (or bytearray)
    if isinstance(data, bytearray):
        data = bytes(data)

    return data.decode(encoding, errors)

def read_json_file(uri: str, *, encoding: str = "utf-8") -> Any:
    """Read a JSON file from workspace, volume, or ADLS storage using Spark.

    The file is loaded through Spark's ``binaryFile`` source and parsed with
    ``json.loads``. This keeps the helper suitable for JSON documents stored as a
    single file, including multi-line objects and arrays.

    Supported path types:
    * ``/Workspace/...`` or ``file:/Workspace/...``
    * ``/Volumes/...`` or ``dbfs:/Volumes/...``
    * ``abfss://...``

    Args:
        uri: Workspace, volume, or ADLS path to a JSON file.
        encoding: Text encoding used to decode the file. Defaults to ``"utf-8"``.

    Returns:
        Parsed JSON content.

    Raises:
        ValueError: If the URI is unsupported.
        RuntimeError: If there is no active Spark session.
        FileNotFoundError: If no file matches the URI.
        json.JSONDecodeError: If the file content is not valid JSON.
    """
    normalized_uri = _normalize_spark_file_uri(uri)
    logger.debug("Reading JSON file via Spark from %s", normalized_uri)

    return json.loads(read_text_file(normalized_uri, encoding=encoding))
