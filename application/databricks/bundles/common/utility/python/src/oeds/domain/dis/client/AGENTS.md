# Common Client Instructions

## Responsibility
- `dis/client` is the client-side library used to call DIS.
- The client is responsible for input preparation, job submission, reading structured job output, and optional verification helpers.
- Keep server-side runtime and orchestration logic out of the client.

## API design
- Prefer clear, explicit client APIs.
- Keep convenience helpers thin and predictable.
- Avoid hidden behavior that makes job submission or verification hard to reason about.
- Favor backward-compatible API evolution where possible.

## Errors
- Raise typed exceptions for invalid inputs, client-side failures, and service-side failures.
- Keep exception boundaries clear so callers can distinguish input errors from execution failures.
- Preserve relevant context in error messages without leaking sensitive details.

## Verification
- Verification logic should be explicit and testable.
- Convenience helper methods may create verifiers internally, but should not hide important verification assumptions.
- Keep verification separate from core submission flow unless the API explicitly combines them.

## Contracts
- Treat server response models and result structures as stable contracts.
- Do not add client-only interpretations that drift from the server contract.
- Keep parsing and validation logic strict enough to catch bad responses early.

## Logging
- Log major client lifecycle steps such as request preparation, job submission, polling, output retrieval, and verification.
- Use `debug` for verbose payload and parsing diagnostics when useful.
- Never log secrets, credentials, tokens, or sensitive data values.

## Input handling
- Validate public method arguments early.
- Normalize optional values consistently.
- Prefer shared helpers for repeated parsing, rendering, and validation behavior.

## Minimal change preference
- Prefer targeted changes over broad refactors.
- Reuse existing client patterns before adding new abstractions.
- Keep helper methods small and aligned with the established client design.