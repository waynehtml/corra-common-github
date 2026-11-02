from __future__ import annotations

import importlib
import json
import logging
from dataclasses import asdict
from importlib import resources
from collections import Counter
from typing import Any, Dict

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from oedis.common.utils import concat_exception_messages, ls_to_str
from oedis.context import Context
from oedis.exceptions import SchemaValidationError, TaskException
from oedis.runtime import Runtime
from oedis.task_result import TaskResult

logger = logging.getLogger(__name__)


class RaptorEngine:

    def __init__(
        self,
        *,
        runtime: Runtime,
        config_resolved: Dict[str, Any],
        config_template: Dict[str, Any],
    ) -> None:
        self.runtime = runtime
        self.config_resolved = config_resolved
        self.config_template = config_template

        self.ctx: Context | None = None
        self.tasks: list[Dict[str, Any]] = []
        self.main_tasks: list[Dict[str, Any]] = []
        self.hook_tasks: list[Dict[str, Any]] = []

    def run(self) -> Context:
        pipeline_failed = False
        pipeline_exception: Exception | None = None

        # Validate configuration schema
        self._validate_schema(instance=self.config_resolved, schema_name="config")

        self.ctx = Context(
            runtime=self.runtime,
            config_resolved=self.config_resolved,
            config_template=self.config_template,
        )
        self.ctx.pipeline = self.config_resolved.get("pipeline")

        # Ensure no duplicate task ID
        self.tasks = self.config_resolved.get("tasks") or []
        self._validate_unique_task_ids(self.tasks)

        # Find task and hooks
        self.main_tasks = [task for task in self.tasks if "when" not in task]
        self.hook_tasks = [task for task in self.tasks if "when" in task]

        logger.info("tasks: %s", self.main_tasks)
        logger.info("hooks: %s", self.hook_tasks)

        try:
            logger.info("Starting main phase with %d task(s)", len(self.main_tasks))
            for task in self.main_tasks:
                task_id = task.get("id")
                is_active = task.get("active", True)

                if not is_active:
                    logger.info("Skipping inactive task: %s", task_id)
                    continue

                self._run_task(task)
                logger.debug("Files in inbox:\n%s", ls_to_str(self.ctx.inbox))

        except Exception as exc:
            logger.debug("Files in inbox:\n%s", ls_to_str(self.ctx.inbox))
            pipeline_failed = True
            pipeline_exception = exc
            logger.exception(
                "Main phase failed. Hook phase will still run before pipeline exits."
            )
        finally:
            self._run_hooks(pipeline_failed=pipeline_failed)

        if pipeline_exception:
            raise pipeline_exception

        return self.ctx

    def _validate_schema(self, *, instance: Any, schema_name: str) -> None:
        """
        Validate an instance against oedis.schema/<schema_name>.json.

        If the schema file does not exist, validation is skipped.
        """
        schema_file = f"{schema_name}.json"

        try:
            schema_path = resources.files("oedis.schema").joinpath(schema_file)
            if not schema_path.is_file():
                logger.info(
                    "No schema found at '%s'. Skipping schema validation for '%s'.",
                    schema_file,
                    schema_name,
                )
                return

            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            validator.validate(instance)

        except ValidationError as exc:
            raise SchemaValidationError(
                f"Schema validation failed for '{schema_name}': "
                f"{concat_exception_messages(exc)}"
            ) from exc
        except Exception as exc:
            raise SchemaValidationError(
                f"Failed to load schema '{schema_file}': "
                f"{concat_exception_messages(exc)}"
            ) from exc

    def _validate_unique_task_ids(self, tasks: list[Dict[str, Any]]) -> None:
        task_ids = [str(task.get("id")) for task in tasks if task.get("id") is not None]
        duplicates = [task_id for task_id, count in Counter(task_ids).items() if count > 1]
        if duplicates:
            duplicate_list = ", ".join(sorted(duplicates))
            raise SchemaValidationError(
                f"Duplicate task id(s) detected in 'tasks': {duplicate_list}. "
                "Each task id must be unique."
            )


    def _load_handler(self, dotted_path: str) -> Any:
        try:
            module_path, symbol_name = dotted_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            return getattr(module, symbol_name)
        except (ValueError, ImportError, AttributeError) as exc:
            raise TaskException(
                f"Failed to load handler '{dotted_path}': {exc}"
            ) from exc

    def _log_task_result(self, result: TaskResult) -> None:
        logger.info("Task result | %s", asdict(result))

    def _run_task(self, task: Dict[str, Any], *, is_hook: bool = False) -> None:
        task_id = task["id"]
        handler_path = task["handler"]
        params = task.get("params") or {}
        optional = task.get("optional", False)

        self._validate_schema(instance=params, schema_name=handler_path)

        self.ctx.task_id = task_id
        self.ctx.task_handler = handler_path
        self.ctx.task_params = params

        handler = self._load_handler(handler_path)
        task_type = "hook" if is_hook else "task"
        logger.info("Running %s %s with handler %s", task_type, task_id, handler_path)

        if not callable(handler):
            raise TaskException(f"Handler '{handler_path}' must be callable(ctx)")

        try:
            result = handler(self.ctx)

            if not isinstance(result, TaskResult):
                raise TaskException(
                    f"Task '{task_id}' failed due to invalid task return type"
                )

            if str(result.status).lower() != "success":
                raise TaskException(
                    f"Task '{task_id}' returned status='{result.status}'. "
                    "Handlers must raise exceptions on error."
                )

            self.ctx.set_task_result(task_id, result)
            self._log_task_result(result)
            return

        except Exception as exc:
            if is_hook:
                # Hooks are best-effort: record the failure for observability and continue.
                logger.exception(
                    "Hook '%s' failed in handler '%s' due to '%s'. Continuing with remaining hooks.",
                    task_id,
                    handler_path,
                    concat_exception_messages(exc),
                )
                failure = TaskResult(
                    task_id=task_id,
                    handler=handler_path,
                    status="failed",
                    error=concat_exception_messages(exc),
                )
                self.ctx.set_task_result(task_id, failure)
                self._log_task_result(failure)
                return

            if not optional:
                # Required tasks still stop the pipeline, but save a failed result first
                # so downstream reporting can see the terminal task state in context.
                error_message = concat_exception_messages(exc)

                # Create a TaskResult with the error details to store in context for the task
                failure = TaskResult(
                    task_id=task_id,
                    handler=handler_path,
                    status="failed",
                    error=error_message,
                )
                self.ctx.set_task_result(task_id, failure)

                # Keep a reference to the error in the context for easier access by hooks.
                self.ctx.pipeline_failed = True
                self.ctx.pipeline_error = {
                    "task_id": task_id,
                    "handler": handler_path,
                    "error_type": type(exc).__name__,
                    "error": error_message,
                }

                # re-raise as TaskException to signal a task failure without losing the original traceback
                raise TaskException(
                    f"Task '{task_id}' failed in handler '{handler_path}' "
                    f"({type(exc).__name__})"
                ) from exc

            # Optional tasks use the same failed TaskResult shape, then continue.
            logger.exception(
                "Task '%s' failed in handler '%s' due to '%s'",
                task_id,
                handler_path,
                concat_exception_messages(exc),
            )
            failure = TaskResult(
                task_id=task_id,
                handler=handler_path,
                status="failed",
                error=concat_exception_messages(exc),
            )
            self.ctx.set_task_result(task_id, failure)
            self._log_task_result(failure)
            logger.info(
                "Continuing to next task despite failure in task '%s' because task is optional",
                task_id,
            )
            return

    def _run_hooks(self, *, pipeline_failed: bool) -> None:
        logger.info("Starting hook phase with %d hook(s)", len(self.hook_tasks))

        if not self.hook_tasks:
            return

        hook_when = "on_failure" if pipeline_failed else "on_success"
        primary_hooks = [task for task in self.hook_tasks if task.get("when") == hook_when]
        complete_hooks = [task for task in self.hook_tasks if task.get("when") == "on_complete"]

        logger.info(
            "Pipeline %s. Running '%s' hooks then 'on_complete' hooks",
            "failed" if pipeline_failed else "succeeded",
            hook_when,
        )

        for hook in [*primary_hooks, *complete_hooks]:
            hook_id = hook.get("id")
            is_active = hook.get("active", True)

            if not is_active:
                logger.info("Skipping inactive task: %s", hook_id)
                continue

            self._run_task(hook, is_hook=True)

        logger.info("Completed hook phase")
