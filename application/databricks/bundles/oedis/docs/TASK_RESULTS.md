# TaskResult Result Contract

All DIS download handlers (SFTP, REST, SMB, SharePoint, UC) return a
**TaskResult** object.

The result contract is **handler‑agnostic**, meaning downstream tasks
can process results without knowing which handler produced them.

Handlers **raise exceptions on failure** instead of returning an error
payload.

------------------------------------------------------------------------

# 1. TaskResult Overview

A `TaskResult` describes the outcome of a task execution and contains:

-   Task metadata
-   Output artifacts produced by the task
-   Execution metrics

## Standard Structure

``` json
{
  "task_id": "download",
  "handler": "oedis.handler.sftp.download",
  "status": "success",
  "error": null,
  "outputs": [],
  "metrics": {}
}
```

### Fields

  Field       Type          Description
  ----------- ------------- ----------------------------------
  `task_id`   string        Logical task identifier
  `handler`   string        Fully qualified handler name
  `status`    string        `"success"` or `"failed"`
  `error`     string/null   Error message if the task failed
  `outputs`   list          One entry per produced artifact
  `metrics`   object        Task-level execution metrics

------------------------------------------------------------------------

# 2. Output Contract

Each item in `outputs[]` describes **one processed artifact**.

``` json
{
  "src": {
    "type": "file",
    "path": "/remote/path/report.csv",
    "size": 12031
  },
  "dest": {
    "type": "file",
    "path": "/job/inbox/report.csv",
    "size": 12031
  },
  "duration_sec": 0.642
}
```

### Output Fields

  Field            Type     Required   Description
  ---------------- -------- ---------- ------------------------------
  `src`            object   yes        Source endpoint
  `src.type`       string   yes        `"file"` or `"table"`
  `src.path`       string   yes        Source location
  `src.size`       number   optional   Bytes (file) or rows (table)
  `dest`           object   yes        Destination endpoint
  `dest.type`      string   yes        `"file"` or `"table"`
  `dest.path`      string   yes        Destination location
  `dest.size`      number   optional   Bytes (file) or rows (table)
  `duration_sec`   number   yes        Processing duration

------------------------------------------------------------------------

# 3. Endpoint Types

The result contract supports **two endpoint types**.

## File Endpoint

Represents any file-style location.

Examples include:

-   Local filesystem
-   DBFS
-   Databricks volumes
-   ADLS
-   SFTP paths
-   HTTP URLs

Example:

``` json
{
  "type": "file",
  "path": "/Volumes/dev/oedis/inbox/report.csv",
  "size": 12031
}
```

Notes:

-   URLs are represented as `type="file"`
-   `path` may be a URL, filesystem path, or storage path

------------------------------------------------------------------------

## Table Endpoint

Represents a **structured table source or destination**.

Examples:

-   Unity Catalog tables
-   SQL extraction tasks

Example:

``` json
{
  "type": "table",
  "path": "main.finance.daily_rates",
  "size": 15042
}
```

------------------------------------------------------------------------

# 4. Common Result Patterns

## File → File

Typical download flow.

Used by:

-   SFTP
-   REST
-   SMB
-   SharePoint
-   Storage copy
-   Archive tasks

Example:

``` json
{
  "src": {
    "type": "file",
    "path": "https://api.vendor.com/v1/reports/daily.csv",
    "size": 43051
  },
  "dest": {
    "type": "file",
    "path": "/job/inbox/rest/daily.csv",
    "size": 43051
  },
  "duration_sec": 0.372
}
```

------------------------------------------------------------------------

## Table → File

Typical **Unity Catalog download** flow.

Example:

``` json
{
  "src": {
    "type": "table",
    "path": "sql:trx_extract",
    "size": 15042
  },
  "dest": {
    "type": "file",
    "path": "file:/dbfs/tmp/oedis/inbox/finance/rundate=2024-10-15/trx_extract.parquet",
    "size": 15042
  },
  "duration_sec": 0.214
}
```

------------------------------------------------------------------------

## File → Table

Typical **Unity Catalog upload** flow.

Example:

``` json
{
  "src": {
    "type": "file",
    "path": "/job/inbox/daily_rates.parquet",
    "size": 15042
  },
  "dest": {
    "type": "table",
    "path": "main.finance.daily_rates",
    "size": 15042
  },
  "duration_sec": 0.214
}
```

------------------------------------------------------------------------

# 5. Metrics Contract

`metrics` contains **task-level aggregate statistics**.

Example:

``` json
{
  "total_items": 1,
  "total_duration_sec": 1.117
}
```

### Recommended Fields

  Field                  Description
  ---------------------- -------------------------------
  `total_items`          Number of output items
  `total_duration_sec`   Total task execution duration

Handlers may add additional metrics when useful.

------------------------------------------------------------------------

# 6. Error Handling

Handlers **raise exceptions** when a failure occurs.

Examples:

-   Connection failures
-   Authentication failures
-   Transfer failures
-   Validation failures

If the engine marks a task as **optional**, it may persist a failed
`TaskResult`:

``` json
{
  "status": "failed",
  "error": "SFTP connection failed"
}
```

------------------------------------------------------------------------

# 7. Consumer Guidelines

Consumers of `TaskResult` should:

1.  Use endpoint types (`file` / `table`) instead of handler names
2.  Treat `size` as optional
3.  Expect multiple items in `outputs`
4.  Handle both success and failure states
5.  Ignore unknown fields for forward compatibility

------------------------------------------------------------------------

# 8. Conceptual Structure

    TaskResult
       │
       ├── metadata
       │     task_id
       │     handler
       │     status
       │
       ├── outputs[]
       │     ├── src (file | table)
       │     ├── dest (file | table)
       │     └── duration_sec
       │
       └── metrics
             total_items
             total_duration_sec

------------------------------------------------------------------------

# 9. Minimal Example

``` json
{
  "task_id": "download_customer_file",
  "handler": "oedis.handler.sftp.download",
  "status": "success",
  "outputs": [
    {
      "src": {
        "type": "file",
        "path": "/remote/customer/report.csv",
        "size": 12031
      },
      "dest": {
        "type": "file",
        "path": "/Volumes/dev/oedis/inbox/report.csv",
        "size": 12031
      },
      "duration_sec": 0.642
    }
  ],
  "metrics": {
    "total_items": 1,
    "total_duration_sec": 1.117
  }
}
```
