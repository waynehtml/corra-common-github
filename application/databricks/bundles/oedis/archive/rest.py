from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from oedis.task_result import TaskResult
from oedis.handler.lib.utils import resolve_inbox_relative_path, truncate_text

logger = logging.getLogger(__name__)

_SENSITIVE_HEADER_KEYS = ("authorization", "token", "api-key", "secret", "password")


class AuthError(RuntimeError):
    pass


class UnsupportedAuthTypeError(RuntimeError):
    pass


# remove sensitive headers before logging or returning metadata.
def _redact_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
    redacted: Dict[str, Any] = {}
    for key, value in headers.items():
        # Replace sensitive header values so logs stay safe.
        if any(fragment in key.lower() for fragment in _SENSITIVE_HEADER_KEYS):
            redacted[key] = "***"
        else:
            redacted[key] = value
    return redacted


# parse an OAuth expires_in value into an integer.
def _parse_expires_in(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# request an OAuth2 token and return token data with expiration metadata.
def fetch_oauth2_token(
    session: requests.Session,
    auth_cfg: Dict[str, Any],
    timeout_sec: Optional[float],
) -> Tuple[str, Optional[int]]:
    token_url = auth_cfg.get("token_url")
    client_id = auth_cfg.get("client_id")
    client_secret = auth_cfg.get("client_secret")

    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    scope = auth_cfg.get("scope")
    if scope:
        payload["scope"] = scope

    request_kwargs: Dict[str, Any] = {"data": payload}
    if timeout_sec is not None:
        request_kwargs["timeout"] = timeout_sec

    try:
        response = session.post(token_url, **request_kwargs)
    except requests.exceptions.RequestException as exc:
        raise AuthError(f"OAuth token request failed: status=N/A body={truncate_text(str(exc))}") from exc

    if response.status_code >= 400:
        raise AuthError(
            "OAuth token request failed: "
            f"status={response.status_code} body={truncate_text(response.text or '')}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise AuthError(
            "OAuth token response was not valid JSON: "
            f"status={response.status_code} body={truncate_text(response.text or '')}"
        ) from exc

    access_token = body.get("access_token")
    if not access_token:
        raise AuthError(
            "OAuth token response missing access_token: "
            f"status={response.status_code} body={truncate_text(response.text or '')}"
        )

    expires_in = _parse_expires_in(body.get("expires_in"))
    return access_token, expires_in


# merge query parameters into a base URL string.
def _merge_query(url: str, query: Dict[str, Any]) -> str:
    # Merge existing query parameters with overrides from the request spec.
    parsed = urlparse(url)
    query_pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_pairs.update(query)
    new_query = urlencode(query_pairs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


# redact credentials or sensitive query parameters in a URL.
def _redact_url(url: str) -> str:
    # Redact sensitive query string values before logging.
    parsed = urlparse(url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    redacted_pairs = []
    for key, value in query_pairs:
        if any(fragment in key.lower() for fragment in _SENSITIVE_HEADER_KEYS):
            redacted_pairs.append((key, "***"))
        else:
            redacted_pairs.append((key, value))
    redacted_query = urlencode(redacted_pairs, doseq=True)
    return urlunparse(parsed._replace(query=redacted_query))


# normalize and validate a file path anchored to the inbox root.
def _normalize_inbox_rel_path(inbox: Path, file_path: Any) -> str:
    if file_path is None or str(file_path).strip() == "":
        raise ValueError("file is required")
    return resolve_inbox_relative_path(inbox, file_path, field_name="file")



# download HTTP resources to the inbox and return a TaskResult.
def download(ctx) -> TaskResult:
    """
    REST download handler.

    Expected ctx.task_params example:
      {
        "connection": {
          "timeout_sec": 60,
          "headers": {"Accept": "application/json"},
          "fault_tolerant": {
            "retries": 3,
            "retry_interval_sec": 30,
            "retry_on_status": [429, 500, 502, 503, 504]
          }
        },
        "requests": [
          {
            "id": "get_report",
            "method": "GET",
            "url": "https://example/api/report",
            "headers": {"r": "abc"},
            "query": {"date": "{{rundate}}"},
            "body_mode": "json",
            "body": [{"vectorId": "{{param(name=vectorId)}}"}],
            "file": "raw/report_{{rundate}}.json"
          }
        ]
      }

    Behavior notes:
    - Merges headers as {**connection.headers, **request.headers}.
    - Adds request.query to URL query parameters (request wins).
    - Calls response.raise_for_status() before writing the response body
      (note: requests does not raise for 3xx responses).
    - Writes response bytes to ctx.inbox/<file> if the request succeeds.
    - Retries on request exceptions and configured HTTP status codes.
    """
    start_time = time.time()
    task_param = getattr(ctx, "task_params", None) or {}
    logger.info("task param: %s", task_param)

    connection_cfg = task_param.get("connection") or {}
    connection_headers = connection_cfg.get("headers") or {}

    # Connection-level defaults for timeouts and retry behavior.
    timeout_sec = connection_cfg.get("timeout_sec")
    connection_fault = connection_cfg.get("fault_tolerant") or {}
    connection_retries = int(connection_fault.get("retries", 0) or 0)
    connection_retry_interval = int(connection_fault.get("retry_interval_sec", 0) or 0)
    connection_retry_statuses = [int(code) for code in connection_fault.get("retry_on_status") or []]

    request_specs = task_param.get("requests") or []

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")
    inbox = Path(ctx.inbox)
    inbox.mkdir(parents=True, exist_ok=True)

    outputs: List[Dict[str, Any]] = []

    session = requests.Session()
    resolved_connection_headers = dict(connection_headers)
    auth_cfg = connection_cfg.get("auth") or None
    if auth_cfg:
        auth_type = auth_cfg.get("type")
        if auth_type != "oauth2_client_credentials":
            raise UnsupportedAuthTypeError(f"Unsupported auth type: {auth_type}")

        connection_auth_state = getattr(ctx, "connection_auth_state", None)
        if connection_auth_state is None:
            connection_auth_state = {}
            setattr(ctx, "connection_auth_state", connection_auth_state)

        access_token = connection_auth_state.get("access_token")
        if not access_token:
            access_token, _ = fetch_oauth2_token(session, auth_cfg, timeout_sec)
            connection_auth_state["access_token"] = access_token

        header_name = auth_cfg.get("header_name") or "Authorization"
        header_format = auth_cfg.get("header_format") or "Bearer {token}"
        resolved_connection_headers[header_name] = header_format.format(token=access_token)

    for idx, spec in enumerate(request_specs):
        request_id = spec.get("id") or f"request_{idx + 1}"
        method = (spec.get("method") or "GET").upper()
        url = spec.get("url")

        # Merge per-request headers on top of the connection headers.
        request_headers = spec.get("headers") or {}
        effective_headers = {**resolved_connection_headers, **request_headers}

        # Merge query params from the spec into the URL.
        query = spec.get("query") or {}
        effective_url = _merge_query(url, query) if query else url
        log_url = _redact_url(effective_url)

        # Ensure the file stays within the inbox root.
        file_rel = _normalize_inbox_rel_path(inbox, spec.get("file"))

        # Apply per-request retry behavior overrides.
        req_fault = spec.get("fault_tolerant") or {}
        retries = int(req_fault.get("retries", connection_retries) or connection_retries)
        retry_interval = int(
            req_fault.get("retry_interval_sec", connection_retry_interval) or connection_retry_interval
        )
        retry_statuses = req_fault.get("retry_on_status", connection_retry_statuses) or []
        retry_statuses = [int(code) for code in retry_statuses]

        # Prepare the outbound request payload, honoring body_mode if present.
        body_mode = (spec.get("body_mode") or "raw").lower()
        body = spec.get("body")

        request_kwargs: Dict[str, Any] = {"headers": effective_headers}
        if timeout_sec is not None:
            request_kwargs["timeout"] = timeout_sec

        if body is not None:
            if body_mode == "json":
                request_kwargs["json"] = body
            elif body_mode == "form":
                request_kwargs["data"] = body
            else:
                request_kwargs["data"] = body

        attempt = 0
        request_start = time.perf_counter()
        while True:
            try:
                response = session.request(method, effective_url, **request_kwargs)
                status_code = response.status_code
                response_bytes = len(response.content)
                logger.info(
                    "REST request %s %s %s status=%s bytes=%s",
                    request_id,
                    method,
                    log_url,
                    status_code,
                    response_bytes,
                )

                if status_code in retry_statuses and attempt < retries:
                    attempt += 1
                    # Retry on explicit status codes before validating/writing.
                    logger.warning(
                        "Retrying %s due to status %s (attempt %s/%s)",
                        request_id,
                        status_code,
                        attempt,
                        retries,
                    )
                    time.sleep(retry_interval)
                    continue

                response.raise_for_status()

                # Persist the successful response to the inbox.
                local_path = inbox / file_rel
                local_path.parent.mkdir(parents=True, exist_ok=True)
                with open(local_path, "wb") as handle:
                    handle.write(response.content)

                outputs.append(
                    {
                        "src": {"type": "file", "path": effective_url, "size": response_bytes},
                        "dest": {"type": "file", "path": str(local_path), "size": response_bytes},
                        "duration_sec": round(time.perf_counter() - request_start, 3),
                    }
                )
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                if attempt < retries:
                    attempt += 1
                    logger.warning(
                        "Retrying %s after exception %s (attempt %s/%s)",
                        request_id,
                        exc.__class__.__name__,
                        attempt,
                        retries,
                    )
                    time.sleep(retry_interval)
                    continue
                raise RuntimeError(
                    f"Request {request_id} failed after retries: {exc.__class__.__name__}: {exc}"
                ) from exc
            except requests.exceptions.RequestException as exc:
                raise RuntimeError(
                    f"Request {request_id} failed: {exc.__class__.__name__}: {exc}"
                ) from exc

        logger.debug("Request headers (redacted): %s", _redact_headers(effective_headers))

    return TaskResult(
        task_id=ctx.task_id or "",
        handler="oedis.handler.rest.download",
        status="success",
        outputs=outputs,
        metrics={"total_items": len(outputs), "total_duration_sec": round(time.time() - start_time, 3)},
    )
