# AMQ user guide

This guide documents the AMQP 1.0 handlers implemented with Apache Qpid Proton:

- `oedis.handler.amq.send`
- `oedis.handler.amq.receive`

The current implementation is intentionally narrow:

- AMQP 1.0 over TLS only
- `amqps://` URI only
- mutual TLS client certificate authentication only
- `requests` array processed sequentially
- no JSON schema file yet; validation happens in the handler at runtime

## Runtime dependency

The Databricks cluster bootstrap installs `python-qpid-proton` in `cluster/init.sh`.

## Connection config

Both handlers use the same `connection` shape:

```json
{
  "connection": {
    "uri": "amqps://broker.example.com:443",
    "timeout_sec": 60,
    "auth": {
      "type": "client_certificate",
      "certificate": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_client_certificate\") }}",
      "private_key": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_client_private_key\") }}",
      "private_key_passphrase": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_client_private_key_passphrase\") }}",
      "ca_certificate": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_ca_certificate\") }}"
    },
    "fault_tolerant": {
      "retries": 3,
      "retry_interval_sec": 30
    }
  }
}
```

Field notes:

- `uri`: AMQP broker endpoint. Must use `amqps://` and include a hostname. Port is part of the URI, for example `:443`.
- `timeout_sec`: connection and blocking operation timeout passed to Qpid Proton.
- `auth.certificate`, `auth.private_key`, `auth.ca_certificate`: PEM text is expected. Base64-encoded PEM text is also accepted.
- `fault_tolerant`: currently applies to opening the AMQP connection. It does not retry an already-started send or a partially-processed receive batch.

## Send handler

Handler path:

```json
"handler": "oedis.handler.amq.send"
```

Task params:

```json
{
  "connection": {
    "uri": "amqps://broker.example.com:443",
    "timeout_sec": 60,
    "auth": {
      "type": "client_certificate",
      "certificate": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_client_certificate\") }}",
      "private_key": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_client_private_key\") }}",
      "private_key_passphrase": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_client_private_key_passphrase\") }}",
      "ca_certificate": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_ca_certificate\") }}"
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
```

Send behavior:

- Requests are processed sequentially under one AMQP connection.
- `requests[*].queue` is used as the AMQP target address.
- `requests[*].body` must be a string.
- `requests[*].properties` is optional and must be an object when provided.
- `requests[*].properties` is sent as AMQP application properties, which many brokers and clients expose as custom message headers.
- Successful output records one `message -> queue` transfer in `TaskResult.outputs` for each request item.

## Receive handler

Handler path:

```json
"handler": "oedis.handler.amq.receive"
```

Task params:

```json
{
  "connection": {
    "uri": "amqps://broker.example.com:443",
    "timeout_sec": 60,
    "auth": {
      "type": "client_certificate",
      "certificate": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_client_certificate\") }}",
      "private_key": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_client_private_key\") }}",
      "private_key_passphrase": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_client_private_key_passphrase\") }}",
      "ca_certificate": "{{ keyvault(scope=\"dis-secrets\", key=\"amq_ca_certificate\") }}"
    },
    "fault_tolerant": {
      "retries": 3,
      "retry_interval_sec": 30
    }
  },
  "requests": [
    {
      "queue": "ORDERS.IN",
      "max_messages": 10,
      "wait_timeout_sec": 30,
      "file": "raw/orders_in_{{ runtime.job_run_id }}.dat"
    }
  ]
}
```

Receive behavior:

- Requests are processed sequentially under one AMQP connection.
- `requests[*].queue` is used as the AMQP source address.
- `requests[*].max_messages` controls the maximum number of messages read for that request item.
- `requests[*].wait_timeout_sec` is treated as a total wait budget for that request item, not a per-message wait budget.
- When no messages arrive before the timeout expires, the request item finishes successfully with zero outputs.
- A received message is accepted only after its payload has been written successfully.
- If processing the current message fails, the handler attempts to release that message before raising the error.

## Receive file output

The receive handler writes payloads under `ctx.inbox`.

- When `max_messages` is `1`, `requests[*].file` is used exactly as provided.
- When `max_messages` is greater than `1`, the handler appends a 4-digit sequence before the file extension.
- Numbering restarts for each request item.

Examples:

- `raw/orders_in_{{ runtime.job_run_id }}.dat` -> `raw/orders_in_<job_run_id>_0001.dat`
- `raw/orders_in_{{ runtime.job_run_id }}.dat` -> `raw/orders_in_<job_run_id>_0002.dat`
- `raw/orders_{{ runtime.job_run_id }}.json` -> `raw/orders_<job_run_id>_0001.json`

## Receive payload serialization

The handler preserves binary payloads when Proton returns `bytes`, `bytearray`, or `memoryview`.

For non-binary AMQP bodies:

- `str` values are written as UTF-8 bytes
- `dict`, `list`, `tuple`, `int`, `float`, and `bool` values are JSON-serialized to UTF-8 bytes
- `null`/`None` is written as an empty file

This keeps the receive side usable for both text-like and binary payloads without adding an `encoding` option yet.

## Sample configs

See the example task configs:

- `examples/amq_send_basic.json`
- `examples/amq_receive_basic.json`
