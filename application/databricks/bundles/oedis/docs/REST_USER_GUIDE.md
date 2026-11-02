# rest

Handler: `oedis.handler.rest.request`

Executes HTTP requests with connection-level defaults, retries, and structured authentication through `connection.auth`. When `requests[].file` is provided, the response body is written to the task inbox; when `file` is omitted, the request is still executed and validated but no file is written.

## Configuration

### Task parameters (`params`)

| Field | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `connection.timeout_sec` | No | number | none | Requests timeout in seconds. |
| `connection.headers` | No | object | `{}` | Free-form headers passed through to every request. Use this for generic headers, not for supported auth types. |
| `connection.fault_tolerant.retries` | No | int | `0` | Default retry count for all requests. |
| `connection.fault_tolerant.retry_interval_sec` | No | int | `0` | Default delay between retries. |
| `connection.fault_tolerant.retry_on_status` | No | list[int] | `[]` | HTTP status codes to retry. |
| `connection.auth` | No | object | `{ "type": "none" }` | Official structured auth contract described below. |
| `requests` | Yes | list | none | One or more request specs. |

### Structured auth (`connection.auth`)

`connection.auth` is the official contract for supported authentication. Use it for `basic`, `bearer`, `api_key`, and `oauth2_client_credentials`.

`connection.headers` remains a free-form header map and is still passed through as-is. That is where nonstandard or fully manual auth belongs, but only when `connection.auth.type = "none"`.

#### Supported auth types

| Type | Required fields | Result |
| --- | --- | --- |
| `none` | `type` | No built-in auth processing. |
| `basic` | `type`, `username`, `password` | Sends `Authorization: Basic <base64(username:password)>`. |
| `bearer` | `type`, `token` | Sends `Authorization: Bearer <token>`. |
| `api_key` | `type`, `name`, `value` | Sends `<name>: <value>`. Header-based only. |
| `oauth2_client_credentials` | `type`, `token_url`, `client_id`, `client_secret`, optional `scope` | Fetches an access token and sends `Authorization: Bearer <access_token>`. |

#### Header precedence and conflicts

Headers are merged in this order:

1. `connection.headers`
2. Built-in auth header from `connection.auth` when `auth.type != "none"`
3. `requests[].headers`

This means:

- `connection.headers` stays generic and is always passed through.
- When `connection.auth.type != "none"`, the built-in auth header overwrites any conflicting header from `connection.headers`.
- `requests[].headers` always wins over both built-in auth and `connection.headers`.
- If you need a raw or nonstandard auth header to remain authoritative, set `connection.auth.type` to `none` and place that header in `connection.headers` or `requests[].headers`.

### Request spec (`requests[]`)

| Field | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | No | string | none | Optional request identifier. |
| `method` | No | string | `"GET"` | HTTP method. |
| `url` | Yes | string | none | Request URL. |
| `headers` | No | object | `{}` | Free-form request headers. These override both `connection.headers` and built-in auth headers. |
| `params` | No | object | `{}` | URL query params merged into the request URL. Request values win over any existing query string in `url`. |
| `body` | No | object | none | Structured request body. When present, both `body.type` and `body.value` are required. |
| `file` | No | string | none | Optional output path under inbox. Prefer relative values like `raw/file.json`. Leading `/` is still accepted and treated as inbox-relative. Must not contain `..`. |
| `fault_tolerant` | No | object | none | Per-request retry overrides. |

### Request params and body

Use `requests[].params` for URL query parameters:

```json
{
  "url": "https://example/api/report",
  "params": {
    "date": "{{ rundate }}",
    "format": "json"
  }
}
```

Use `requests[].body` for request content:

| `body.type` | `body.value` shape | Behavior |
| --- | --- | --- |
| `json` | Any JSON value | Sent as `json=<value>`. |
| `form` | Object of key-value pairs | Sent as `data=<value>` form fields. |
| `raw` | String | Sent as raw `data=<value>` content. |

Examples:

```json
{
  "body": {
    "type": "json",
    "value": {
      "vectorId": "{{ param(name=\"vectorId\") }}"
    }
  }
}
```

```json
{
  "body": {
    "type": "form",
    "value": {
      "start_date": "{{ delta(days=-1) }}",
      "end_date": "{{ rundate }}"
    }
  }
}
```

```json
{
  "body": {
    "type": "raw",
    "value": "{\"status\":\"ready\"}"
  }
}
```

## Outputs

- `TaskResult.outputs` contains one entry per successful request using `{src, dest, duration_sec}` where URLs are represented as `src.type="file"` and `src.path=<url>`.
- `TaskResult.metrics` includes `total_items` and `total_duration_sec`.
- When `file` is omitted, the request still adds an output entry and uses `dest.type="file"` with `dest.path=""`.
- HTTP errors (4xx and 5xx) raise after retries via `response.raise_for_status()`.

## Errors and troubleshooting

- `connection.auth.type` must be one of `none`, `basic`, `bearer`, `api_key`, or `oauth2_client_credentials`.
- Required auth fields raise `ValueError` when missing for the selected auth type.
- OAuth2 token failures raise `AuthError` with status and response details.
- If `body` is present, both `body.type` and `body.value` are required.
- `file` paths are always resolved under `ctx.inbox`; a leading `/` is treated as inbox-rooted shorthand. Paths containing `..` are rejected.
- Request timeouts and connection errors are retried based on configuration and raised after retries are exhausted.
- HTTP status errors (4xx and 5xx) raise after retries via `response.raise_for_status()`.
- `ctx.inbox` must be set by the runner when at least one request writes a file; otherwise a `RuntimeError` is raised.

## Examples

### none plus manual Authorization header

Use this when the auth header is custom, preformatted, or intentionally managed by headers.

```json
{
  "connection": {
    "auth": {"type": "none"},
    "headers": {
      "Authorization": "Bearer {{ keyvault(scope=\"dis-secrets\", key=\"token_value\") }}"
    }
  }
}
```

Sample file: `rest_request_token_header.json`

### basic

```json
{
  "connection": {
    "auth": {
      "type": "basic",
      "username": "{{ keyvault(scope=\"dis-secrets\", key=\"api_user\") }}",
      "password": "{{ keyvault(scope=\"dis-secrets\", key=\"api_password\") }}"
    }
  }
}
```

Sample file: `rest_request_basic_auth.json`

### bearer

```json
{
  "connection": {
    "auth": {
      "type": "bearer",
      "token": "{{ keyvault(scope=\"dis-secrets\", key=\"token_value\") }}"
    }
  }
}
```

Sample file: `rest_request_bearer_auth.json`

### api_key

```json
{
  "connection": {
    "auth": {
      "type": "api_key",
      "name": "x-api-key",
      "value": "{{ keyvault(scope=\"dis-secrets\", key=\"api_key\") }}"
    }
  }
}
```

Sample file: `rest_request_x_api_key.json`

### oauth2_client_credentials

```json
{
  "connection": {
    "auth": {
      "type": "oauth2_client_credentials",
      "token_url": "https://login.example.com/oauth2/token",
      "client_id": "{{ keyvault(scope=\"dis-secrets\", key=\"client_id\") }}",
      "client_secret": "{{ keyvault(scope=\"dis-secrets\", key=\"client_secret\") }}",
      "scope": "api.read"
    }
  }
}
```

Sample file: `rest_request_oauth_client_credentials.json`

See also `rest_request_form_post.json` for a form-body action request without file output and `dest.path` set to `""`.
