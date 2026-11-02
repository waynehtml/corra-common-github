# smb

Handlers:

- `oedis.handler.smb.download`
- `oedis.handler.smb.upload`

Transfers files between an SMB share and the task inbox using a UNC root plus relative source/destination paths.

## Configuration

### Task parameters (`params`)

| Field | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `connection.unc` | Yes | string | — | UNC root path, for example `\\FS01\FinanceShare` or `\\FS01\FinanceShare\Inbound\CORRA`. Must include both server and share. |
| `connection.auth.username` | No | string | — | SMB username passed to `register_session()`. |
| `connection.auth.password` | No | string | — | SMB password passed to `register_session()`. |
| `requests` | Yes | list | — | One or more transfer specs. |

### Download spec (`requests[]` for `smb.download`)

| Field | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `src_dir` | No | string | `""` | Remote source directory relative to `connection.unc`. Leave empty to discover from the UNC root. Accepts `test\\abc` or `test/abc`. |
| `include` | No | string or list[string] | `["*"]` | Glob patterns for files to include. Matching is attempted against both the file name and the path relative to `src_dir`. Prefer `recursive: true` with patterns like `"*.csv"` for nested discovery. |
| `exclude` | No | string or list[string] | `[]` | Glob patterns for files to skip after include filtering. Matching is attempted against both the file name and the path relative to `src_dir`. |
| `recursive` | No | bool | `false` | When `true`, recursively walks subdirectories under `src_dir`. |
| `updated_after` | No | string | — | ISO-8601 timestamp. Keeps files with modified time `>= updated_after`. |
| `updated_before` | No | string | — | ISO-8601 timestamp. Keeps files with modified time `< updated_before`. |
| `dst_dir` | No | string | `""` | Local destination subdirectory relative to `ctx.inbox`. Must be relative and must not contain `..`. |
| `delete_source_file` | No | bool | `false` | Deletes the remote SMB file after successful download and local size validation. |

### Upload spec (`requests[]` for `smb.upload`)

| Field | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `dst_dir` | No | string | `""` | Remote destination directory relative to `connection.unc`. Leave empty to upload directly into the UNC root. |
| `src_dir` | No | string | `""` | Local source directory relative to `ctx.inbox`. Must be relative and must not contain `..`. |
| `include` | No | string or list[string] | `["*"]` | Glob patterns for local files to include. Matching is attempted against both the file name and the path relative to `src_dir`. Prefer `recursive: true` with patterns like `"*.csv"` for nested discovery. |
| `exclude` | No | string or list[string] | `[]` | Glob patterns for local files to skip after include filtering. Matching is attempted against both the file name and the path relative to `src_dir`. |
| `recursive` | No | bool | `false` | When `true`, recursively discovers local files under `src_dir`. |
| `delete_source_file` | No | bool | `false` | Deletes the local source file after successful upload and remote size validation. |

## Behavior notes

- `connection.unc` must be a valid UNC path beginning with `\\` and must include both server and share.
- Download `src_dir` and upload `dst_dir` are resolved relative to `connection.unc`. Empty values resolve to the UNC root.
- Download `dst_dir` and upload `src_dir` are resolved relative to `ctx.inbox`. Empty values resolve to `ctx.inbox`.
- Download `dst_dir` and upload `src_dir` must be relative local paths. Absolute paths and paths containing `..` are rejected.
- Download matching uses SMB file modified time. Upload matching uses local file name and relative path only.
- Time filtering applies only to `smb.download`: `updated_after <= mtime < updated_before`.
- If `recursive=false`, only the immediate directory entries are considered.
- If `recursive=true`, subdirectories are traversed and `include` and `exclude` are matched against both file names and paths relative to `src_dir`.
- Preferred style: use `recursive: true` with `include: ["*.csv"]` rather than `include: ["**/*.csv"]`.
- Downloads and uploads always keep the path relative to the resolved source root under the resolved destination root.
- The handler detects path collisions before transfer:
  - download fails if multiple remote files would resolve to the same local path
  - upload fails if multiple local files would resolve to the same remote path
- Download verifies that the local file exists, is a regular file, and matches the expected remote size after transfer.
- Upload verifies that the remote file size matches the local file size after transfer.
- Upload automatically creates missing SMB destination folders.
- Transfers use Python stream copy via `shutil.copyfileobj()`.
- The handler returns a standardized `TaskResult` structure documented in [`TASK_RESULTS.md`](../TASK_RESULTS.md).

## Outputs

Each transferred file produces one output entry like:

```json
{
  "src": {
    "type": "file",
    "path": "\\\\FS01\\FinanceShare\\Inbound\\CORRA\\rates\\daily.csv",
    "size": 1234,
    "mtime": "2026-03-19T08:15:00"
  },
  "dest": {
    "type": "file",
    "path": "/local/inbox/download/rates/daily.csv",
    "size": 1234
  },
  "duration_sec": 0.214
}
