# sharepoint

Handler: `oedis.handler.sharepoint.download`

Downloads files from SharePoint Online document libraries using Microsoft Graph app-only authentication.

## Configuration

### Task fields

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `connection.site` | Yes | none | SharePoint site URL, for example `https://contoso.sharepoint.com/sites/Finance`. |
| `connection.drive` | Yes | none | Drive or document library name, for example `Documents`. |
| `connection.auth.tenant_id` | Yes | none | Microsoft Entra tenant ID. |
| `connection.auth.client_id` | Yes | none | Application client ID. |
| `connection.auth.client_secret` | Yes | none | Application client secret. |
| `connection.timeout_sec` | No | none | Optional HTTP timeout in seconds. |
| `requests` | Yes | none | One or more SharePoint download specs. |

### Request spec fields

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `src_dir` | No | `""` | Remote source directory relative to the selected SharePoint drive. Leave empty or omit it to discover files directly from the drive root. |
| `dst_dir` | No | `""` | Destination directory under `ctx.inbox`. Leave empty or omit it to copy directly into `ctx.inbox`. |
| `recursive` | No | `false` | When `true`, traverse subfolders under `src_dir`. When `false`, only files directly under `src_dir` are considered. |
| `include` | No | `[]` | Glob pattern or list of patterns to include. Patterns are matched against the path relative to `src_dir`. Empty means all files are eligible. Use `recursive: true` with patterns like `"*.csv"` for nested discovery. |
| `exclude` | No | `[]` | Glob pattern or list of patterns to exclude after include filtering. Patterns are matched against the path relative to `src_dir`. |
| `updated_after` | No | none | Keep files modified at or after this timestamp. |
| `updated_before` | No | none | Keep files modified before this timestamp. |

## Behavior

- `src_dir` is resolved relative to the selected drive and must not start with `/`.
- `dst_dir`, when provided, must be relative to `ctx.inbox`.
- Pagination follows Graph `@odata.nextLink` until all items are listed.
- Pattern matching is applied to the path relative to `src_dir`. File-name-only patterns such as `*.csv` still work because they are evaluated against that relative path.
- Preferred style: use `recursive: true` with `include: ["*.csv"]` rather than `include: ["**/*.csv"]`.
- Matched files always keep their path relative to `src_dir` under `dst_dir`.
- Time filtering uses a half-open interval: `updated_after <= lastModifiedDateTime < updated_before`.

## Outputs

The handler returns the standardized `TaskResult` shape documented in
[`TASK_RESULTS.md`](TASK_RESULTS.md).

## Example

```json
{
  "connection": {
    "site": "https://contoso.sharepoint.com/sites/Finance",
    "drive": "Documents",
    "auth": {
      "tenant_id": "[[ tenant_id ]]",
      "client_id": "{{ keyvault(scope='dis-secrets', key='sp_client_id') }}",
      "client_secret": "{{ keyvault(scope='dis-secrets', key='sp_client_secret') }}"
    }
  },
  "requests": [
    {
      "src_dir": "CORRA",
      "dst_dir": "sharepoint/corra",
      "recursive": true,
      "include": ["*.csv"],
      "exclude": ["*_tmp.csv", "~$*"],
      "updated_after": "2026-02-01T00:00:00Z",
      "updated_before": "2026-02-02T00:00:00Z"
    }
  ]
}
```
