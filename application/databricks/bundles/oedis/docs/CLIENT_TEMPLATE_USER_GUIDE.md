# DIS Client Template User Guide

## Overview

The DIS client supports **client-side template rendering** before submitting a config to the DIS service.

This lets you:

- keep a reusable config template in code
- inject environment-specific (catalog name) or run-specific values (run date or file name etc.) on the client side
- leave DIS runtime macros for the server side

The client template flow is intentionally **two-phase**:

| Phase | Where | Syntax | Purpose |
|---|---|---|---|
| 1 | Client (`common`) | `[[ ... ]]` | Inject input parameters before submission |
| 2 | Server (`oedis`) | `{{ ... }}` | Resolve runtime values and macros during DIS execution |

In short:

- use `[[ ... ]]` for values known before calling DIS
- use `{{ ... }}` for values resolved by DIS at runtime

---

## How it works

The client-side template logic is implemented in:

```python
DisClient.resolve_config_template(
    config_template: str,
    template_params: dict[str, Any] | None = None,
) -> str
```

It uses Jinja with custom delimiters:

- variables: `[[ ... ]]`
- blocks: `[% ... %]`
- comments: `[# ... #]`

The client uses `StrictUndefined`, so missing parameters fail immediately instead of silently rendering empty values.

After rendering, the resolved config must still be a valid JSON string. That resolved JSON is then submitted through:

```python
DisClient.ingest(...)
```

---

## Template module pattern

A typical client-side template package contains 3 objects:

### `config_template`
A JSON template string containing `[[ ... ]]` placeholders.

### `config_params`
A dictionary used to resolve the client-side placeholders.

### `service_params`
Optional runtime parameters sent separately to DIS as job parameters.
These are not substituted directly into `config_template` unless you explicitly place them into `config_params`.

---

## Basic usage

```python
resolved_config = client.resolve_config_template(
    config_template=config_template,
    template_params=config_params,
)

result = client.ingest(
    config=resolved_config,
    params=service_params,
    wait=True,
)
```

The flow is:

1. define `config_template`
2. define `config_params`
3. resolve the template on the client
4. submit the resolved JSON to DIS
5. let DIS resolve any remaining `{{ ... }}` expressions at runtime

---

## Example

### Template

```python
config_template = """
{
  "pipeline": "corra",
  "tasks": [
    {
      "id": "download",
      "handler": "oedis.handler.database.download",
      "params": {
        "requests": [
          {
            "id": "[[ database1.source_table ]]",
            "select_sql": "SELECT * FROM [[ database1.source_table ]]",
            "format": "parquet"
          }
        ]
      }
    },
    {
      "id": "upload",
      "handler": "oedis.handler.database.upload",
      "params": {
        "requests": [
          {
            "id": "[[ database1.source_table ]]",
            "table": "[[ database1.target_table ]]",
            "select_sql": "select * from {source}",
            "create_sql": "CREATE TABLE IF NOT EXISTS [[ database1.target_table ]] (\\n{columns}\\n) USING DELTA",
            "write_options": {
              "mode": "overwrite"
            }
          }
        ]
      }
    }
  ]
}
""".strip()
```

### Parameters

```python
config_params = {
    "database1": {
        "source_table": "dev_ext_ora_habs.gsmadmin_internal.duplicated_table",
        "target_table": "dev_application_cat.oedisint_db.duplicated_table",
    }
}

service_params = {}
```

### Resolved output

For example:

```json
"table": "[[ database1.target_table ]]"
```

becomes:

```json
"table": "dev_application_cat.oedisint_db.duplicated_table"
```

---

## Parameter referencing rules

Because the client passes the parameter dictionary directly into the Jinja renderer, keys in `config_params` become top-level template variables.

Example:

```python
config_params = {
    "env": "dev",
    "source": {
        "host": "sftp.example.com",
        "port": 22,
    },
}
```

You can reference them like this:

```json
{
  "env": "[[ env ]]",
  "host": "[[ source.host ]]",
  "port": "[[ source.port ]]"
}
```

Nested dictionaries are supported through dotted access.

---

## `config_params` vs `service_params`

Use `config_params` when a value must be injected directly into the config before submission.

Typical examples:

- table names
- file paths
- folder names
- host names
- usernames
- environment labels

Use `service_params` when a value should be sent to DIS as supplemental runtime input instead of being substituted directly into the config.

Typical examples:

- runtime switches
- metadata for the job
- values referenced later through DIS runtime parameters

Rule of thumb:

- if it appears inside `[[ ... ]]`, put it in `config_params`
- if it should travel with the run but not be substituted into the config text, put it in `service_params`

---

## Two-phase templating

### Phase 1: client-side rendering with `[[ ... ]]`

This happens in `common` before the config is submitted.

Good examples:

```jinja
[[ env ]]
[[ source.host ]]
[[ database1.target_table ]]
```

Use this phase for plain parameter substitution only.

### Phase 2: server-side rendering with `{{ ... }}`

This happens later inside DIS.

Good examples:

```jinja
{{ runtime.rundate }}
{{ runtime.job_params.batch }}
{{ delta(base=runtime.rundate, days=-1, format='YYYYMMDD') }}
{{ keyvault(scope='my-scope', key='my-key') }}
```

Use this phase for:

- runtime values
- DIS macros and helper functions
- secret lookup
- date logic

---

## Mixing both phases in one config

You can combine both phases in the same config.

Example:

For SFTP key auth, `private_key` may resolve to a raw PEM string or a base64-encoded PEM string.

```json
{
  "private_key": "{{ keyvault(scope='[[ kv.scope ]]', key='[[ kv.private_key_name ]]') }}",
  "remote_path": "/out/{{ delta(base=runtime.rundate, days=-1, format='YYYYMMDD') }}/"
}
```

Client-side rendering resolves only the `[[ ... ]]` parts first.

So DIS receives:

```json
{
  "private_key": "{{ keyvault(scope='dept-a-kv', key='sftp-private-key') }}",
  "remote_path": "/out/{{ delta(base=runtime.rundate, days=-1, format='YYYYMMDD') }}/"
}
```

Then DIS resolves the remaining `{{ ... }}` expressions at runtime.

---

## Best practices

### 1. Keep templates JSON-safe

If the rendered value is a string, keep the JSON quotes.

Good:

```json
"table": "[[ database1.target_table ]]"
```

Risky:

```json
"table": [[ database1.target_table ]]
```

### 2. Keep parameter structures clean

Prefer structured dictionaries over flat names.

Good:

```python
config_params = {
    "database1": {
        "source_table": "...",
        "target_table": "..."
    }
}
```

### 3. Separate client and server responsibilities

Use:

- `[[ ... ]]` for client-known values
- `{{ ... }}` for DIS runtime logic

### 4. Resolve before submit

During development, print or log the resolved config before calling `ingest(...)`.

### 5. Fail fast on missing values

Rely on `StrictUndefined` and do not hide missing parameters.

---

## Recommended authoring pattern

```python
config_template = """
{
  "pipeline": "example",
  "tasks": [
    {
      "id": "upload",
      "handler": "oedis.handler.adls.upload",
      "params": {
        "requests": [
          {
            "id": "[[ file.id ]]",
            "path": "[[ file.path ]]"
          }
        ]
      }
    }
  ]
}
""".strip()

config_params = {
    "file": {
        "id": "sample_01",
        "path": "/mnt/input/sample_01.csv",
    }
}

service_params = {
    "batch": "daily"
}
```

Usage:

```python
resolved_config = client.resolve_config_template(
    config_template=config_template,
    template_params=config_params,
)

result = client.ingest(
    config=resolved_config,
    params=service_params,
    wait=True,
)
```

---

## Summary

The DIS client template feature is a thin client-side rendering layer over a JSON config.

- `config_template` holds the template text
- `config_params` holds the values for `[[ ... ]]`
- `service_params` holds extra runtime/job parameters
- `resolve_config_template()` performs the client-side render
- `ingest()` validates and submits the resolved JSON
- DIS later resolves any remaining `{{ ... }}` server-side expressions

This separation keeps the client simple and makes template behavior predictable.
