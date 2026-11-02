from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from oedis.handler.lib.utils import resolve_inbox_relative_path, truncate_text
from oedis.task_result import TaskResult

logger = logging.getLogger(__name__)

_SENSITIVE_HEADER_KEYS = ("authorization", "token", "api-key", "secret", "password")
_OAUTH2_EXPIRY_SKEW_SEC = 30


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


# validate a required structured auth field.
def _require_auth_field(auth_cfg: Dict[str, Any], auth_type: str, field_name: str) -> str:
    value = auth_cfg.get(field_name)
    if value is None or str(value).strip() == "":
        raise ValueError(f'connection.auth.{field_name} is required when connection.auth.type="{auth_type}"')
    return str(value)


# request an OAuth2 token and return token data with expiration metadata.
def fetch_oauth2_token(
    session: requests.Session,
    auth_cfg: Dict[str, Any],
    timeout_sec: Optional[float],
) -> Tuple[str, Optional[int]]:
    token_url = _require_auth_field(auth_cfg, "oauth2_client_credentials", "token_url")
    client_id = _require_auth_field(auth_cfg, "oauth2_client_credentials", "client_id")
    client_secret = _require_auth_field(auth_cfg, "oauth2_client_credentials", "client_secret")

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
    return str(access_token), expires_in


# build a standard HTTP Basic authorization header value.
def _build_basic_authorization(username: str, password: str) -> str:
    credential_bytes = f"{username}:{password}".encode("utf-8")
    return f"Basic {base64.b64encode(credential_bytes).decode('ascii')}"


# access or initialize per-connection auth state on the context.
def _get_connection_auth_state(ctx) -> Dict[str, Any]:
    connection_auth_state = getattr(ctx, "connection_auth_state", None)
    if connection_auth_state is None:
        connection_auth_state = {}
        setattr(ctx, "connection_auth_state", connection_auth_state)
    return connection_auth_state


# resolve a cached OAuth2 bearer token, refreshing when needed.
def _resolve_oauth2_access_token(
    ctx,
    session: requests.Session,
    auth_cfg: Dict[str, Any],
    timeout_sec: Optional[float],
) -> str:
    token_url = _require_auth_field(auth_cfg, "oauth2_client_credentials", "token_url")
    client_id = _require_auth_field(auth_cfg, "oauth2_client_credentials", "client_id")
    scope = str(auth_cfg.get("scope") or "")

    cache_key = f"{token_url}|{client_id}|{scope}"
    connection_auth_state = _get_connection_auth_state(ctx)
    oauth_state = connection_auth_state.get(cache_key) or {}

    access_token = oauth_state.get("access_token")
    expires_at_epoch = oauth_state.get("expires_at_epoch")
    if access_token and (expires_at_epoch is None or time.time() < float(expires_at_epoch)):
        logger.debug("Reusing cached OAuth2 access token for token_url=%s", token_url)
        return str(access_token)

    logger.info("Fetching OAuth2 access token for token_url=%s", token_url)
    access_token, expires_in = fetch_oauth2_token(session, auth_cfg, timeout_sec)

    oauth_state = {"access_token": access_token}
    if expires_in is not None:
        oauth_state["expires_at_epoch"] = time.time() + max(expires_in - _OAUTH2_EXPIRY_SKEW_SEC, 0)
    connection_auth_state[cache_key] = oauth_state
    logger.debug(
        "Cached OAuth2 access token for token_url=%s expires_at_epoch=%s",
        token_url,
        oauth_state.get("expires_at_epoch"),
    )
    return access_token


# merge request params into a base URL string.
def _merge_params(url: str, params: Dict[str, Any]) -> str:
    # Merge existing query parameters with overrides from the request spec.
    parsed = urlparse(url)
    query_pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_pairs.update(params)
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
        raise ValueError("file must be a non-empty inbox-relative path when provided")
    return resolve_inbox_relative_path(inbox, file_path, field_name="file")


# prepare outbound request body arguments from the structured body config.
def _build_request_body_kwargs(request_id: str, body_cfg: Any) -> Dict[str, Any]:
    if body_cfg is None:
        logger.debug("REST request %s has no request body", request_id)
        return {}
    if not isinstance(body_cfg, dict):
        raise ValueError(f"Request {request_id} body must be an object with type and value")
    if "type" not in body_cfg or "value" not in body_cfg:
        raise ValueError(f"Request {request_id} requires both body.type and body.value when body is present")

    body_type = str(body_cfg.get("type") or "").lower()
    body_value = body_cfg.get("value")

    if body_type == "json":
        logger.debug("REST request %s body_type=json", request_id)
        return {"json": body_value}
    if body_type == "form":
        if not isinstance(body_value, dict):
            raise ValueError(f'Request {request_id} body.value must be an object when body.type="form"')
        logger.debug("REST request %s body_type=form form_field_count=%s", request_id, len(body_value))
        return {"data": body_value}
    if body_type == "raw":
        if not isinstance(body_value, str):
            raise ValueError(f'Request {request_id} body.value must be a string when body.type="raw"')
        logger.debug("REST request %s body_type=raw raw_length=%s", request_id, len(body_value))
        return {"data": body_value}

    raise ValueError(f'Request {request_id} body.type must be one of: "json", "form", "raw"')


# resolve structured auth headers that should be applied at the connection level.
def _resolve_connection_auth_headers(
    ctx,
    session: requests.Session,
    auth_cfg: Optional[Dict[str, Any]],
    timeout_sec: Optional[float],
) -> Dict[str, str]:
    if not auth_cfg:
        return {}

    auth_type = str(auth_cfg.get("type") or "none").lower()
    logger.info("Resolving REST connection auth type=%s", auth_type)

    if auth_type == "none":
        return {}
    if auth_type == "basic":
        username = _require_auth_field(auth_cfg, auth_type, "username")
        password = _require_auth_field(auth_cfg, auth_type, "password")
        return {"Authorization": _build_basic_authorization(username, password)}
    if auth_type == "bearer":
        token = _require_auth_field(auth_cfg, auth_type, "token")
        return {"Authorization": f"Bearer {token}"}
    if auth_type == "api_key":
        name = _require_auth_field(auth_cfg, auth_type, "name")
        value = _require_auth_field(auth_cfg, auth_type, "value")
        return {name: value}
    if auth_type == "oauth2_client_credentials":
        access_token = _resolve_oauth2_access_token(ctx, session, auth_cfg, timeout_sec)
        return {"Authorization": f"Bearer {access_token}"}

    raise UnsupportedAuthTypeError(f"Unsupported auth type: {auth_type}")


# execute HTTP requests and optionally write responses to the inbox.
def request(ctx) -> TaskResult:
    """
    REST request handler.

    Expected ctx.task_params example:
      {
        "connection": {
          "timeout_sec": 60,
          "headers": {"Accept": "application/json"},
          "auth": {"type": "bearer", "token": "{{ keyvault(...) }}"},
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
            "headers": {"X-Request-Id": "{{rundate}}"},
            "params": {"date": "{{rundate}}"},
            "body": {"type": "json", "value": {"include_metadata": true}},
            "file": "raw/report_{{rundate}}.json"
          }
        ]
      }

    Behavior notes:
    - Merges headers as {**connection.headers, **built_in_auth_headers, **request.headers}.
    - Adds request.params to URL query parameters (request wins).
    - Calls response.raise_for_status() before any optional file write
      (note: requests does not raise for 3xx responses).
    - Writes response bytes to ctx.inbox/<file> only when file is provided.
    - Retries on request exceptions and configured HTTP status codes.
    """
    start_time = time.time()
    task_param = getattr(ctx, "task_params", None) or {}

    connection_cfg = task_param.get("connection") or {}
    connection_headers = connection_cfg.get("headers") or {}

    # Connection-level defaults for timeouts and retry behavior.
    timeout_sec = connection_cfg.get("timeout_sec")
    connection_fault = connection_cfg.get("fault_tolerant") or {}
    connection_retries = int(connection_fault.get("retries", 0) or 0)
    connection_retry_interval = int(connection_fault.get("retry_interval_sec", 0) or 0)
    connection_retry_statuses = [int(code) for code in connection_fault.get("retry_on_status") or []]

    request_specs = task_param.get("requests") or []

    inbox: Optional[Path] = None
    if any("file" in spec for spec in request_specs):
        if not getattr(ctx, "inbox", None):
            raise RuntimeError("ctx.inbox is not set.")
        inbox = Path(ctx.inbox)
        inbox.mkdir(parents=True, exist_ok=True)

    outputs: List[Dict[str, Any]] = []

    session = requests.Session()
    logger.info(
        "REST request handler starting task_id=%s request_count=%s timeout_sec=%s",
        ctx.task_id or "",
        len(request_specs),
        timeout_sec,
    )
    logger.debug(
        "REST connection defaults task_id=%s retries=%s retry_interval_sec=%s retry_on_status=%s",
        ctx.task_id or "",
        connection_retries,
        connection_retry_interval,
        connection_retry_statuses,
    )
    logger.debug("REST connection headers (redacted): %s", _redact_headers(connection_headers))

    auth_cfg = connection_cfg.get("auth") or {"type": "none"}
    auth_headers = _resolve_connection_auth_headers(ctx, session, auth_cfg, timeout_sec)
    resolved_connection_headers = {**connection_headers, **auth_headers}
    logger.debug("REST built-in auth headers (redacted): %s", _redact_headers(auth_headers))

    for idx, spec in enumerate(request_specs):
        request_id = spec.get("id") or f"request_{idx + 1}"
        method = (spec.get("method") or "GET").upper()
        url = spec.get("url")

        # Merge per-request headers on top of the connection headers.
        request_headers = spec.get("headers") or {}
        effective_headers = {**resolved_connection_headers, **request_headers}

        # Merge URL params from the spec into the URL.
        request_params = spec.get("params") or {}
        effective_url = _merge_params(url, request_params) if request_params else url
        log_url = _redact_url(effective_url)

        file_rel: Optional[str] = None
        if "file" in spec:
            if inbox is None:
                raise RuntimeError("ctx.inbox is not set.")
            file_rel = _normalize_inbox_rel_path(inbox, spec.get("file"))

        logger.info(
            "Preparing REST request %s method=%s url=%s file=%s",
            request_id,
            method,
            log_url,
            file_rel or "<none>",
        )
        logger.debug(
            "REST request %s resolved params=%s request_headers=%s",
            request_id,
            request_params,
            _redact_headers(request_headers),
        )

        # Apply per-request retry behavior overrides.
        req_fault = spec.get("fault_tolerant") or {}
        retries = int(req_fault.get("retries", connection_retries) or connection_retries)
        retry_interval = int(
            req_fault.get("retry_interval_sec", connection_retry_interval) or connection_retry_interval
        )
        retry_statuses = req_fault.get("retry_on_status", connection_retry_statuses) or []
        retry_statuses = [int(code) for code in retry_statuses]

        request_kwargs: Dict[str, Any] = {"headers": effective_headers}
        if timeout_sec is not None:
            request_kwargs["timeout"] = timeout_sec
        request_kwargs.update(_build_request_body_kwargs(request_id, spec.get("body")))
        logger.debug(
            "REST request %s effective retry config retries=%s retry_interval_sec=%s retry_on_status=%s timeout_sec=%s",
            request_id,
            retries,
            retry_interval,
            retry_statuses,
            timeout_sec,
        )
        logger.debug(
            "REST request %s effective headers (redacted): %s",
            request_id,
            _redact_headers(effective_headers),
        )

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

                output_entry = {
                    "src": {"type": "file", "path": effective_url, "size": response_bytes},
                    "dest": {"type": "file", "path": "", "size": response_bytes},
                    "duration_sec": round(time.perf_counter() - request_start, 3),
                }

                if file_rel is not None:
                    # Persist the successful response to the inbox.
                    local_path = inbox / file_rel
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(local_path, "wb") as handle:
                        handle.write(response.content)

                    output_entry["dest"] = {"type": "file", "path": str(local_path), "size": response_bytes}
                    logger.debug("REST request %s wrote response to %s", request_id, local_path)
                else:
                    logger.info("REST request %s completed without file output", request_id)

                logger.debug("REST request %s output_entry=%s", request_id, output_entry)
                outputs.append(output_entry)
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

    logger.info(
        "REST request handler completed task_id=%s success_count=%s total_duration_sec=%s",
        ctx.task_id or "",
        len(outputs),
        round(time.time() - start_time, 3),
    )

    handler_name = getattr(ctx, "task_handler", None) or "oedis.handler.rest.request"
    return TaskResult(
        task_id=ctx.task_id or "",
        handler=handler_name,
        status="success",
        outputs=outputs,
        metrics={"total_items": len(outputs), "total_duration_sec": round(time.time() - start_time, 3)},
    )
