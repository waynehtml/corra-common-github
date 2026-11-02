from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple
from urllib import error, parse, request

from oedis.common.template_resolver import resolve_task
from oedis.handler.file import _ensure_list, _select_files
from oedis.handler.lib.utils import resolve_inbox_path
from oedis.task_result import TaskResult

logger = logging.getLogger(__name__)

# Microsoft Graph OAuth scope used for client-credentials authentication.
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Token endpoint template for Azure AD / Microsoft identity platform.
GRAPH_TOKEN_URL_TMPL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

# Graph endpoint template for sending mail as a specific sender.
GRAPH_SENDMAIL_URL_TMPL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

# Default total size limit across all attachments in a single email.
DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES = 10 * 1024 * 1024

# Email body templates must live under oedis/template beside the deployed code.
EMAIL_TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "template"


def _resolve_attachment_paths(
    attachment_requests: Sequence[Any],
    inbox: Path,
) -> List[str]:
    """
    Resolve configured attachment requests into a deduplicated list of absolute file paths.

    Parameters
    ----------
    attachment_requests:
        A list-like collection of attachment request objects. Each item is expected
        to be a dict with fields like:
            - src_dir: base directory relative to ctx.inbox
            - include: include glob patterns
            - exclude: exclude glob patterns
            - recursive: whether to search recursively

    inbox:
        The task inbox path from ctx.inbox. Attachment src_dir values are resolved
        relative to this location.

    Returns
    -------
    List[str]
        Absolute file paths, deduplicated in first-seen order.

    Notes
    -----
    - Each request can match one or more files.
    - The helper reuses the common file selection logic from the file handler.
    - Duplicate files across multiple attachment requests are removed.
    """
    resolved: List[str] = []
    seen: set[str] = set()

    # Process each configured attachment request one by one.
    for index, attachment_request in enumerate(attachment_requests):
        # Each attachment request must be a config object.
        if not isinstance(attachment_request, dict):
            raise ValueError(f"message.attachments[{index}] must be an object")

        # Resolve src_dir relative to inbox.
        # If src_dir is omitted, default to the inbox root itself.
        base_dir = resolve_inbox_path(
            inbox,
            attachment_request.get("src_dir"),
            field_name=f"message.attachments[{index}].src_dir",
            default=inbox,
        )

        # Validate the resolved base directory before attempting file selection.
        if not base_dir.exists():
            raise FileNotFoundError(
                f"message.attachments[{index}].src_dir not found: {base_dir}"
            )
        if not base_dir.is_dir():
            raise NotADirectoryError(
                f"message.attachments[{index}].src_dir is not a directory: {base_dir}"
            )

        # Normalize include/exclude into lists for consistent downstream handling.
        include = _ensure_list(attachment_request.get("include"), default=["*"])
        exclude = _ensure_list(attachment_request.get("exclude"), default=[])
        recursive = bool(attachment_request.get("recursive", False))

        logger.debug(
            "Email send attachment request[%d] resolved base_dir=%s include=%s exclude=%s recursive=%s",
            index,
            base_dir,
            include,
            exclude,
            recursive,
        )

        # Select files under the resolved base directory using the common matcher.
        candidates = _select_files(
            base_dir,
            include=include,
            exclude=exclude,
            recursive=recursive,
        )

        # Convert to absolute paths and deduplicate across all requests.
        for candidate in candidates:
            resolved_path = str(candidate.resolve())
            if resolved_path not in seen:
                seen.add(resolved_path)
                resolved.append(resolved_path)

    return resolved


def _select_attachments(
    attachment_paths: Sequence[str],
    max_total_attachment_bytes: int,
) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """
    Convert local files into Microsoft Graph attachment payload objects.

    Parameters
    ----------
    attachment_paths:
        Absolute or local file paths already resolved from attachment config.

    max_total_attachment_bytes:
        Maximum cumulative size allowed across all selected attachments.

    Returns
    -------
    Tuple[List[Dict[str, Any]], List[str], List[str]]
        (
            attachments_to_send,
            missing_or_unreadable_paths,
            omitted_due_to_size_limit_paths
        )

    Notes
    -----
    - Files are read and base64 encoded because Microsoft Graph expects
      contentBytes for file attachments.
    - Files that do not exist or cannot be read are collected in `missing`.
    - Files that would exceed the total size cap are collected in `omitted`.
    """
    total_size = 0
    attachments: List[Dict[str, Any]] = []
    missing: List[str] = []
    omitted: List[str] = []

    # Evaluate each candidate path and decide whether it can be attached.
    for file_path in attachment_paths:
        p = Path(file_path)

        # Skip missing paths or non-file paths.
        if not p.exists() or not p.is_file():
            missing.append(file_path)
            continue

        try:
            # Read the file and encode it for the Graph API.
            file_size = p.stat().st_size
            encoded = base64.b64encode(p.read_bytes()).decode("ascii")
        except Exception:
            # Treat unreadable files the same as missing for reporting purposes.
            missing.append(file_path)
            continue

        # Enforce a total size limit across all selected attachments.
        if total_size + file_size > max_total_attachment_bytes:
            omitted.append(file_path)
            continue

        total_size += file_size

        # Build the payload object expected by Microsoft Graph.
        attachments.append(
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": p.name,
                "contentType": "application/octet-stream",
                "contentBytes": encoded,
            }
        )

    return attachments, missing, omitted


def _graph_request_json(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    """
    Send a POST request and parse the JSON response.

    Parameters
    ----------
    url:
        Target endpoint URL.

    payload:
        Request payload. This function supports either:
        - form-encoded payloads when Content-Type is application/x-www-form-urlencoded
        - JSON payloads for all other content types

    headers:
        HTTP headers to send with the request.

    Returns
    -------
    Dict[str, Any]
        Parsed JSON response body, or {} if the response is empty.

    Raises
    ------
    RuntimeError
        Wrapped HTTP or URL-level network errors with response details.

    Notes
    -----
    This helper is used for both:
    - token acquisition
    - sendMail request
    """
    logger.debug(
        "Graph request starting: url=%s content_type=%s payload_keys=%s",
        url,
        headers.get("Content-Type"),
        sorted(payload.keys()),
    )

    # Choose encoding based on the declared content type.
    req = request.Request(
        url=url,
        data=parse.urlencode(payload).encode("utf-8")
        if headers.get("Content-Type") == "application/x-www-form-urlencoded"
        else json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")

            logger.debug(
                "Graph request completed: url=%s status=%s response_length=%s",
                url,
                getattr(resp, "status", None),
                len(raw),
            )

            # Some successful Graph responses are empty; return {} in that case.
            return json.loads(raw) if raw else {}

    except error.HTTPError as exc:
        # Read the response body so the caller gets a useful error message.
        body = exc.read().decode("utf-8", errors="replace")
        logger.error("Graph HTTP error for %s status=%s body=%s", url, exc.code, body)
        raise RuntimeError(f"Graph HTTP error {exc.code}: {body}") from exc

    except error.URLError as exc:
        logger.error("Graph URL error for %s reason=%s", url, exc.reason)
        raise RuntimeError(f"Graph URL error: {exc.reason}") from exc


def _load_body_template(template_name: str) -> str:
    """
    Load an email body template file from the deployed template directory.

    Parameters
    ----------
    template_name:
        File name only, for example:
            "summary.html"
            "failure_notification.txt"

    Returns
    -------
    str
        Raw template file content.

    Raises
    ------
    ValueError
        If the provided template name is empty, is not a plain file name,
        or the file does not exist.

    Notes
    -----
    Only a file name is allowed, not a relative path.
    This intentionally prevents:
    - nested paths such as "emails/summary.html"
    - traversal attempts such as "../secret.txt"
    """
    if not isinstance(template_name, str) or not template_name.strip():
        raise ValueError("email.send requires message.body.template to be a non-empty file name")

    template_file_name = template_name.strip()

    # Enforce "file name only" to keep template lookup restricted to EMAIL_TEMPLATE_ROOT.
    if Path(template_file_name).name != template_file_name or template_file_name in {".", ".."}:
        raise ValueError("email.send requires message.body.template to be a file name only")

    template_path = EMAIL_TEMPLATE_ROOT / template_file_name

    logger.info("Email send loading body template template=%s root=%s", template_file_name, EMAIL_TEMPLATE_ROOT)
    logger.debug("Email send resolved body template path=%s", template_path)

    if not template_path.is_file():
        raise ValueError(f"email.send body template not found: {template_file_name}")

    return template_path.read_text(encoding="utf-8")


def _get_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """
    Request an app-only Microsoft Graph access token using client credentials flow.

    Parameters
    ----------
    tenant_id:
        Azure AD tenant ID.

    client_id:
        Application / service principal client ID.

    client_secret:
        Application client secret.

    Returns
    -------
    str
        Access token string.

    Raises
    ------
    RuntimeError
        If the token response does not contain access_token.
    """
    token_url = GRAPH_TOKEN_URL_TMPL.format(tenant_id=tenant_id)

    logger.info("Requesting Graph access token for tenant_id=%s client_id=%s", tenant_id, client_id)

    token_payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": GRAPH_SCOPE,
        "grant_type": "client_credentials",
    }

    token_resp = _graph_request_json(
        token_url,
        token_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    token = token_resp.get("access_token")
    if not token:
        raise RuntimeError("Graph token response did not include access_token")

    logger.info("Acquired Graph access token for tenant_id=%s", tenant_id)
    return token


def send(ctx: Any) -> TaskResult:
    """
    Main handler entry point: resolve config, render templates, collect attachments,
    obtain a Graph token, and send the email.

    High-level flow
    ---------------
    1. Read raw task params from ctx.task_params
    2. Build render_context for templating
    3. Resolve templating across the entire task config
    4. Validate required config fields
    5. Resolve body content:
       - either inline message.body.content
       - or load message.body.template from disk, then template it
    6. Resolve attachment files under ctx.inbox
    7. Acquire Microsoft Graph access token
    8. Send the message through Graph sendMail
    9. Return TaskResult

    Templating behavior
    -------------------
    The task config is templated in two stages:

    Stage 1:
        `resolve_task(raw_params, render_context=...)`
        runs against the full task params.
        This means these fields can all be templated:
        - connection/auth values
        - message.from
        - message.to
        - message.subject
        - message.body.content
        - attachment request fields

    Stage 2:
        If message.body.template is used, the template file content is loaded
        from oedis/template/<file> and then templated again with the same
        render_context.

    Available template variables
    ----------------------------
    render_context contains:
        - runtime
        - task_results

    Example ideas
    -------------
        subject: "DIS run {{ runtime.run_id }} completed"
        body.content: "Downloaded {{ task_results.download.metrics.total_items }} files."
    """
    handler_start = time.perf_counter()
    raw_params = ctx.task_params or {}

    logger.info("Email send handler starting task_id=%s", getattr(ctx, "task_id", None))
    logger.info("Email send raw task params: %s", raw_params)

    # Build the template render context.
    #
    # runtime:
    #   per-run execution context made available by the engine
    #
    # task_results:
    #   results from previously executed tasks, which allows email fields
    #   to reference upstream outputs or metrics
    #
    # This is the only data explicitly passed into template resolution here.
    render_context = {
        "runtime": getattr(ctx, "runtime", None),
        "task_results": getattr(ctx, "task_results", {}) or {},
    }

    # Resolve templates across the entire task configuration first.
    #
    # Important:
    # - Inline body content is already templated at this step.
    # - Subject, recipients, sender, and even attachment config may also be templated.
    params = resolve_task(
        raw_params,
        render_context=render_context,
    )

    # Extract the main config sections after templating has been applied.
    connection = params.get("connection") or {}
    auth = connection.get("auth") or {}
    message = params.get("message") or {}

    # Read authentication fields.
    tenant_id = auth.get("tenant_id")
    client_id = auth.get("client_id")
    client_secret = auth.get("client_secret")

    # Read message fields.
    sender = message.get("from")
    recipients = message.get("to") or []
    subject = message.get("subject")
    body = message.get("body") or {}
    body_format = str(body.get("format", "html")).upper()
    body_content = body.get("content")
    body_template = body.get("template")

    # Allow a single recipient string for convenience; normalize to a list.
    if isinstance(recipients, str):
        recipients = [recipients]

    # Validate required auth fields.
    if not tenant_id or not client_id or not client_secret:
        raise ValueError("email.send requires connection.auth.tenant_id/client_id/client_secret")

    # Validate required message fields.
    if not sender:
        raise ValueError("email.send requires message.from")
    if not recipients:
        raise ValueError("email.send requires at least one message.to recipient")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("email.send requires message.subject")
    subject = subject.strip()

    # Exactly one of inline body content or body template must be provided.
    has_body_content = isinstance(body_content, str) and bool(body_content.strip())
    has_body_template = isinstance(body_template, str) and bool(body_template.strip())
    if has_body_content == has_body_template:
        raise ValueError("email.send requires exactly one of message.body.content or message.body.template")

    # Resolve the final body content.
    if has_body_template:
        # Load the template file text from oedis/template/<name>.
        #
        # Then run template resolution on the loaded file content using the same
        # render context. This is the second templating stage mentioned above.
        #
        # This is what allows a deployed HTML/TXT template file to contain
        # placeholders like:
        #   {{ runtime.run_id }}
        #   {{ task_results.download.metrics.total_items }}
        body_content = resolve_task(
            _load_body_template(body_template),
            render_context=render_context,
        )
    else:
        # Inline body content was already templated during resolve_task(raw_params, ...),
        # so here we only normalize whitespace.
        body_content = body_content.strip()

    logger.info(
        "Email send resolved sender=%s recipient_count=%s subject=%s body_format=%s",
        sender,
        len(recipients),
        subject,
        body_format,
    )
    logger.info(
        "Email send resolved body source=%s",
        "template" if has_body_template else "content",
    )

    # Read attachment size cap from the task config, with a sensible default.
    max_total_attachment_bytes = int(
        params.get("max_total_attachment_bytes", DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES)
    )
    logger.debug("Email send max_total_attachment_bytes=%s", max_total_attachment_bytes)

    # Attachments are selected relative to ctx.inbox.
    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")
    inbox = Path(ctx.inbox)

    # Resolve configured attachment requests into actual file paths.
    attachment_candidates = _resolve_attachment_paths(
        message.get("attachments") or [],
        inbox,
    )
    logger.info("Email send resolved %s attachment candidate(s)", len(attachment_candidates))
    logger.debug("Email send attachment candidates: %s", attachment_candidates)

    # Convert the resolved files into Graph attachment payloads,
    # while tracking missing and omitted files for logging.
    attachments, missing_attachments, omitted_attachments = _select_attachments(
        attachment_paths=attachment_candidates,
        max_total_attachment_bytes=max_total_attachment_bytes,
    )
    logger.info(
        "Email send attachments selected=%s missing=%s omitted=%s",
        len(attachments),
        len(missing_attachments),
        len(omitted_attachments),
    )
    if missing_attachments:
        logger.warning("Email send missing attachment(s): %s", missing_attachments)
    if omitted_attachments:
        logger.warning("Email send omitted attachment(s) due to size limit: %s", omitted_attachments)

    # Acquire an OAuth token for Microsoft Graph.
    access_token = _get_access_token(tenant_id, client_id, client_secret)

    # Build the Graph sendMail payload.
    send_mail_payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": body_format, "content": body_content},
            "toRecipients": [{"emailAddress": {"address": recipient}} for recipient in recipients],
            "attachments": attachments,
        },
        "saveToSentItems": True,
    }

    # URL-encode the sender because it becomes part of the endpoint path.
    send_url = GRAPH_SENDMAIL_URL_TMPL.format(sender=parse.quote(sender))

    logger.info(
        "Sending Graph email request sender=%s recipient_count=%s attachment_count=%s save_to_sent_items=%s",
        sender,
        len(recipients),
        len(attachments),
        True,
    )

    # Send the email. A successful sendMail call typically returns an empty body.
    _graph_request_json(
        send_url,
        send_mail_payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )

    logger.info(
        "Email sent with %d attachment candidate(s), %d attachment(s) sent",
        len(attachment_candidates),
        len(attachments),
    )

    # Return standard task result with normalized metrics.
    return TaskResult(
        task_id=ctx.task_id or "send_email",
        handler=ctx.task_handler or "oedis.handler.email.send",
        status="success",
        outputs=[],
        metrics={
            "total_items": 1,
            "total_duration_sec": round(time.perf_counter() - handler_start, 3),
        },
    )