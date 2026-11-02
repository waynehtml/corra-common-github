# DIS templating guide (client + server)

DIS uses **two-phase templating** so that:
- the **client** (your notebook / pipeline using `common`) can inject **dynamic parameters** first, and
- the **server** (DIS job) can later resolve **macros/functions** and **runtime parameters**.

That's why the delimiters are different:

| Phase | Where | Delimiters | What it supports |
|---|---|---|---|
| 1 | Client side (`common`) | `[[ ... ]]` | **Parameters only** (dynamic values you pass when launching the job) |
| 2 | Server side (`oedis`) | `{{ ... }}` | **Parameters + functions/macros** (e.g., `delta()`, `timestamp()`, `epoch()`, `keyvault()`) |

---

## Phase 1 - client-side templates: `[[ ... ]]` (parameters only)

Implemented in `common` as `DisClient.resolve_config_template()` using Jinja with:
- `variable_start_string="[[", variable_end_string="]]"`
- `StrictUndefined` (**missing parameters fail fast**)

### How to reference parameters

When the client renders your config, it calls `template.render(parameters)` which means every key in your `parameters` dict becomes a top-level variable.

If you pass:
```json
{
  "env": "dev",
  "rundate": "2026-02-05",
  "source": {
    "host": "sftp.example.com",
    "port": 22
  }
}
```

You can reference them like:
```json
{
  "env": "[[ env ]]",
  "rundate": "[[ rundate ]]",
  "sftp_host": "[[ source.host ]]",
  "sftp_port": "[[ source.port ]]"
}
```

### What "parameters only" means

Use `[[ ... ]]` for **straight substitutions**. Avoid calling server macros here.

Good:
- `[[ env ]]`
- `[[ source.host ]]`
- `[[ rundate ]]`

Don't do server macros here (those are for phase 2):
- `[[ delta(days=-1) ]]`  (wrong phase)
- `[[ keyvault(scope="x", key="y") ]]` (wrong phase)

> Tip: Using `[[ ... ]]` ensures your server-side `{{ ... }}` stays untouched until DIS runs.

---

## Phase 2 - server-side templates: `{{ ... }}` (parameters + functions)

Implemented in `oedis` in `oedis/template_functions.py` via Jinja default delimiters `{{ ... }}` with:
- `StrictUndefined` (**missing values fail fast**)
- globals (macros) registered: `delta`, `timestamp`, `epoch`, `keyvault`
- render called as: `compiled.render(runtime=runtime)`

### How to reference runtime parameters

DIS passes a `runtime` object (or dict-like) to templates.

You can refer to runtime attributes like:
- `{{ runtime.env }}`
- `{{ runtime.rundate }}`
- `{{ runtime.job_run_id }}` (if present in your runtime)
- `{{ runtime.job_params.key_name }}` for client-passed `params` values

Example:
```json
{
  "landing_path": "abfss://landing@acct.dfs.core.windows.net/raw/{{ runtime.env }}/{{ runtime.rundate }}/"
}
```

When the client supplies `params` to the job, DIS stores them under `runtime.job_params`.
If a value is purely a client input (not a runtime macro or secret), prefer resolving it in
Phase 1 with `[[ ... ]]` to avoid mixing phases and fail earlier on missing values.

### Using macros / functions

Macros are called inside `{{ ... }}` like normal Jinja expressions.

Example:
```json
{
  "partition": "{{ delta(base=runtime.rundate, days=-1, format='YYYYMMDD') }}",
  "file": "extract_{{ timestamp() }}.json",
  "message_id": "{{ epoch() }}"
}
```

---

## Server-side macro reference

### `delta(...) -> str`

Creates a formatted date string by shifting a base date.

Signature:
```python
delta(
  base: date | None = None,
  format: str = "YYYYMMDD",   # Pendulum tokens
  days: int = 0,
  months: int = 0,
  years: int = 0,
  bday: bool = False,         # True = business days, False = calendar days
  tz: str = "America/Toronto",
  uppercase: bool = True
) -> str
```

Behavior:
- All arguments are keyword-only (use `delta(days=-1)`, not positional args).
- If `base` is not provided, it uses "today" in `tz`.
- Applies `months`/`years` via `pandas.DateOffset`.
- Applies `days` via:
  - `pandas.offsets.BDay(days)` if `bday=True`
  - calendar `Timedelta(days=days)` if `bday=False`
- Formats with **Pendulum** date tokens (e.g. `YYYY-MM-DD`, `YYYYMMDD`)
  (strftime tokens like `%Y-%m-%d` are not supported).
- Uppercases the result by default (`uppercase=True`).

Examples:
```jinja
{{ delta(days=-1) }}
```
Yesterday (calendar day) based on today in `America/Toronto`.

```jinja
{{ delta(base=runtime.rundate, days=-1, bday=True, format="YYYY-MM-DD") }}
```
Previous **business day** relative to `runtime.rundate`.

```jinja
{{ delta(base=runtime.rundate, months=1, format="YYYYMM") }}
```
One month after `runtime.rundate`, formatted as `YYYYMM`.

---

### `timestamp(...) -> str`

Formats the current timestamp in the requested timezone.

Signature:
```python
timestamp(
  format: str = "YYYY-MM-DD_HH-mm-ss",
  tz: str = "America/Toronto",
  uppercase: bool = True
) -> str
```

Behavior:
- All arguments are keyword-only.
- Uses the current time in `tz`.
- Formats with **Pendulum** datetime tokens.
- Uppercases the result by default (`uppercase=True`).

Examples:
```jinja
{{ timestamp() }}
```
Current timestamp using the default format `YYYY-MM-DD_HH-mm-ss`.

```jinja
{{ timestamp(format="YYYYMMDDHHmmss") }}
```
Current timestamp with a compact filename-friendly format.

```jinja
{{ timestamp(tz="UTC") }}
```
Current UTC timestamp.

---

### `epoch(...) -> str`

Returns the current Unix epoch time.

Signature:
```python
epoch(unit: str = "seconds") -> str
```

Behavior:
- All arguments are keyword-only.
- `unit` supports `seconds` and `milliseconds`.
- Uses the current UTC time.

Examples:
```jinja
{{ epoch() }}
```
Current Unix epoch in seconds.

```jinja
{{ epoch(unit="milliseconds") }}
```
Current Unix epoch in milliseconds.

---

### `keyvault(scope, key) -> str`

Fetches a Databricks secret using `dbutils.secrets.get`.

Signature:
```python
keyvault(scope: str, key: str) -> str
```

Requirements:
- Must run inside Databricks runtime where `dbutils` is available.
- Both `scope` and `key` must be non-empty strings.
- Raises an error if secret lookup fails (Strict / fail-fast behavior).

Example:
```jinja
{{ keyvault(scope="my-kv-scope", key="sftp-private-key") }}
```

For SFTP key auth, that secret may contain a raw PEM string or a base64-encoded PEM string.

---

## Putting it together: end-to-end example

### 1) You author a config template (contains both phases)

```json
{
  "pipeline": "sftp_ingest",
  "tasks": [
    {
      "id": "download",
      "handler": "oedis.handler.sftp.download",
      "params": {
        "connection": {
          "hostname": "[[ sftp.host ]]",
          "port": "[[ sftp.port ]]",
          "auth": {
            "type": "key",
            "username": "[[ sftp.username ]]",
            "private_key": "{{ keyvault(scope='[[ kv.scope ]]', key='[[ kv.private_key_name ]]') }}"
          }
        },
        "requests": [
          {
            "src_dir": "/out/{{ delta(base=runtime.rundate, days=-1, format='YYYYMMDD') }}/",
            "file": "extract_{{ timestamp() }}.json",
            "include": ["*"],
            "recursive": false
          }
        ]
      }
    }
  ]
}
```

### 2) Client passes parameters (phase 1)

```json
{
  "sftp": { "host": "sftp.example.com", "port": 22, "username": "svc_user" },
  "kv": { "scope": "dept-a-kv", "private_key_name": "sftp-private-key" }
}
```

After client rendering, DIS receives:

```json
{
  "connection": {
    "hostname": "sftp.example.com",
    "port": 22,
    "auth": {
      "type": "key",
      "username": "svc_user",
      "private_key": "{{ keyvault(scope='dept-a-kv', key='sftp-private-key') }}"
    }
  },
  "requests": [
    {
      "src_dir": "/out/{{ delta(base=runtime.rundate, days=-1, format='YYYYMMDD') }}/",
      "file": "extract_{{ timestamp() }}.json",
      "include": ["*"],
      "recursive": false
    }
  ]
}
```

### 3) DIS resolves server macros at runtime (phase 2)

- `runtime.rundate` is provided by DIS runtime
- `keyvault(...)` fetches the secret
- `delta(...)` computes the date string
- `timestamp(...)` computes the current timestamp string
- `epoch(...)` computes the current Unix epoch string

---

## Common pitfalls

- **Mixing phases**: use `[[ ... ]]` only for values you pass in; use `{{ ... }}` for macros/runtime.
- **Missing parameters**: both phases use **StrictUndefined** -> missing variables raise errors early.
- **Quoting**: if your placeholder is meant to render a string, keep JSON quotes around it (as shown above).
- **Secrets**: `keyvault()` only works inside Databricks with a configured secret scope.


## Global hooks (`when`)

You can mark any task as an end-of-pipeline global hook by setting `when`:

- `on_success`: run only if the main (non-hook) task phase succeeds.
- `on_failure`: run only if the main phase fails.
- `on_complete`: always run at the end.

Rules:
- Tasks without `when` are normal main-phase tasks and run sequentially in config order.
- Tasks with `when` do **not** run in the main phase.
- End-phase order is fixed: (`on_success` or `on_failure`) first, then `on_complete`.
- Within each `when` group, config order is preserved.
- Hook failures are logged and stored as failed task results, but they do not flip the main pipeline status and do not stop remaining hooks.

Example:
```json
{
  "pipeline": "sftp_ingest",
  "tasks": [
    {
      "id": "download",
      "handler": "oedis.handler.sftp.download",
      "params": { "connection": {}, "requests": [] }
    },
    {
      "id": "notify_success",
      "handler": "oedis.handler.email.send",
      "when": "on_success",
      "params": {}
    },
    {
      "id": "notify_any",
      "handler": "oedis.handler.email.send",
      "when": "on_complete",
      "params": {}
    }
  ]
}
```
