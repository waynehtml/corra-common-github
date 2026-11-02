from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, urlparse

import requests

from oedis.task_result import TaskResult
from oedis.handler.lib.utils import matches_any, parse_datetime

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


@dataclass
class SharePointItem:
    item_id: str
    name: str
    rel_path: str
    last_modified: datetime
    size: int


def _normalize_sharepoint_subdir(value: Optional[str], field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError(f"{field_name} must be relative to the configured SharePoint drive: {value}")
    candidate = Path(normalized)
    if candidate.is_absolute():
        raise ValueError(f"{field_name} must be relative to the configured SharePoint drive: {value}")
    if ".." in candidate.parts:
        raise ValueError(f"{field_name} must not contain '..': {value}")
    return "/".join(part for part in candidate.parts if part not in ("", "."))


def _validate_rel_path(rel_path: str) -> None:
    candidate = Path(rel_path)
    if candidate.is_absolute():
        raise ValueError(f"file path must be relative: {rel_path}")
    if ".." in candidate.parts:
        raise ValueError(f"file path must not contain '..': {rel_path}")


def _normalize_local_subdir(value: Optional[str]) -> Path:
    raw = str(value or "").strip()
    if not raw:
        return Path()
    normalized = raw.replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute():
        raise ValueError(f"dst_dir must be relative to ctx.inbox, got absolute path: {value}")
    if ".." in candidate.parts:
        raise ValueError(f"dst_dir must not contain '..': {value}")
    return Path(*[part for part in candidate.parts if part not in ("", ".")])


def _normalize_patterns(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        return [value]
    return [str(value)]


def _parse_site_url(site_url: str) -> Tuple[str, str]:
    parsed = urlparse(site_url)
    return parsed.hostname or "", parsed.path or "/"


def _make_token(session: requests.Session, tenant_id: str, client_id: str, client_secret: str) -> str:
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    logger.debug(
        "Requesting Microsoft Graph token: tenant_id=%s client_id_present=%s client_secret_present=%s",
        tenant_id,
        bool(client_id),
        bool(client_secret),
    )
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": GRAPH_SCOPE,
    }
    response = session.post(token_url, data=payload)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Token request failed: status={response.status_code} body={(response.text or '')[:512]}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Token response was not valid JSON: status={response.status_code} body={(response.text or '')[:512]}"
        ) from exc
    token = body.get("access_token")
    if not token:
        raise RuntimeError("Token response missing access_token")
    return token


def _sleep_backoff(attempt: int, retry_after: Optional[str]) -> None:
    if retry_after:
        try:
            delay = int(retry_after)
        except ValueError:
            delay = 0
    else:
        delay = 0
    if delay <= 0:
        delay = min(60, 2**attempt)
    time.sleep(delay)


def _request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: Dict[str, str],
    timeout: Optional[float],
    retries: int = 3,
) -> requests.Response:
    attempt = 0
    while True:
        logger.debug(
            "Sending Graph request: method=%s url=%s attempt=%s timeout=%s",
            method,
            url,
            attempt + 1,
            timeout,
        )
        try:
            response = session.request(method, url, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"Graph request failed: url={url} error={exc}") from exc
        logger.debug(
            "Graph response received: method=%s url=%s status=%s",
            method,
            url,
            response.status_code,
        )
        if response.status_code != 429 or attempt >= retries:
            return response
        attempt += 1
        logger.warning("Retrying after throttling (status=429) for %s (attempt %s/%s)", url, attempt, retries)
        _sleep_backoff(attempt, response.headers.get("Retry-After"))


def _request_json(
    session: requests.Session,
    url: str,
    headers: Dict[str, str],
    timeout: Optional[float],
) -> Dict[str, Any]:
    response = _request_with_retry(session, "GET", url, headers=headers, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"Graph request failed: status={response.status_code} url={url} body={response.text}")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"Graph response was not valid JSON: url={url}") from exc


def _paged_get(
    session: requests.Session,
    url: str,
    headers: Dict[str, str],
    timeout: Optional[float],
) -> Iterable[Dict[str, Any]]:
    next_url: Optional[str] = url
    page = 0
    while next_url:
        page += 1
        logger.debug("Fetching Graph page %s: %s", page, next_url)
        payload = _request_json(session, next_url, headers, timeout)
        values = payload.get("value", [])
        logger.debug(
            "Fetched Graph page %s: item_count=%s has_next=%s",
            page,
            len(values),
            bool(payload.get("@odata.nextLink")),
        )
        for item in values:
            yield item
        next_url = payload.get("@odata.nextLink")


def _resolve_site_id(
    session: requests.Session,
    headers: Dict[str, str],
    timeout: Optional[float],
    site_url: str,
) -> str:
    hostname, path = _parse_site_url(site_url)
    logger.debug("Resolving SharePoint site id: hostname=%s path=%s", hostname, path)
    encoded_path = quote(path, safe="/")
    url = f"{GRAPH_BASE_URL}/sites/{hostname}:{encoded_path}"
    payload = _request_json(session, url, headers, timeout)
    site_id = payload.get("id")
    if not site_id:
        raise RuntimeError("Unable to resolve SharePoint site id")
    return site_id


def _resolve_drive_id(
    session: requests.Session,
    headers: Dict[str, str],
    timeout: Optional[float],
    site_id: str,
    drive_name: str,
) -> str:
    url = f"{GRAPH_BASE_URL}/sites/{site_id}/drives"
    logger.debug("Resolving SharePoint drive id: site_id=%s drive_name=%s", site_id, drive_name)
    for drive in _paged_get(session, url, headers, timeout):
        if drive.get("name") == drive_name:
            drive_id = drive.get("id")
            if drive_id:
                return drive_id
    raise RuntimeError(f"Drive '{drive_name}' not found for site {site_id}")


def _resolve_folder_item(
    session: requests.Session,
    headers: Dict[str, str],
    timeout: Optional[float],
    drive_id: str,
    dir_path: str,
) -> Dict[str, Any]:
    if not dir_path:
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root"
    else:
        encoded = quote(dir_path, safe="/")
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root:/{encoded}"
    logger.debug("Resolving SharePoint folder: drive_id=%s dir=%s", drive_id, dir_path or "/")
    payload = _request_json(session, url, headers, timeout)
    if "folder" not in payload:
        raise RuntimeError(f"Folder '{dir_path or '/'}' not found in drive {drive_id}")
    return payload


def _resolve_item_rel_path(drive_id: str, dir_path: str, item: Dict[str, Any]) -> str:
    parent_path = (item.get("parentReference") or {}).get("path") or ""
    prefix = f"/drives/{drive_id}/root:"
    if parent_path.startswith(prefix):
        item_parent = parent_path[len(prefix) :]
    else:
        item_parent = parent_path
    item_parent = item_parent.strip("/")
    item_path = "/".join(p for p in [item_parent, item.get("name") or ""] if p)
    if not dir_path:
        return item_path
    dir_prefix = dir_path.strip("/")
    if item_path.startswith(dir_prefix):
        rel = item_path[len(dir_prefix) :].lstrip("/")
        return rel or (item.get("name") or "")
    raise RuntimeError(
        f"SharePoint item path {item_path!r} is not under configured source directory {dir_path!r}"
    )


def _list_items(
    session: requests.Session,
    headers: Dict[str, str],
    timeout: Optional[float],
    drive_id: str,
    folder_id: str,
    dir_path: str,
    recursive: bool,
) -> List[SharePointItem]:
    items: List[SharePointItem] = []
    queue: List[str] = [folder_id]
    logger.debug(
        "Listing SharePoint items: drive_id=%s folder_id=%s dir=%s recursive=%s",
        drive_id,
        folder_id,
        dir_path or "/",
        recursive,
    )
    while queue:
        current_id = queue.pop(0)
        logger.debug(
            "Listing SharePoint children: current_folder_id=%s remaining_queue=%s",
            current_id,
            len(queue),
        )
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{current_id}/children"
        for child in _paged_get(session, url, headers, timeout):
            if child.get("folder") is not None:
                if recursive:
                    child_id = child.get("id")
                    if child_id:
                        logger.debug(
                            "Queueing SharePoint subfolder: child_id=%s child_name=%s",
                            child_id,
                            child.get("name") or "",
                        )
                        queue.append(child_id)
                continue
            if child.get("file") is None:
                continue
            rel_path = _resolve_item_rel_path(drive_id, dir_path, child)
            last_modified_raw = child.get("lastModifiedDateTime")
            last_modified = parse_datetime(last_modified_raw, "lastModifiedDateTime") or datetime.now(
                timezone.utc
            )
            items.append(
                SharePointItem(
                    item_id=child.get("id"),
                    name=child.get("name") or "",
                    rel_path=rel_path,
                    last_modified=last_modified.astimezone(timezone.utc),
                    size=int(child.get("size") or 0),
                )
            )
    logger.debug(
        "Completed SharePoint listing: drive_id=%s dir=%s file_count=%s",
        drive_id,
        dir_path or "/",
        len(items),
    )
    return items


def _filter_items(
    items: List[SharePointItem],
    include: Iterable[str],
    exclude: Iterable[str],
    updated_after: Optional[datetime],
    updated_before: Optional[datetime],
) -> List[SharePointItem]:
    filtered: List[SharePointItem] = []
    include_patterns = [pattern for pattern in include if pattern]
    exclude_patterns = [pattern for pattern in exclude if pattern]
    logger.debug(
        "Filtering SharePoint items: total=%s include=%s exclude=%s updated_after=%s updated_before=%s",
        len(items),
        include_patterns,
        exclude_patterns,
        updated_after.isoformat() if updated_after else None,
        updated_before.isoformat() if updated_before else None,
    )
    for item in items:
        if include_patterns and not matches_any(item.rel_path, include_patterns):
            continue
        if exclude_patterns and matches_any(item.rel_path, exclude_patterns):
            continue
        if updated_after and item.last_modified < updated_after.astimezone(timezone.utc):
            continue
        if updated_before and item.last_modified >= updated_before.astimezone(timezone.utc):
            continue
        filtered.append(item)
    logger.debug("Filtered SharePoint items: matched=%s", len(filtered))
    return filtered


def _download_item(
    session: requests.Session,
    headers: Dict[str, str],
    timeout: Optional[float],
    drive_id: str,
    item: SharePointItem,
    dest_path: Path,
) -> int:
    url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item.item_id}/content"
    logger.debug(
        "Downloading SharePoint item: item_id=%s rel_path=%s dest_path=%s",
        item.item_id,
        item.rel_path,
        dest_path,
    )
    response = _request_with_retry(session, "GET", url, headers=headers, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"Download failed: status={response.status_code} url={url} body={response.text}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as handle:
        handle.write(response.content)
    logger.debug(
        "Downloaded SharePoint item: item_id=%s bytes_written=%s dest_path=%s",
        item.item_id,
        len(response.content),
        dest_path,
    )
    return len(response.content)


def download(ctx) -> TaskResult:
    handler_start = time.perf_counter()
    task_params = getattr(ctx, "task_params", None) or {}

    connection = task_params.get("connection") or {}
    auth = connection.get("auth") or {}
    site_url = connection.get("site")
    drive_name = connection.get("drive")
    tenant_id = auth.get("tenant_id")
    client_id = auth.get("client_id")
    client_secret = auth.get("client_secret")

    request_specs = task_params.get("requests") or []

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")
    inbox = Path(ctx.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    hostname, site_path = _parse_site_url(site_url or "")
    logger.info(
        "Starting SharePoint download handler task_id=%s request_count=%s inbox=%s site_host=%s site_path=%s drive=%s",
        getattr(ctx, "task_id", None),
        len(request_specs),
        inbox,
        hostname,
        site_path,
        drive_name,
    )
    logger.debug(
        "SharePoint connection settings: timeout_sec=%s client_id_present=%s client_secret_present=%s",
        connection.get("timeout_sec"),
        bool(client_id),
        bool(client_secret),
    )

    if not tenant_id or not client_id or not client_secret:
        raise ValueError("sharepoint.download requires connection.auth.tenant_id/client_id/client_secret")

    session = requests.Session()
    token = _make_token(session, tenant_id, client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}

    timeout_sec = connection.get("timeout_sec")
    site_id = _resolve_site_id(session, headers, timeout_sec, site_url)
    drive_id = _resolve_drive_id(session, headers, timeout_sec, site_id, drive_name)
    logger.info("Resolved SharePoint site id=%s drive id=%s", site_id, drive_id)

    outputs: List[Dict[str, Any]] = []

    for idx, request in enumerate(request_specs):
        dir_path = _normalize_sharepoint_subdir(request.get("src_dir"), "src_dir")
        dst_dir = _normalize_local_subdir(request.get("dst_dir"))
        recursive = bool(request.get("recursive", False))
        include = _normalize_patterns(request.get("include"))
        exclude = _normalize_patterns(request.get("exclude"))

        updated_after = parse_datetime(request.get("updated_after"), "updated_after")
        updated_before = parse_datetime(request.get("updated_before"), "updated_before")
        logger.debug(
            "Request[%s] normalized config: src_dir=%s dst_dir=%s recursive=%s include=%s exclude=%s updated_after=%s updated_before=%s",
            idx,
            dir_path or "/",
            str(dst_dir) or ".",
            recursive,
            include,
            exclude,
            updated_after.isoformat() if updated_after else None,
            updated_before.isoformat() if updated_before else None,
        )

        folder_item = _resolve_folder_item(session, headers, timeout_sec, drive_id, dir_path)
        folder_id = folder_item.get("id")
        if not folder_id:
            raise RuntimeError(f"Folder '{dir_path or '/'}' not found in drive {drive_id}")
        logger.info(
            "Request[%s] folder id=%s src_dir=%s dst_dir=%s recursive=%s",
            idx,
            folder_id,
            dir_path or "/",
            str(dst_dir) or ".",
            recursive,
        )

        items = _list_items(session, headers, timeout_sec, drive_id, folder_id, dir_path, recursive)
        matched = _filter_items(items, include, exclude, updated_after, updated_before)

        request_listed = len(items)
        request_matched = len(matched)
        request_downloaded = 0

        for item in matched:
            dest_rel = item.rel_path
            _validate_rel_path(dest_rel)
            dest_path = inbox / dst_dir / dest_rel
            logger.debug(
                "Preparing SharePoint download: request_index=%s item_id=%s rel_path=%s dest_rel=%s dest_path=%s",
                idx,
                item.item_id,
                item.rel_path,
                dest_rel,
                dest_path,
            )
            record = {
                "src": {"type": "file", "path": item.rel_path, "size": item.size},
                "dest": {"type": "file", "path": str(dest_path), "size": item.size},
                "duration_sec": 0.0,
            }
            outputs.append(record)
            download_start = time.perf_counter()
            bytes_written = _download_item(session, headers, timeout_sec, drive_id, item, dest_path)
            record["duration_sec"] = round(time.perf_counter() - download_start, 3)
            record["src"]["size"] = bytes_written
            record["dest"]["size"] = bytes_written
            request_downloaded += 1
        logger.info(
            "Request[%s] listed=%s matched=%s downloaded=%s",
            idx,
            request_listed,
            request_matched,
            request_downloaded,
        )

    total_duration = round(time.perf_counter() - handler_start, 3)
    logger.info(
        "Completed SharePoint download handler task_id=%s downloaded_items=%s duration_sec=%s",
        getattr(ctx, "task_id", None),
        len(outputs),
        total_duration,
    )
    return TaskResult(
        task_id=ctx.task_id or "",
        handler=getattr(ctx, "task_handler", None) or "oedis.handler.sharepoint.download",
        status="success",
        outputs=outputs,
        metrics={"total_items": len(outputs), "total_duration_sec": total_duration},
    )