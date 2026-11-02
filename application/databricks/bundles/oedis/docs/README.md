# TaskResult usage

This package standardizes handler outputs through `TaskResult`. Handlers return a
`TaskResult` instance and the engine stores it in `ctx.task_results[task_id]`
so downstream tasks can read a consistent, structured outcome.

## Handler responsibilities

- Return a `TaskResult` with standardized metadata (`task_id`, `handler`, `status`, `error`).
- Populate `outputs` and standardized `metrics` for the handler (`total_items`, `total_duration_sec`).
- Raise exceptions for failures instead of returning error payloads.
- Share data with downstream tasks by populating `TaskResult.outputs` and `TaskResult.metrics`.

## Engine responsibilities

- Execute handlers and capture their return value.
- Raise when handlers return a non-`TaskResult`.
- Propagate handler exceptions unless `optional=true`, in which case a failed `TaskResult` is recorded.
- Store results in `ctx.task_results[task_id]`.
- Enforce `optional` flag (default: `false`, optional: `true`).

## Example

```python
from oedis.task_result import TaskResult

def download(ctx) -> TaskResult:
    outputs = [
        {
            "src": {"type": "file", "path": "sftp:/in/a.csv", "size": 12031},
            "dest": {"type": "file", "path": "/inbox/a.csv", "size": 12031},
            "duration_sec": 0.642,
        }
    ]
    return TaskResult(
        task_id=ctx.task_id or "",
        handler="oedis.handler.sftp.download",
        status="success",
        outputs=outputs,
        metrics={"total_items": len(outputs), "total_duration_sec": 0.642},
    )
```


## Shared file-matching rule (`include` / `exclude`)

For handlers that support file discovery with `src_dir`, `include`, `exclude`, and `recursive`:

- `recursive=false` means only direct children of `src_dir` are considered.
- `recursive=true` means subdirectories under `src_dir` are also traversed.
- Handlers differ on whether `include` and `exclude` are matched against file names, relative paths, or both, so each handler guide must state that explicitly.
- Prefer `recursive: true` with simple patterns like `include: ["*.csv"]` when the handler supports basename matching for nested files.
- Some download handlers, including SharePoint download, also support `dst_dir` as an inbox-relative destination root.

Handler guides can define additional matching behavior, but they should state any exceptions explicitly.

## Global hooks (`when`)

Tasks with a `when` field are treated as global hooks and are not executed during the main task phase.

- `when: "on_success"` runs at the end only when the main phase succeeds.
- `when: "on_failure"` runs at the end only when the main phase fails.
- `when: "on_complete"` runs at the end in all cases.

Hook execution order is fixed:
1. Outcome-specific hooks (`on_success` or `on_failure`) in config order.
2. `on_complete` hooks in config order.

Hook failures are recorded as failed `TaskResult` entries and logged, but they do not change the main pipeline outcome and do not prevent other hooks from running.

## Notebook entrypoint parameter contract (`src/main.py`)

When this bundle is launched as a Databricks job notebook, `main.py` reads runtime values from
job bindings (first) and widgets (fallback).

Required:

- `config` (string): ingestion configuration JSON/template string.

Optional:

- `rundate` (string): `YYYY-MM-DD`; defaults to "today" in runtime timezone.
- `params` (string): JSON object string; defaults to empty string and is treated as `{}` at runtime.
- `zip` (boolean-like string): when true, notebook output is returned as `zlib:<base64>`.
- `debug` (boolean-like string): defaults to `true` in notebook mode.
- `timezone` (string): defaults to `America/Toronto`.
- Runtime/workspace metadata inputs: `env`, `home_dir`, `home_vol`, `job_name`, `job_user`, `job_id`,
  `job_run_id`, `job_trigger_type`, `workspace_url`.
- Derived runtime fields: `runtime.inbox_dir`, `runtime.inbox_vol`, and `runtime.inbox` (alias of `inbox_dir`).
- Staging behavior: tasks always use local inbox `home_dir/<job_run_id>`.
- Volume inbox is also exposed as `runtime.inbox_vol` (`home_vol/<job_run_id>`) for reference.

Notes:

- For local notebook development, when `config` does not start with `{`, DIS treats it as a
  file stem and attempts to load `../tests/config/<config>.json` relative to the notebook path.
- Invalid JSON in `config` or `params` raises explicit runtime/config resolution errors.

