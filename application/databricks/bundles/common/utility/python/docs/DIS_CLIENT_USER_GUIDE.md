# DIS Client User Guide

This guide covers the current `DisClient` API in
`oeds.domain.dis.client.dis_client` and the result models in
`oeds.domain.dis.client.ingest_result` and
`oeds.domain.dis.client.verify_result`.

## Overview

`DisClient` submits a Databricks job that runs the DIS ingestion service.

What it does:

1. Validates a final DIS config JSON string.
2. Calls the configured Databricks job with string job parameters.
3. Optionally waits for completion.
4. Returns an `IngestResult` with normalized status and available run metadata.
5. Optionally verifies landed files by path and size with `verify_file_by_file_size(...)`.

Notebook output can be returned either as plain JSON text or as a compressed
`zlib:<base64>` payload. `IngestResult` handles both forms when you call
`get_task_notebook_output()`.

## Prerequisites

You need:

- Databricks workspace host URL
- Azure tenant ID
- Azure client ID
- Azure client secret
- Azure Databricks workspace resource ID
- DIS Databricks job ID
- The `oeds-common` package available in the runtime that will call the client

## Create The Client

```python
from oeds.domain.dis.client.dis_client import DisClient

client = DisClient(
    host="https://adb-xxxxxxxxxxxx.x.azuredatabricks.net",
    azure_tenant_id="<tenant-id>",
    azure_client_id="<client-id>",
    azure_client_secret="<client-secret>",
    azure_workspace_resource_id="/subscriptions/.../resourceGroups/.../providers/Microsoft.Databricks/workspaces/...",
    dis_job_id=123456789,
)
```

## DIS Config Shape

`DisClient.ingest(...)` expects `config` to already be a valid JSON string.
That JSON must match the server-side pipeline contract used by OEDIS.

Top-level shape:

```json
{
  "pipeline": "storage_volume_to_volume",
  "tasks": [
    {
      "id": "download",
      "handler": "oedis.handler.storage.download",
      "params": {
        "connection": {
          "uri": "/Volumes/dev_application_cat/oedisint_db/land-oedataservices/dis/data/"
        },
        "requests": [
          {
            "recursive": true
          }
        ]
      }
    },
    {
      "id": "upload",
      "handler": "oedis.handler.storage.upload",
      "params": {
        "connection": {
          "uri": "/Volumes/dev_application_cat/oedisint_db/land-oedataservices/dis/data-copy/"
        },
        "requests": [
          {
            "recursive": true
          }
        ]
      }
    }
  ]
}
```

Notes:

- `tasks` is an array, not an object.
- Each task requires `id`, `handler`, and `params`.
- `params` is handler-specific and should follow the handler schema in
  `application/databricks/bundles/oedis/src/oedis/schema`.

## Minimal Synchronous Usage

```python
from datetime import date
from oeds.domain.dis.client.dis_client import DisClient

client = DisClient(
    host="https://adb-xxxxxxxxxxxx.x.azuredatabricks.net",
    azure_tenant_id="<tenant-id>",
    azure_client_id="<client-id>",
    azure_client_secret="<client-secret>",
    azure_workspace_resource_id="/subscriptions/.../resourceGroups/.../providers/Microsoft.Databricks/workspaces/...",
    dis_job_id=123456789,
)

config = """
{
  "pipeline": "storage_volume_to_volume",
  "tasks": [
    {
      "id": "upload",
      "handler": "oedis.handler.storage.upload",
      "params": {
        "connection": {
          "uri": "/Volumes/dev_application_cat/oedisint_db/land-oedataservices/dis/data-copy/"
        },
        "requests": [
          {
            "recursive": true
          }
        ]
      }
    }
  ]
}
""".strip()

ingest_result = client.ingest(
    config=config,
    rundate=date(2026, 3, 20),
    params={"batch": "daily"},
)

print(ingest_result.status)
print(ingest_result.get_job_run_id())
print(ingest_result.get_job_run_page_url())
print(ingest_result.get_task_notebook_output())
```

## Template Rendering

`DisClient.resolve_config_template(...)` renders a config template before you
submit it.

Client-side template markers:

- variables: `[[ ... ]]`
- blocks: `[% ... %]`
- comments: `[# ... #]`

```python
from oeds.domain.dis.client.dis_client import DisClient

config_template = """
{
  "pipeline": "storage_volume_to_volume",
  "tasks": [
    {
      "id": "upload",
      "handler": "oedis.handler.storage.upload",
      "params": {
        "connection": {
          "uri": "[[ landing_dir ]]"
        },
        "requests": [
          {
            "dst_dir": "[[ rundate ]]",
            "recursive": true
          }
        ]
      }
    }
  ]
}
""".strip()

resolved_config = DisClient.resolve_config_template(
    config_template=config_template,
    template_params={
        "landing_dir": "/Volumes/dev_application_cat/oedisint_db/land-oedataservices/dis/data-copy",
        "rundate": "2026-03-20"
    },
)
```

The resolved string must still be valid JSON before it is passed to `ingest()`.

## ingest(...) Parameters

`DisClient.ingest(...)` accepts:

- `config`: required final JSON string
- `rundate`: optional `datetime.date`; sent as `YYYY-MM-DD`
- `params`: optional dict; JSON-encoded before submission
- `debug`: optional bool; serialized to `"true"` or `"false"`
- `compress_notebook_output`: optional bool; sent to Databricks as the `zip` job parameter
- `wait`: optional bool, default `True`
- `wait_timeout_sec`: optional int, default `3600`
- `wait_poll_sec`: optional int, default `10`

Job parameters sent to Databricks are:

- `config`
- `rundate`
- `params`
- `debug`
- `zip`

## Waiting vs Non-Waiting

When `wait=True`:

- the method waits for terminal state
- the returned `IngestResult` contains normalized status
- `job_run_output` and `task_run_output` are populated when available

When `wait=False`:

- the returned `IngestResult.status` is `RUNNING`
- `job_run_id` is available
- `job_run_output`, `task_run_output`, and notebook output are not available yet

Example:

```python
ingest_result = client.ingest(
    config=config,
    wait=False,
)

print(ingest_result.status)
print(ingest_result.get_job_run_id())
```

## Reading Results

`client.ingest(...)` returns an `IngestResult`.

Useful fields:

- `ingest_result.status`
- `ingest_result.job_id`
- `ingest_result.job_parameters`
- `ingest_result.job_run_output`
- `ingest_result.task_run_output`

Useful helper methods on `IngestResult`:

- `get_job_run_id()`
- `get_job_run_page_url()`
- `get_task_run_id()`
- `get_task_run_error()`
- `get_task_run_error_trace()`
- `get_task_notebook_output()`
- `get_nb_task_result(task_id)`
- `get_nb_task_result_dst(task_id)`
- `get_nb_task_result_dst_path(task_id)`

Example:

```python
ingest_result = client.ingest(config=config)

print("status:", ingest_result.status)
print("job run id:", ingest_result.get_job_run_id())
print("page url:", ingest_result.get_job_run_page_url())
print("task run id:", ingest_result.get_task_run_id())
print("task error:", ingest_result.get_task_run_error())
print("notebook output:", ingest_result.get_task_notebook_output())
print("upload result:", ingest_result.get_nb_task_result("upload"))
print("upload dest objects:", ingest_result.get_nb_task_result_dst("upload"))
print("upload dest paths:", ingest_result.get_nb_task_result_dst_path("upload"))
```

Expected notebook output pattern:

```json
{
  "status": "success",
  "task_results": {
    "upload": {
      "task_id": "upload",
      "handler": "oedis.handler.storage.upload",
      "status": "success",
      "outputs": [
        {
          "dest": {
            "path": "/Volumes/dev_application_cat/oedisint_db/land-oedataservices/dis/data-copy/file.csv"
          }
        }
      ],
      "metrics": {
        "total_items": 1,
        "total_duration_sec": 0.123
      }
    }
  }
}
```

## File Verification

`DisClient` verifies landed files with `verify_file_by_file_size(...)`.
This method uses destination records from a prior successful `IngestResult`.

Requirements:

- `client.ingest(...)` must already have completed successfully
- the target task must have file destinations in `outputs[].dest`

```python
ingest_result = client.ingest(config=config)

verify_result = client.verify_file_by_file_size(
    dest_dir_path="/Volumes/dev_application_cat/oedisint_db/land-oedataservices/dis/data-copy",
    land_dir_path="/Volumes/dev_application_cat/oedisint_db/land-oedataservices/dis/data-final",
    timeout_sec=300,
    poll_sec=10,
    upload_task_key="upload",
)

print(verify_result.status)
print(verify_result.is_success)
print(verify_result.matched)
print(verify_result.unmatched)
```

Behavior:

- if every expected file appears with the expected size, status is `SUCCESS`
- if polling reaches the timeout first, status is `TIMEOUT`
- the returned object is also stored on `client.verify_result`

## Exceptions

The client defines these explicit exception types:

- `IngestBaseError`
- `IngestValueError`
- `IngestServiceError`
- `IngestVerifyError`

Typical guidance:

- use `IngestValueError` for bad caller inputs or invalid verification arguments
- use `IngestServiceError` for Databricks submission, wait, or task retrieval failures
- use `IngestVerifyError` when verification cannot run because the client state is invalid

## Troubleshooting

`config must be a valid JSON string`

- render templates first
- confirm the final string is valid JSON
- confirm it matches the OEDIS pipeline shape

`wait_timeout_sec must be greater than 0` or `wait_poll_sec must be greater than 0`

- pass positive integers only

`No ingest_result is available. Call ingest() before verification.`

- run `client.ingest(...)` before `verify_file_by_file_size(...)`

`ingest_result status must be SUCCESS before verification`

- verification only runs against a completed successful ingest result

Notebook output is missing or cannot be parsed

- confirm the Databricks notebook returned JSON through `dbutils.notebook.exit(...)`
- if compression was requested, confirm the payload was returned as `zlib:<base64>`

## Recommended Usage Pattern

1. Build or render the final config JSON string.
2. Call `client.ingest(...)`.
3. Store the returned `IngestResult`.
4. Read notebook output from the `IngestResult` helpers.
5. If needed, call `client.verify_file_by_file_size(...)`.
6. Catch `IngestBaseError` or the specific subclasses that matter for your workflow.