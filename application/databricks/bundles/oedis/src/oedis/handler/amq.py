from __future__ import annotations

import base64
import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from oedis.common.utils import json_dumps
from oedis.handler.lib.utils import resolve_inbox_relative_path
from oedis.task_result import TaskResult

logger = logging.getLogger(__name__)


class UnsupportedAuthTypeError(RuntimeError):
    pass


@dataclass(frozen=True)
class _FaultTolerance:
    retries: int = 0
    retry_interval_sec: int = 0


@dataclass(frozen=True)
class _ProtonBindings:
    BlockingConnection: Any
    Message: Any
    SSLDomain: Any
    Timeout: Any


@dataclass
class _TlsFiles:
    temp_dir: tempfile.TemporaryDirectory[str]
    certificate_file: str
    private_key_file: str
    ca_certificate_file: Optional[str]

    def cleanup(self) -> None:
        self.temp_dir.cleanup()


def _load_proton() -> _ProtonBindings:
    try:
        from proton import Message, SSLDomain, Timeout
        from proton.utils import BlockingConnection
    except ImportError as exc:
        raise RuntimeError(
            "AMQ handler requires python-qpid-proton to be installed on the cluster."
        ) from exc

    return _ProtonBindings(
        BlockingConnection=BlockingConnection,
        Message=Message,
        SSLDomain=SSLDomain,
        Timeout=Timeout,
    )


def _require_str(cfg: Dict[str, Any], field_name: str) -> str:
    value = cfg.get(field_name)
    if value is None or str(value).strip() == "":
        raise ValueError(f"{field_name} is required")
    return str(value)


def _require_request_value(cfg: Dict[str, Any], field_name: str) -> Any:
    if field_name not in cfg:
        raise ValueError(f"{field_name} is required")
    return cfg[field_name]


def _parse_optional_float(value: Any, field_name: str) -> Optional[float]:
    if value in (None, ""):
        return None
    parsed = float(value)
    if parsed < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
    return parsed


def _parse_optional_int(value: Any, field_name: str, *, minimum: int = 0) -> Optional[int]:
    if value in (None, ""):
        return None
    parsed = int(value)
    if parsed < minimum:
        raise ValueError(f"{field_name} must be greater than or equal to {minimum}")
    return parsed


def _resolve_fault_tolerance(connection_cfg: Dict[str, Any]) -> _FaultTolerance:
    fault_tolerant = connection_cfg.get("fault_tolerant") or {}
    retries = _parse_optional_int(fault_tolerant.get("retries"), "connection.fault_tolerant.retries", minimum=0) or 0
    retry_interval_sec = (
        _parse_optional_int(
            fault_tolerant.get("retry_interval_sec"),
            "connection.fault_tolerant.retry_interval_sec",
            minimum=0,
        )
        or 0
    )
    return _FaultTolerance(retries=retries, retry_interval_sec=retry_interval_sec)


def _normalize_requests(task_param: Dict[str, Any], handler_name: str) -> list[Dict[str, Any]]:
    requests_cfg = task_param.get("requests")
    if requests_cfg is None:
        raise ValueError(f"{handler_name} requires params.requests")

    if not isinstance(requests_cfg, list) or not requests_cfg:
        raise ValueError("requests must be a non-empty array")

    normalized_requests: list[Dict[str, Any]] = []
    for index, request in enumerate(requests_cfg):
        if not isinstance(request, dict):
            raise ValueError(f"requests[{index}] must be an object")
        normalized_requests.append(request)
    return normalized_requests


def _request_label(request_cfg: Dict[str, Any], index: int) -> str:
    request_id = request_cfg.get("id")
    if request_id is not None and str(request_id).strip():
        return str(request_id)
    return f"request_{index + 1}"


def _resolve_receive_file_rel(
    inbox: Path,
    file_path: Any,
    index: int,
    *,
    batched: bool,
    field_name: str,
) -> str:
    base_rel = resolve_inbox_relative_path(inbox, file_path, field_name=field_name)
    if not batched:
        return base_rel

    path = PurePosixPath(base_rel)
    suffix = "".join(path.suffixes)
    stem = path.name[: -len(suffix)] if suffix else path.name
    numbered_name = f"{stem}_{index:04d}{suffix}"
    parent_parts = path.parts[:-1]
    if parent_parts:
        return PurePosixPath(*parent_parts, numbered_name).as_posix()
    return numbered_name


def _decode_text_secret(value: str, field_name: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError(f"{field_name} is required")
    if "-----BEGIN" in candidate:
        return candidate

    try:
        decoded = base64.b64decode(candidate).decode("utf-8")
    except Exception:
        return candidate

    return decoded or candidate


def _write_tls_material(auth_cfg: Dict[str, Any]) -> _TlsFiles:
    temp_dir = tempfile.TemporaryDirectory(prefix="oedis-amq-")
    base_path = Path(temp_dir.name)

    certificate_text = _decode_text_secret(
        _require_str(auth_cfg, "certificate"),
        "connection.auth.certificate",
    )
    private_key_text = _decode_text_secret(
        _require_str(auth_cfg, "private_key"),
        "connection.auth.private_key",
    )

    certificate_file = base_path / "client-cert.pem"
    private_key_file = base_path / "client-key.pem"
    certificate_file.write_text(certificate_text, encoding="utf-8")
    private_key_file.write_text(private_key_text, encoding="utf-8")

    ca_certificate_file: Optional[str] = None
    ca_certificate_value = auth_cfg.get("ca_certificate")
    if ca_certificate_value not in (None, ""):
        ca_certificate_text = _decode_text_secret(str(ca_certificate_value), "connection.auth.ca_certificate")
        ca_path = base_path / "ca-cert.pem"
        ca_path.write_text(ca_certificate_text, encoding="utf-8")
        ca_certificate_file = str(ca_path)

    return _TlsFiles(
        temp_dir=temp_dir,
        certificate_file=str(certificate_file),
        private_key_file=str(private_key_file),
        ca_certificate_file=ca_certificate_file,
    )


def _build_ssl_domain(connection_cfg: Dict[str, Any], proton: _ProtonBindings) -> tuple[Any, _TlsFiles]:
    auth_cfg = connection_cfg.get("auth") or {}
    auth_type = str(auth_cfg.get("type") or "").strip().lower()
    if auth_type != "client_certificate":
        raise UnsupportedAuthTypeError(
            f'Unsupported auth type: {auth_type or "<missing>"}. Expected connection.auth.type="client_certificate".'
        )

    if not proton.SSLDomain.present():
        raise RuntimeError("Qpid Proton SSL support is unavailable in the current runtime.")

    tls_files = None
    try:
        tls_files = _write_tls_material(auth_cfg)
        ssl_domain = proton.SSLDomain(proton.SSLDomain.MODE_CLIENT)
        ssl_domain.set_credentials(
            tls_files.certificate_file,
            tls_files.private_key_file,
            auth_cfg.get("private_key_passphrase"),
        )
        if tls_files.ca_certificate_file:
            ssl_domain.set_trusted_ca_db(tls_files.ca_certificate_file)
            ssl_domain.set_peer_authentication(
                proton.SSLDomain.VERIFY_PEER_NAME,
                tls_files.ca_certificate_file,
            )
        else:
            ssl_domain.set_peer_authentication(proton.SSLDomain.VERIFY_PEER_NAME)
        return ssl_domain, tls_files
    except Exception:
        if tls_files is not None:
            tls_files.cleanup()
        raise


def _validate_connection_uri(connection_cfg: Dict[str, Any]) -> str:
    uri = _require_str(connection_cfg, "uri")
    parsed = urlparse(uri)
    if parsed.scheme.lower() != "amqps":
        raise ValueError("connection.uri must use the amqps:// scheme for AMQP 1.0 over TLS.")
    if not parsed.hostname:
        raise ValueError("connection.uri must include a hostname.")
    return uri


def _connection_log_parts(connection_cfg: Dict[str, Any]) -> Dict[str, Any]:
    uri = _require_str(connection_cfg, "uri")
    parsed = urlparse(uri)
    return {
        "hostname": parsed.hostname,
        "port": parsed.port,
        "timeout_sec": connection_cfg.get("timeout_sec"),
    }


def _open_connection_with_retries(
    *,
    connection_cfg: Dict[str, Any],
    fault_tolerance: _FaultTolerance,
    open_connection: Callable[[], tuple[Any, _TlsFiles]],
    action_label: str,
) -> tuple[Any, _TlsFiles]:
    attempt = 0
    while True:
        try:
            logger.info(
                "Opening AMQP connection action=%s attempt=%s uri=%s",
                action_label,
                attempt + 1,
                _require_str(connection_cfg, "uri"),
            )
            return open_connection()
        except Exception as exc:
            if attempt >= fault_tolerance.retries:
                raise RuntimeError(
                    f"Failed to open AMQP connection for {action_label}: {exc}"
                ) from exc
            attempt += 1
            logger.warning(
                "Retrying AMQP connection action=%s after %s (attempt %s/%s)",
                action_label,
                exc.__class__.__name__,
                attempt,
                fault_tolerance.retries,
            )
            time.sleep(fault_tolerance.retry_interval_sec)


def _create_blocking_connection(connection_cfg: Dict[str, Any], proton: _ProtonBindings) -> tuple[Any, _TlsFiles]:
    uri = _validate_connection_uri(connection_cfg)
    timeout_sec = _parse_optional_float(connection_cfg.get("timeout_sec"), "connection.timeout_sec")
    ssl_domain, tls_files = _build_ssl_domain(connection_cfg, proton)
    try:
        connection = proton.BlockingConnection(
            url=uri,
            timeout=timeout_sec,
            ssl_domain=ssl_domain,
        )
    except Exception:
        tls_files.cleanup()
        raise
    return connection, tls_files


def _serialize_message_body(body: Any) -> bytes:
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, bytearray):
        return bytes(body)
    if isinstance(body, memoryview):
        return body.tobytes()
    if isinstance(body, str):
        return body.encode("utf-8")
    if isinstance(body, (dict, list, tuple, int, float, bool)):
        return json_dumps(body).encode("utf-8")
    return str(body).encode("utf-8")


def _close_safely(resource: Any, resource_name: str) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except Exception as exc:
        logger.warning("Failed to close %s cleanly: %s", resource_name, exc)


def send(ctx) -> TaskResult:
    """
    Send one or more AMQP 1.0 messages over TLS with mutual TLS authentication.

    Expected ctx.task_params example:
      {
        "connection": {
          "uri": "amqps://broker.example.com:443",
          "timeout_sec": 60,
          "auth": {
            "type": "client_certificate",
            "certificate": "{{ keyvault(...) }}",
            "private_key": "{{ keyvault(...) }}",
            "private_key_passphrase": "{{ keyvault(...) }}",
            "ca_certificate": "{{ keyvault(...) }}"
          },
          "fault_tolerant": {
            "retries": 3,
            "retry_interval_sec": 30
          }
        },
        "requests": [
          {
            "queue": "ORDERS.OUT",
            "body": "business_date={{ rundate }}",
            "properties": {
              "message_type": "order",
              "business_date": "{{ rundate }}"
            }
          }
        ]
      }
    """
    start_time = time.perf_counter()
    task_param = getattr(ctx, "task_params", None) or {}
    connection_cfg = task_param.get("connection") or {}
    request_cfgs = _normalize_requests(task_param, "oedis.handler.amq.send")

    proton = _load_proton()
    uri = _validate_connection_uri(connection_cfg)
    fault_tolerance = _resolve_fault_tolerance(connection_cfg)
    timeout_sec = _parse_optional_float(connection_cfg.get("timeout_sec"), "connection.timeout_sec")
    logger.info(
        "AMQ send handler starting task_id=%s request_count=%s uri=%s timeout_sec=%s",
        getattr(ctx, "task_id", None),
        len(request_cfgs),
        uri,
        timeout_sec,
    )
    logger.debug("AMQ send connection config: %s", _connection_log_parts(connection_cfg))

    connection = None
    tls_files = None
    outputs: list[dict[str, Any]] = []
    total_bytes = 0
    try:
        connection, tls_files = _open_connection_with_retries(
            connection_cfg=connection_cfg,
            fault_tolerance=fault_tolerance,
            open_connection=lambda: _create_blocking_connection(connection_cfg, proton),
            action_label="send",
        )

        for request_index, request_cfg in enumerate(request_cfgs):
            request_label = _request_label(request_cfg, request_index)
            queue = _require_str(request_cfg, f"requests[{request_index}].queue")
            body = _require_request_value(request_cfg, "body")
            if not isinstance(body, str):
                raise ValueError(f"requests[{request_index}].body must be a string for oedis.handler.amq.send")

            properties = request_cfg.get("properties")
            if properties is not None and not isinstance(properties, dict):
                raise ValueError(f"requests[{request_index}].properties must be an object/dict when provided")

            logger.info(
                "Preparing AMQ send request=%s queue=%s property_key_count=%s",
                request_label,
                queue,
                len(properties or {}),
            )
            logger.debug(
                "AMQ send request=%s property keys=%s",
                request_label,
                sorted(str(key) for key in properties.keys()) if properties else [],
            )

            sender = None
            request_start = time.perf_counter()
            try:
                sender = connection.create_sender(queue)
                message_kwargs: dict[str, Any] = {"body": body}
                if properties:
                    message_kwargs["properties"] = properties
                message = proton.Message(**message_kwargs)
                sender.send(message, timeout=timeout_sec if timeout_sec is not None else False)

                payload_size = len(body.encode("utf-8"))
                total_bytes += payload_size
                output_entry = {
                    "src": {"type": "message", "path": "inline", "size": payload_size},
                    "dest": {"type": "queue", "path": queue, "size": payload_size},
                    "duration_sec": round(time.perf_counter() - request_start, 3),
                }
                outputs.append(output_entry)
                logger.info(
                    "AMQ send request=%s completed queue=%s bytes=%s",
                    request_label,
                    queue,
                    payload_size,
                )
            finally:
                _close_safely(sender, f"AMQP sender {request_label}")

        total_duration = round(time.perf_counter() - start_time, 3)
        logger.info(
            "AMQ send handler completed task_id=%s request_count=%s total_bytes=%s duration_sec=%s",
            getattr(ctx, "task_id", None),
            len(outputs),
            total_bytes,
            total_duration,
        )
        return TaskResult(
            task_id=ctx.task_id or "",
            handler=getattr(ctx, "task_handler", None) or "oedis.handler.amq.send",
            status="success",
            outputs=outputs,
            metrics={
                "total_items": len(outputs),
                "total_bytes": total_bytes,
                "total_duration_sec": total_duration,
            },
        )
    finally:
        _close_safely(connection, "AMQP connection")
        if tls_files is not None:
            tls_files.cleanup()


def receive(ctx) -> TaskResult:
    """
    Receive one or more AMQP 1.0 messages over TLS with mutual TLS authentication.

    Expected ctx.task_params example:
      {
        "connection": {
          "uri": "amqps://broker.example.com:443",
          "timeout_sec": 60,
          "auth": {
            "type": "client_certificate",
            "certificate": "{{ keyvault(...) }}",
            "private_key": "{{ keyvault(...) }}",
            "private_key_passphrase": "{{ keyvault(...) }}",
            "ca_certificate": "{{ keyvault(...) }}"
          }
        },
        "requests": [
          {
            "queue": "ORDERS.IN",
            "max_messages": 10,
            "wait_timeout_sec": 30,
            "file": "raw/orders_in.dat"
          }
        ]
      }
    """
    start_time = time.perf_counter()
    task_param = getattr(ctx, "task_params", None) or {}
    connection_cfg = task_param.get("connection") or {}
    request_cfgs = _normalize_requests(task_param, "oedis.handler.amq.receive")

    proton = _load_proton()
    uri = _validate_connection_uri(connection_cfg)

    if not getattr(ctx, "inbox", None):
        raise RuntimeError("ctx.inbox is not set.")
    inbox = Path(ctx.inbox)
    inbox.mkdir(parents=True, exist_ok=True)

    fault_tolerance = _resolve_fault_tolerance(connection_cfg)
    timeout_sec = _parse_optional_float(connection_cfg.get("timeout_sec"), "connection.timeout_sec")
    logger.info(
        "AMQ receive handler starting task_id=%s request_count=%s uri=%s timeout_sec=%s",
        getattr(ctx, "task_id", None),
        len(request_cfgs),
        uri,
        timeout_sec,
    )
    logger.debug("AMQ receive connection config: %s", _connection_log_parts(connection_cfg))

    connection = None
    tls_files = None
    outputs: list[dict[str, Any]] = []
    total_bytes = 0

    try:
        connection, tls_files = _open_connection_with_retries(
            connection_cfg=connection_cfg,
            fault_tolerance=fault_tolerance,
            open_connection=lambda: _create_blocking_connection(connection_cfg, proton),
            action_label="receive",
        )

        for request_index, request_cfg in enumerate(request_cfgs):
            request_label = _request_label(request_cfg, request_index)
            queue = _require_str(request_cfg, f"requests[{request_index}].queue")
            max_messages = _parse_optional_int(
                request_cfg.get("max_messages"),
                f"requests[{request_index}].max_messages",
                minimum=1,
            ) or 1
            wait_timeout_sec = _parse_optional_float(
                request_cfg.get("wait_timeout_sec"),
                f"requests[{request_index}].wait_timeout_sec",
            )
            if wait_timeout_sec is None:
                wait_timeout_sec = _parse_optional_float(connection_cfg.get("timeout_sec"), "connection.timeout_sec") or 30.0

            _require_request_value(request_cfg, "file")
            batched = max_messages > 1
            receive_deadline = time.monotonic() + wait_timeout_sec
            logger.info(
                "Preparing AMQ receive request=%s queue=%s max_messages=%s wait_timeout_sec=%s",
                request_label,
                queue,
                max_messages,
                wait_timeout_sec,
            )

            receiver = None
            try:
                receiver = connection.create_receiver(queue, credit=max_messages)
                logger.info(
                    "AMQ receiver opened request=%s queue=%s credit=%s timeout_sec=%s",
                    request_label,
                    queue,
                    max_messages,
                    timeout_sec,
                )

                request_received = 0
                for item_index in range(1, max_messages + 1):
                    remaining_timeout = max(receive_deadline - time.monotonic(), 0.0)
                    try:
                        message = receiver.receive(timeout=remaining_timeout)
                    except proton.Timeout:
                        logger.info(
                            "AMQ receive request=%s timed out queue=%s received_count=%s",
                            request_label,
                            queue,
                            request_received,
                        )
                        break

                    item_start = time.perf_counter()
                    try:
                        payload = _serialize_message_body(getattr(message, "body", None))
                        file_rel = _resolve_receive_file_rel(
                            inbox,
                            request_cfg.get("file"),
                            item_index,
                            batched=batched,
                            field_name=f"requests[{request_index}].file",
                        )
                        local_path = inbox / file_rel
                        local_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(local_path, "wb") as handle:
                            handle.write(payload)

                        receiver.accept()
                        payload_size = len(payload)
                        request_received += 1
                        total_bytes += payload_size
                        output_entry = {
                            "src": {"type": "queue", "path": queue, "size": payload_size},
                            "dest": {"type": "file", "path": str(local_path), "size": payload_size},
                            "duration_sec": round(time.perf_counter() - item_start, 3),
                        }
                        outputs.append(output_entry)
                        logger.info(
                            "AMQ received message request=%s queue=%s file=%s bytes=%s",
                            request_label,
                            queue,
                            local_path,
                            payload_size,
                        )
                    except Exception:
                        try:
                            receiver.release()
                        except Exception as release_exc:
                            logger.warning("Failed to release AMQ message after processing error: %s", release_exc)
                        raise

                logger.info(
                    "AMQ receive request=%s completed queue=%s received_count=%s",
                    request_label,
                    queue,
                    request_received,
                )
            finally:
                _close_safely(receiver, f"AMQP receiver {request_label}")

        total_duration = round(time.perf_counter() - start_time, 3)
        logger.info(
            "AMQ receive handler completed task_id=%s message_count=%s total_bytes=%s duration_sec=%s",
            getattr(ctx, "task_id", None),
            len(outputs),
            total_bytes,
            total_duration,
        )
        return TaskResult(
            task_id=ctx.task_id or "",
            handler=getattr(ctx, "task_handler", None) or "oedis.handler.amq.receive",
            status="success",
            outputs=outputs,
            metrics={
                "total_items": len(outputs),
                "total_bytes": total_bytes,
                "total_duration_sec": total_duration,
            },
        )
    finally:
        _close_safely(connection, "AMQP connection")
        if tls_files is not None:
            tls_files.cleanup()
