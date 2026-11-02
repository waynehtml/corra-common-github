# file

Handlers:

- `oedis.handler.file.copy`
- `oedis.handler.file.move`
- `oedis.handler.file.flatten`
- `oedis.handler.file.delete`

Performs file operations inside the staging area using Python filesystem functions (`pathlib`, `shutil`).

## Configuration

### Task parameters (`params`)

| Field | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `requests` | Yes | list | n/a | One or more file operation specs. |

### Copy and move request spec (`requests[]`)

| Field | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `src_dir` | No | string | `""` | Inbox-relative source directory. Empty or omitted means `ctx.inbox`. |
| `recursive` | No | bool | `false` | When `true`, traverse subdirectories under `src_dir`; otherwise only direct children of `src_dir` are considered. |
| `include` | No | string or list[string] | `["*"]` | Glob patterns matched against each candidate path relative to `src_dir`. For nested discovery, prefer `recursive: true` with patterns like `"*.csv"` only when matching by relative path is sufficient. |
| `exclude` | No | string or list[string] | `[]` | Glob patterns matched against each candidate path relative to `src_dir`; matching entries are skipped. |
| `dst_dir` | No | string | `""` | Inbox-relative destination directory. Empty or omitted means `ctx.inbox`. |
| `dst_name` | No | string | n/a | Override only the final destination file name. Relative parent directories from `src_dir` are still preserved. Allowed only when exactly one file matches. |

### Flatten request spec (`requests[]`)

| Field | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `src_dir` | No | string | `""` | Source directory relative to `ctx.inbox`. Empty or omitted means `ctx.inbox`. Files are always collected recursively. |
| `dst_dir` | No | string | `""` | Destination directory relative to `ctx.inbox`. Empty or omitted means `ctx.inbox`. Must not normalize to the same path as `src_dir`. |
| `include` | No | string or list[string] | `["*"]` | Glob patterns matched against each collected file path relative to `src_dir`. |
| `exclude` | No | string or list[string] | `[]` | Glob patterns matched against each collected file path relative to `src_dir`; matching entries are skipped. |
| `operation` | No | string | `move` | One of `move` or `copy`. |
| `on_collision` | No | string | `error` | One of `error`, `overwrite`, or `rename`. |

### Delete request spec (`requests[]`)

| Field | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `src_dir` | No | string | `""` | Inbox-relative source directory. Empty or omitted means `ctx.inbox`. |
| `recursive` | No | bool | `false` | When `true`, traverse subdirectories under `src_dir`; otherwise only direct children of `src_dir` are considered. |
| `include` | No | string or list[string] | `["*"]` | Glob patterns matched against each candidate path relative to `src_dir`. |
| `exclude` | No | string or list[string] | `[]` | Glob patterns matched against each candidate path relative to `src_dir`; matching entries are skipped. |

## Path rules

All paths are resolved relative to `ctx.inbox`.

- Empty `src_dir` resolves to `ctx.inbox`.
- Empty `dst_dir` resolves to `ctx.inbox` for `copy`, `move`, and `flatten`.
- `flatten` allows empty `dst_dir`, but `src_dir` and `dst_dir` must resolve to different inbox paths.
- Paths that resolve outside `ctx.inbox` are rejected.
- `src_dir` must exist and must be a directory.

## Matching behavior

Candidate entries are selected from `src_dir` and then filtered as follows:

1. Build the candidate set.
2. Keep only candidates matching at least one `include` pattern.
3. Remove candidates matching any `exclude` pattern.

Pattern matching uses `fnmatch` against the candidate path relative to `src_dir` and normalized to POSIX style (`/` separators). It does not do separate basename matching.

Examples of relative paths seen by pattern matching:

- `report.csv`
- `nested/a/report.csv`

Notes:

- `include` defaults to `["*"]`.
- `exclude` defaults to `[]`.
- `copy`, `move`, and `flatten` operate on files only.
- `delete` operates on files and directories.
- For `delete`, directories are deleted deepest-first so nested content is removed safely.
- `flatten` always walks `src_dir` recursively.
- Preferred style for handlers that support basename matching is `recursive: true` with `include: ["*.csv"]`, but the local file handlers match relative paths only, so nested files may still need path-aware patterns.

## Validation rules

### `copy` and `move`

- If `dst_name` is provided, exactly one file must match.

### `flatten`

- `src_dir` and `dst_dir` must normalize to different inbox-relative paths.
- Destination paths are planned before any writes are performed.
- Matched files are sorted by relative path before collision handling and writes.

### `delete`

- `dst_dir` and `dst_name` are forbidden.

## Destination rules for `copy` and `move`

For each matched file:

1. Start with the file path relative to `src_dir`.
2. If `dst_name` is set, replace only the final file name in that relative path.
3. Resolve the destination as `dst_dir / <resulting relative path>`.

Parent directories are created automatically as needed.

## Destination rules for `flatten`

For each matched file:

1. Collect all files recursively under `src_dir`.
2. Sort matches by relative path for deterministic behavior.
3. Flatten each file into `dst_dir` using only `src_path.name`.
4. Apply `on_collision` to source-name collisions before any writes happen.

Collision handling:

- `error`: fail if multiple matched files would map to the same flattened destination path.
- `overwrite`: later files in sorted order win.
- `rename`: keep the first file name unchanged, then rename later collisions as `basename_1.ext`, `basename_2.ext`, and so on.

When the planned destination file already exists in `dst_dir`, `copy` and `move` overwrite it.

## Outputs

Each handler returns a `TaskResult` with:

- `status="success"` on success
- `outputs`: one entry per processed item
- `metrics.total_items`: number of processed items
- `metrics.total_duration_sec`: total handler wall-clock duration

### Output entry shape

Each output entry contains:

- `src.type`: always `"file"`
- `src.path`: source path
- `dest.type`: always `"file"`
- `dest.path`: destination path for `copy`, `move`, and `flatten`, empty string for `delete`
- `duration_sec`: per-item execution duration in seconds

## Examples

### Copy matching files recursively

```json
{
  "id": "file-copy-basic",
  "handler": "oedis.handler.file.copy",
  "params": {
    "requests": [
      {
        "src_dir": "staged",
        "include": ["*.csv"],
        "recursive": true,
        "dst_dir": "archive"
      }
    ]
  }
}
```

### Flatten matched files into a single directory

```json
{
  "id": "file-flatten-basic",
  "handler": "oedis.handler.file.flatten",
  "params": {
    "requests": [
      {
        "src_dir": "staged",
        "include": ["daily_report.csv"],
        "exclude": ["*.txt"],
        "dst_dir": "archive",
        "operation": "move",
        "on_collision": "error"
      }
    ]
  }
}
```
