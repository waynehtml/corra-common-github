# gpg

Handler: `oedis.handler.gpg.decrypt`

Decrypts OpenPGP-encrypted files (`.gpg`, `.pgp`, `.asc`) from the task inbox using a provided private key.

## Configuration

### Task parameters (`params`)

| Field | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `credential.private_key` | Yes | string | — | Base64-encoded private key. For the GnuPG backend, this should be base64 of the ASCII-armored private key text. |
| `credential.passphrase` | No | string | — | Passphrase for a protected private key. |
| `requests` | Yes | list | n/a | One or more decryption specs. |

### Request spec (`requests[]`)

| Field | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `src_dir` | No | string | `""` | Inbox-relative source directory. Leading `/` is treated as inbox-rooted shorthand. Empty means `ctx.inbox`. Paths must stay under `ctx.inbox`. |
| `include` | No | string or list[string] | `["*"]` | Glob pattern(s) for candidate files. Only files ending in `.gpg`, `.pgp`, or `.asc` are actually decrypted. |
| `exclude` | No | string or list[string] | `[]` | Glob pattern(s) to skip. |
| `recursive` | No | bool | `false` | Search nested subdirectories when `true`. |
| `dst_dir` | No | string | `""` | Inbox-relative destination directory for plaintext files. Empty or omitted keeps the handler's default of writing plaintext beside each source file. Leading `/` is treated as inbox-rooted shorthand. Absolute paths outside the inbox and paths containing `..` are rejected. When set, the relative path under `src_dir` is preserved. |
| `delete_source_file` | No | bool | `false` | Delete the encrypted source file after successful decryption. |

## Behavior

- The handler reads files from `ctx.inbox / src_dir`.
- `src_dir` and `dst_dir` are always resolved under `ctx.inbox`. Leading `/` is treated as inbox-rooted shorthand and paths containing `..` are rejected.
- Matching is performed by `find_files(...)` using `include`, `exclude`, and `recursive`.
- Files whose suffix is not `.gpg`, `.pgp`, or `.asc` are skipped even if matched by the glob patterns.
- If `dst_dir` is not set, the plaintext file is written beside the source file.
- If `dst_dir` is set, the plaintext file is written under `ctx.inbox / dst_dir` and preserves the relative path under `src_dir`.
- The plaintext output file name is derived by removing the final encrypted suffix:
  - `a.csv.gpg` → `a.csv`
  - `a.pgp` → `a`
  - `a.asc` → `a`
- Decryption tries GnuPG first and falls back to PGPy if GnuPG fails.

## Outputs

`TaskResult.outputs` contains one entry per decrypted file:

```json
[
  {
    "src": {"type": "file", "path": "<cipher file path>"},
    "dest": {"type": "file", "path": "<plaintext file path>"},
    "duration_sec": 0.123456
  }
]

