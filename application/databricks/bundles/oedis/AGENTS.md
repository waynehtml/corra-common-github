# DIS Server Instructions

## Responsibility
- `oedis` is the server-side ingestion service.
- The server is responsible for runtime orchestration, task execution, path handling, and structured task results.
- Keep client concerns out of the server unless they are part of a shared contract.

## Runtime and orchestration
- Runtime entrypoints such as `main.py` should handle setup and cleanup responsibilities.
- Directory initialization, inbox selection, and environment-specific setup should stay in the runtime layer.
- Handlers should focus on their own task logic and should not take over cross-cutting orchestration concerns.

## Handlers
- Keep handler interfaces as uniform as practical.
- Prefer consistent input structure across handlers.
- Handlers should raise exceptions for real failures instead of returning synthetic success states.
- Avoid handler-specific output formats when the standard task result shape can be used.

## Task results
- Keep `TaskResult` structure consistent across handlers.
- Prefer standardized outputs with clear source and destination semantics.
- Keep metrics names consistent across handlers.
- Use `extras` sparingly and only for data that does not belong in the core contract.

## Logging
- Log major handler lifecycle steps, resolved sources and destinations, filtering decisions, item counts, and timing.
- Use `debug` for detailed diagnostics that are helpful during troubleshooting.
- Do not log secrets, credentials, tokens, or full sensitive payloads.

## Error handling
- Let true failures fail loudly with useful exceptions.
- Preserve enough context in raised errors to identify the task and handler involved.
- Do not hide stack traces that are useful for Databricks troubleshooting.

## Config and schema
- Keep handler config structure aligned with project-wide conventions.
- Validate resolved config before execution when possible.
- Prefer consistent top-level keys and patterns across handlers.

## Storage and file operations
- Prefer shared path normalization helpers.
- Use `dbutils.fs`-compatible handling where appropriate for Databricks-managed storage.
- Be careful with `/Volumes` behavior and cloud-backed storage consistency.
- Avoid unnecessary mixing of `pathlib`, local file APIs, and Databricks filesystem APIs for the same path flow.

## Databricks-specific behavior
- Keep notebook exit payloads small and structured.
- Put detailed debugging information in logs, not the returned payload.
- Design for reliability in job execution, storage interaction, and result collection.