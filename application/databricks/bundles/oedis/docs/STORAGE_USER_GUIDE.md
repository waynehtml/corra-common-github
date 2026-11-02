# storage

Handlers:
- `oedis.handler.storage.download`
- `oedis.handler.storage.upload`

Copies files with `dbutils.fs.cp` between Databricks-supported storage paths and the task inbox.

## Common behavior

### Task fields

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `connection.uri` | Yes | none | Remote base path or URI used for every request in the task. Examples: `/Volumes/...` or `abfss://...`. |
| `requests` | Yes | none | One or more transfer request specs. |

### Common request fields

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `include` | No | `["*"]` | Glob pattern or list of glob patterns to include. Use `recursive: true` with patterns like `"*.csv"` for nested discovery. |
| `exclude` | No | `[]` | Glob pattern or list of glob patterns to exclude after include filtering. |
| `recursive` | No | `false` | Walk subdirectories under the resolved source root. |

Notes:
- `include` and `exclude` accept either a string or a list of strings.
- `recursive=false` considers only files directly under the resolved source root and matches `include` and `exclude` against file names.
- `recursive=true` also traverses subdirectories and matches `include` and `exclude` against both file names and paths relative to the resolved source root.
- Preferred style: use `recursive: true` with `include: ["*.csv"]` rather than `include: ["**/*.csv"]`.
- Request `src_dir` and `dst_dir` values must be relative when provided. Absolute paths, full URIs, and paths containing `..` are rejected.
- Empty strings and omitted values resolve directly to the applicable base root.
- Matched files always keep their path relative to the resolved source root under the resolved destination root.

## storage.download

Copies files from remote storage into the task inbox.

### Download resolution

- Remote source root = `connection.uri + src_dir`
- Local destination root = `ctx.inbox + dst_dir`

### Download request spec

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `src_dir` | No | `""` | Remote source directory relative to `connection.uri`. Empty or omitted means use `connection.uri` directly. |
| `dst_dir` | No | `""` | Local destination directory relative to `ctx.inbox`. Empty or omitted means use `ctx.inbox` directly. |
| `include` | No | `["*"]` | Glob pattern or list of patterns to include. Use `recursive: true` with patterns like `"*.csv"` for nested discovery. |
| `exclude` | No | `[]` | Glob pattern or list of patterns to exclude after include filtering. |
| `recursive` | No | `false` | Walk subdirectories under the resolved remote source root. |

### Download example

```json
{
  "id": "transfer-storage-to-staging",
  "handler": "oedis.handler.storage.download",
  "params": {
    "connection": {
      "uri": "/Volumes/main/raw"
    },
    "requests": [
      {
        "src_dir": "partners",
        "recursive": true,
        "include": ["*.parquet"],
        "dst_dir": "staged/inbound"
      }
    ]
  }
}
```

## storage.upload

Copies files from the task inbox into remote storage.

### Upload resolution

- Local source root = `ctx.inbox + src_dir`
- Remote destination root = `connection.uri + dst_dir`

### Upload request spec

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `src_dir` | No | `""` | Local source directory relative to `ctx.inbox`. Empty or omitted means use `ctx.inbox` directly. |
| `dst_dir` | No | `""` | Remote destination directory relative to `connection.uri`. Empty or omitted means use `connection.uri` directly. |
| `include` | No | `["*"]` | Glob pattern or list of patterns to include. |
| `exclude` | No | `[]` | Glob pattern or list of patterns to exclude. |
| `recursive` | No | `false` | Walk subdirectories under the resolved local source root. |

### Upload example

```json
{
  "id": "transfer-staging-to-storage",
  "handler": "oedis.handler.storage.upload",
  "params": {
    "connection": {
      "uri": "abfss://oedatastore@lakeanalyticsprd01.dfs.core.windows.net/curated"
    },
    "requests": [
      {
        "src_dir": "download",
        "dst_dir": "partners",
        "recursive": true,
        "include": ["*.parquet"],
        "exclude": ["*_tmp.parquet"]
      }
    ]
  }
}
```