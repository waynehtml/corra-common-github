# Project Instructions

## Git workflow
- At the start of every new task or thread, create a new git branch before making any code changes.
- Branch names must use the `code/` prefix.
- Prefer short, descriptive branch names derived from the task, for example `code/fix-login-timeout`.
- Do not commit directly to `main`.

## Project structure
- `oedis` is the server-side data ingestion service.
- `common` is the client-side library used to call DIS.
- Keep server orchestration and runtime logic in `oedis`.
- Keep client SDK, job invocation, and verification helpers in `common`.
- Shared contracts between client and server must remain consistent and intentional.

## Change scope
- Prefer minimal, targeted changes.
- Do not refactor unrelated code unless explicitly requested.
- Preserve existing behavior unless the task explicitly requires a logic change.
- Distinguish clearly between required fixes and optional cleanup.

## Consistency
- Reuse existing patterns before introducing new ones.
- Prefer extending existing result models, helpers, and exception types over creating parallel patterns.
- Keep naming, config shape, and return structures consistent across modules.

## Logging and observability
- Add useful logging for major execution steps, path resolution, verification, and external I/O.
- Use `debug` for verbose diagnostic details and `info` for major lifecycle events.
- Keep logs actionable and include task or handler identifiers where possible.
- Never log secrets, credentials, tokens, or sensitive payload contents.

## Error handling
- Raise exceptions for real failures rather than silently returning partial success.
- Use project-specific exception types where available.
- Preserve useful stack traces for debugging, especially for Databricks job runs.
- Avoid swallowing exceptions unless there is a clear recovery path.

## Results and contracts
- Keep result payloads structured and consistent across client and server.
- Avoid introducing one-off output shapes when an existing contract can be reused.
- Use optional fields only when they are part of a stable contract, not as ad hoc extensions.

## Configuration
- Keep config structure consistent across handlers.
- Prefer validation over implicit assumptions.
- Avoid introducing handler-specific config patterns unless necessary and clearly documented.

## Storage and path handling
- Prefer shared helpers for path normalization.
- Be explicit about path types such as local paths, `file:`, `dbfs:`, `/Volumes`, and cloud URIs.
- Be careful with Databricks storage semantics and avoid mixing path conventions unnecessarily.

## Databricks conventions
- Keep notebook and task return payloads compact and structured.
- Put detailed diagnostics in logs rather than large notebook exit payloads.
- Design with Databricks job output limits and storage behavior in mind.

## Code style
- Use English in code, comments, docstrings, logs, and docs.
- Prefer clear docstrings over redundant inline comments.
- Remove comments that only restate obvious code behavior.
- Favor readability and maintainability over cleverness.

## PR workflow
- For Azure DevOps repos, prefer Azure DevOps REST API for PR creation and merge when Azure CLI auth, extensions, or repo detection are unreliable.
- Cached Git Credential Manager credentials may be used for authenticated Azure DevOps REST API calls.
- After pushing a task branch, create a PR to `main` and complete it immediately when no branch policies block completion.
- Delete the remote source branch after merge when allowed.
- Switch back to main branch after merge.


## Shell and tool fallbacks
- Use PowerShell-native syntax for command sequencing; do not use `&&`.
- Do not try `apply_patch` since it is blocked by policy
