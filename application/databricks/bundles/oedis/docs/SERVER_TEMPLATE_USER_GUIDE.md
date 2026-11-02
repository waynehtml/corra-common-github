# Service-Side Templating Guide

## Overview

The DIS engine supports **server-side templating** using Jinja2 to dynamically resolve configuration at runtime.

Templating is evaluated **on the service side (Databricks job runtime)** after the job starts and before task execution.

This allows configurations to reference:

* Runtime metadata (e.g., run date, job ID)
* Client-passed parameters
* Built-in helper functions (e.g., `delta`, `timestamp`, `epoch`, `keyvault`)

---

## Rendering Flow

At runtime, the engine performs the following:

1. Parse job inputs (`config`, `rundate`, `params`, etc.)
2. Construct a `Runtime` object
3. Render the config using Jinja2:

```python
resolved_config = resolve_config(
    config,
    render_context={
        "runtime": runtime,
        **_TEMPLATE_FUNCTIONS,
    },
)
```

---

## Runtime Object

The `runtime` object is the **primary entry point** for all dynamic values.

### Available Fields

| Field                      | Description                   |
| -------------------------- | ----------------------------- |
| `runtime.env`              | Environment (dev/stg/prd)     |
| `runtime.rundate`          | Run date (YYYY-MM-DD)         |
| `runtime.job_params`       | Parameters passed from client |
| `runtime.debug`            | Debug flag                    |
| `runtime.inbox_dir`        | Local staging path            |
| `runtime.inbox_vol`        | Volume staging path           |
| `runtime.job_name`         | Job name                      |
| `runtime.job_id`           | Job ID                        |
| `runtime.job_run_id`       | Run ID                        |
| `runtime.job_user`         | Trigger user                  |
| `runtime.job_trigger_type` | Manual / schedule             |
| `runtime.workspace_url`    | Databricks workspace URL      |

---

## Referencing Values in Templates

### 1. Runtime Fields

```jinja
{{ runtime.rundate }}
{{ runtime.env }}
{{ runtime.job_run_id }}
```

---

### 2. Client Parameters

Client-side `params` are exposed under:

```jinja
runtime.job_params
```

#### Example

Client call:

```python
client.ingest(
    config=config,
    rundate=date(2026, 3, 31),
    params={
        "source_table": "cat.sch.tbl",
        "batch_id": "daily"
    }
)
```

Service-side usage:

```jinja
{{ runtime.job_params.source_table }}
{{ runtime.job_params.batch_id }}
```

#### Non-standard keys

```jinja
{{ runtime.job_params["some-key"] }}
```

---

## Built-in Template Functions

The following helper functions are available in templates:

### 1. `delta`

Used for date arithmetic.

```jinja
{{ delta(base=runtime.rundate, days=-1, format="YYYYMMDD") }}
```

---

### 2. `timestamp`

Used to format the current timestamp.

```jinja
{{ timestamp() }}
{{ timestamp(format="YYYYMMDDHHmmss") }}
```

---

### 3. `epoch`

Used to return the current Unix epoch.

```jinja
{{ epoch() }}
{{ epoch(unit="milliseconds") }}
```

---

### 4. `keyvault`

Used to retrieve secrets.

```jinja
{{ keyvault(scope="my-scope", key="db-password") }}
```

---

## Example Configuration

```json
{
  "pipeline": "example_pipeline",
  "tasks": [
    {
      "id": "download",
      "handler": "oedis.handler.database.download",
      "params": {
        "requests": [
          {
            "table": "{{ runtime.job_params.source_table }}",
            "file": "extract_{{ timestamp() }}.parquet",
            "watermark": "{{ epoch() }}"
          }
        ]
      }
    }
  ]
}
```

---

## Strict Validation Behavior

Server-side templating uses:

```python
StrictUndefined
```

### Implication

* Missing variables will **fail immediately**
* No silent fallback to empty values

#### Example (will fail)

```jinja
{{ runtime.job_params.missing_key }}
```

---

## Client-Side vs Server-Side Templating

| Type        | Syntax      | Evaluated         | Purpose             |
| ----------- | ----------- | ----------------- | ------------------- |
| Client-side | `[[ ... ]]` | Before submission | Parameter injection |
| Server-side | `{{ ... }}` | At runtime        | Dynamic execution   |

### Example

```json
{
  "table": "[[ database.source_table ]]",
  "file": "extract_{{ runtime.rundate }}.parquet"
}
```

---

## Best Practices

### 1. Use `runtime.job_params` for all external inputs

Avoid hardcoding values in config.

---

### 2. Keep templates simple

Complex logic should be handled in handlers, not templates.

---

### 3. Use helper functions for consistency

Prefer:

```jinja
{{ delta(...) }}
{{ timestamp(...) }}
{{ epoch(...) }}
```

over manual string manipulation.

---

### 4. Validate parameters early

Ensure all required `params` are provided by the client.

---

### 5. Avoid mixing responsibilities

* Client-side -> structure & inputs
* Server-side -> runtime resolution

---

## Summary

* Server-side templating is powered by **Jinja2**
* All dynamic values are accessed via `runtime`
* Client parameters are available under `runtime.job_params`
* Helper functions are injected into the template context
* Strict validation ensures early failure for missing values
