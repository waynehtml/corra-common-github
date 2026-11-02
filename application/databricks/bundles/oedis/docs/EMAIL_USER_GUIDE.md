# email

Handler: `oedis.handler.email.send`

Sends a single email through Microsoft Graph using client credentials.

## Configuration

### Task fields

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `connection.auth.tenant_id` | Yes | none | Microsoft Entra tenant ID. |
| `connection.auth.client_id` | Yes | none | Application client ID. |
| `connection.auth.client_secret` | Yes | none | Application client secret. |
| `message.from` | Yes | none | Sender mailbox address used in `/users/{sender}/sendMail`. |
| `message.to` | Yes | none | Recipient email address or list of addresses. |
| `message.subject` | No | `DIS notification` | Subject line. |
| `message.body.format` | No | `html` | Passed to Graph as `contentType`. |
| `message.body.content` | Conditionally | none | Inline message body content. Mutually exclusive with `message.body.template`. |
| `message.body.template` | Conditionally | none | Bundled template file name loaded from `oedis/template/{template}`. File name only. Mutually exclusive with `message.body.content`. |
| `message.attachments` | No | `[]` | List of staged-file match requests using `src_dir`, `include`, `exclude`, and `recursive`. |
| `max_total_attachment_bytes` | No | `10485760` | Maximum total attachment size to include. |

Exactly one of `message.body.content` or `message.body.template` must be provided.

## Body fields

`message.body.format` controls how Microsoft Graph interprets the body content. The handler passes this value to Graph as `contentType`. If omitted, it defaults to `html`.

- Use `html` when the body contains HTML markup such as `<p>Summary</p>`.
- Use `text` when the body should be sent as plain text.

`message.body.content` is inline body text defined directly in the task JSON. Use it when the email body is short or fully self-contained in the config.

Example:

```json
"body": {
  "format": "html",
  "content": "<p>DIS test email</p>"
}
```

`message.body.template` is the file name of a bundled template loaded from `oedis/template/{template}`. Use it when the email body is larger, shared across tasks, or easier to maintain in a separate file.

Example:

```json
"body": {
  "format": "html",
  "template": "email_send_graph.html"
}
```

Only the file name is allowed for `message.body.template`; nested paths are not supported.

Choose one of these two approaches:

- Use `message.body.content` for inline content stored directly in the task config.
- Use `message.body.template` for a reusable bundled template file.

## Templating

`message.subject`, `message.body.content`, `message.body.template`, and string values inside `message.attachments` are resolved with task-level markers `<< ... >>`.

If `message.body.template` is used, the handler loads the bundled file from the fixed root `oedis/template` and then renders the loaded template content with the same task-level render context used for inline body content.

Render context includes:

- `runtime`
- `task_results`

## Attachment requests

`message.attachments` is a list of objects with the same staged-file request shape used by other file handlers:

- `src_dir`: optional inbox-relative directory under the staging root. Default `""`.
- `include`: optional glob pattern or list of glob patterns. Default `['*']`.
- `exclude`: optional glob pattern or list of glob patterns. Default `[]`.
- `recursive`: optional boolean. Default `false`.

Behavior:

- Attachment files are resolved only from staged files under `ctx.inbox`.
- The old attachment selector and literal-path behavior is not supported.
- Matched files are deduplicated by resolved path in first-seen order.
- Empty attachment matches are allowed.
- Missing or unreadable matched files are skipped.
- Files over `max_total_attachment_bytes` are omitted from the outgoing email.
- `saveToSentItems` is always `true`.

## Example

Template file: `oedis/template/email_send_graph.html`

```json
{
  "id": "send_email",
  "handler": "oedis.handler.email.send",
  "when": "on_complete",
  "params": {
    "connection": {
      "auth": {
        "tenant_id": "<tenant-id>",
        "client_id": "<client-id>",
        "client_secret": "<client-secret>"
      }
    },
    "message": {
      "from": "dis@yourorg.com",
      "to": ["team@yourorg.com"],
      "subject": "DIS job summary - << runtime.job_run_id >>",
      "body": {
        "format": "html",
        "template": "email_send_graph.html"
      },
      "attachments": [
        {
          "src_dir": "download",
          "include": ["*.csv", "*.xlsx"]
        },
        {
          "src_dir": "upload",
          "include": "**/*.json",
          "recursive": true
        }
      ]
    }
  }
}
```
