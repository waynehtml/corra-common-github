# archive

Handlers:

- `oedis.handler.archive.extract`
- `oedis.handler.archive.compress`

Reads and writes archive files from the task inbox. Supported formats are `zip`, `tar`, `gztar`/`tgz`, and `7z`.

## Extract (`oedis.handler.archive.extract`)

Extracts matched archive files from `ctx.inbox`.

### Task fields

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `credential.passphrase` | No | none | Task-level passphrase used when a file spec does not provide its own `passphrase`. |
| `requests` | Yes | none | One or more archive extraction specs. |

### Request spec fields

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `src_dir` | No | `""` | Inbox-relative source directory containing archive files. Empty or omitted means `ctx.inbox`. |
| `include` | No | `["*"]` | Archive glob or list of globs to include. |
| `exclude` | No | `[]` | Archive glob or list of globs to exclude. |
| `format` | No | `auto` | `auto`, `zip`, `tar`, `gztar`, `tgz`, or `7z`. |
| `recursive` | No | `false` | Discover archives recursively under `src_dir`. |
| `dst_dir` | No | `""` | Inbox-relative destination folder for extracted files. Empty or omitted keeps the handler's default of extracting beside each source archive. |
| `delete_source_file` | No | `false` | Delete the archive after successful extraction. |
| `passphrase` | No | none | File-level passphrase override. |

### Behavior

- `format=auto` infers the format from the matched archive extension.
- If `dst_dir` is omitted, extracted files are written beside the source archive.
- Extracted files always keep their archive-relative path under `dst_dir`.
- `src_dir` and `dst_dir` are always resolved under `ctx.inbox`. Leading `/` is treated as inbox-rooted shorthand and paths containing `..` are rejected.
- ZIP and `7z` can use a passphrase; tar formats ignore it.
- Output records are file-to-file transfers from the archive path to each extracted file.

### Example

```json
{
  "requests": [
    {
      "src_dir": "raw",
      "include": ["*.zip"],
      "format": "auto",
      "dst_dir": "staged/reports",
      "delete_source_file": true
    }
  ]
}
```

## Compress (`oedis.handler.archive.compress`)

Creates archive files from matched inbox files.

### Task fields

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `credential.passphrase` | No | none | Task-level passphrase used when a file spec does not provide its own `passphrase`. |
| `requests` | Yes | none | One or more archive creation specs. |

### Request spec fields

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `src_dir` | No | `""` | Inbox-relative source directory containing files to archive. Empty or omitted means `ctx.inbox`. |
| `include` | No | `["*"]` | File glob or list of globs to include. |
| `exclude` | No | `[]` | File glob or list of globs to exclude. |
| `format` | No | `zip` | `auto`, `zip`, `tar`, `gztar`, `tgz`, or `7z`. |
| `recursive` | No | `false` | Discover source files recursively under `src_dir`. |
| `dst_dir` | No | `""` | Inbox-relative destination folder for the archive file. Empty or omitted keeps the handler's default of writing the archive under `src_dir`. |
| `delete_source_file` | No | `false` | Delete matched source files after the archive is written. |
| `passphrase` | No | none | File-level passphrase override. Only used for `zip` and `7z`. |

### Behavior

- `format=auto` writes a zip archive.
- The handler generates the archive file name from the matched source selection.
- `src_dir` and `dst_dir` are always resolved under `ctx.inbox`. Leading `/` is treated as inbox-rooted shorthand and paths containing `..` are rejected.
- Existing archive targets are overwritten.
- Output records are directory-to-file transfers with `metrics.source_file_count` per archive.

### Example

```json
{
  "credential": {
    "passphrase": "{{ keyvault(scope='dept-kv', key='archive_pass') }}"
  },
  "requests": [
    {
      "src_dir": "unit_data/out",
      "include": ["*.csv"],
      "recursive": true,
      "format": "7z",
      "dst_dir": "unit_data/archives"
    }
  ]
}
```
