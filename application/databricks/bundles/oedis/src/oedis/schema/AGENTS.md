# Schema Instructions

- Keep schema changes minimal and targeted.
- Prefer readable schemas over abstraction.
- Inline property definitions instead of shared `$defs` when that makes the file easier to understand.
- Prefer flexible schemas for task params: use `additionalProperties: true` unless strict validation is clearly required.
- Add a short `description` for each top-level and user-facing property.
- Allow simple config authoring patterns such as `string` or `array` where the handler accepts both.
- Keep schema names aligned with handler paths, for example `oedis.handler.storage.download.json`.
- Preserve current handler behavior; do not make schemas stricter than runtime logic unless explicitly requested.
- Use English for schema titles and descriptions.
