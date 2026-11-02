"""Utilities for managing Databricks jobs via the workspace SDK."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config

logger = logging.getLogger(__name__)


class DatabricksJobManagerError(RuntimeError):
    """Base error for DatabricksJobManager operations."""


class NotFoundError(DatabricksJobManagerError):
    """Raised when a job/run/task cannot be found."""


class AmbiguityError(DatabricksJobManagerError):
    """Raised when multiple jobs match a lookup query."""


class DatabricksJobManager:
    """Manage Databricks Jobs API operations via the Workspace SDK with Azure SPN auth.

    Handles common Job/Run lookups and normalizes payload differences between Jobs API 2.1 and 2.2.
    """

    def __init__(
        self,
        host: str,
        resource_id: str,
        spn_tenantid: str,
        spn_id: str,
        spn_secret: str,
    ) -> None:
        """Create a workspace SDK client using Azure service principal credentials.

        Normalizes the host URL and configures the SDK for Azure client-secret auth.
        """
        # Avoid double https:// if the caller already passed a full URL.
        host_url = host if host.startswith("https://") else f"https://{host}"

        cfg = Config(
            host=host_url,
            auth_type="azure-client-secret",
            azure_workspace_resource_id=resource_id,
            azure_tenant_id=spn_tenantid,
            azure_client_id=spn_id,
            azure_client_secret=spn_secret,
        )
        self._client = WorkspaceClient(config=cfg)
        logger.info("Initialized Databricks workspace client for host %s", host_url)

    @staticmethod
    def _as_dict(value: Any) -> Dict[str, Any]:
        """Convert SDK models to dicts when possible."""
        if hasattr(value, "as_dict"):
            return value.as_dict()  # type: ignore[no-any-return]
        if isinstance(value, dict):
            return value
        return {"value": value}

    def get_job_id(self, job_name: str) -> int:
        """Return the job ID for an exact name match (case-insensitive).

        Raises NotFoundError when no job matches and AmbiguityError when multiple match.
        """
        logger.info("Looking up Databricks job ID for name '%s'", job_name)
        normalized = job_name.casefold()

        # Prefer server-side filtering if supported by the installed SDK version.
        try:
            jobs_iter = self._client.jobs.list(name=job_name)
        except TypeError:
            # Older SDKs may not accept the name kwarg.
            jobs_iter = self._client.jobs.list()

        matches = []
        for job in jobs_iter:
            settings = getattr(job, "settings", None)
            name = getattr(settings, "name", None) if settings else None
            if name and name.casefold() == normalized:
                matches.append(job)
        logger.debug("Matched %d jobs for name '%s'", len(matches), job_name)

        if not matches:
            raise NotFoundError(f"Job '{job_name}' not found.")
        if len(matches) > 1:
            raise AmbiguityError(f"Multiple jobs matched name '{job_name}'.")
        if matches[0].job_id is None:
            raise NotFoundError(f"Job '{job_name}' does not have an ID.")
        logger.info("Resolved job name '%s' to ID %s", job_name, matches[0].job_id)
        return matches[0].job_id

    def run_now(self, job_id: int, job_parameters: Optional[Dict[str, Any]] = None) -> int:
        """Start a new run for the specified job and return the run ID.

        The job_parameters payload is passed through as-is; only its keys are logged.
        """
        logger.info(
            "Starting job run for job_id=%s with job_parameters keys=%s",
            job_id,
            list((job_parameters or {}).keys()),
        )
        run = self._client.jobs.run_now(job_id=job_id, job_parameters=job_parameters)
        if run.run_id is None:
            raise NotFoundError(f"Run did not return an ID for job {job_id}.")
        logger.info("Started job run %s for job_id=%s", run.run_id, job_id)
        return run.run_id

    def cancel(self, run_id: int) -> None:
        """Cancel a running job run by run ID (no return value)."""
        logger.info("Canceling job run %s", run_id)
        self._client.jobs.cancel_run(run_id=run_id)

    def get_job_run(self, run_id: int) -> Dict[str, Any]:
        """Fetch run details for a job run and return a dictionary payload.

        Returns the SDK model converted to a dict, regardless of 2.1 or 2.2 payload shape.
        """
        run = self._client.jobs.get_run(run_id=run_id)
        return self._as_dict(run)

    def get_task_run(self, run_id: int) -> Dict[str, Any]:
        """Fetch task run output for a run ID and return a dictionary payload.

        Returns the SDK model converted to a dict for consistent access.
        """
        output = self._client.jobs.get_run_output(run_id=run_id)
        return self._as_dict(output)

    def wait_for_job_terminated(
        self,
        run_id: int,
        poll_interval_s: int = 10,
        timeout_s: int = 3600,
    ) -> Dict[str, Any]:
        """Wait for a job run to reach a terminal state and return its details.

        Polls Jobs API 2.2 (status.state) and 2.1 (state.life_cycle_state) payloads
        until TERMINATED or raises TimeoutError after the timeout.
        """
        logger.info(
            "Waiting for run %s to terminate (poll_interval_s=%s, timeout_s=%s)",
            run_id,
            poll_interval_s,
            timeout_s,
        )
        deadline = time.time() + timeout_s

        while True:
            run_dict = self.get_job_run(run_id)
            # Check both status and state because Jobs API 2.2 and 2.1 differ in payload shape.
            status = (run_dict.get("status") or {})
            status_state = status.get("state") if isinstance(status, dict) else None

            # Jobs API 2.2 terminal indicator (status.state).
            if status_state == "TERMINATED":
                logger.info("Run %s reached terminal status via Jobs API 2.2 payload", run_id)
                return run_dict

            state = (run_dict.get("state") or {})
            life_cycle_state = state.get("life_cycle_state") if isinstance(state, dict) else None

            # Jobs API 2.1 terminal indicator (state.life_cycle_state).
            if life_cycle_state == "TERMINATED":
                logger.info("Run %s reached terminal status via Jobs API 2.1 payload", run_id)
                return run_dict

            # Log the observed state for each poll iteration.
            logger.debug(
                "Polled run %s: status.state=%s state.life_cycle_state=%s",
                run_id,
                status_state,
                life_cycle_state,
            )
            now = time.time()
            if now >= deadline:
                # Timeout before terminal state was observed.
                logger.info("Timed out waiting for run %s after %s seconds", run_id, timeout_s)
                raise TimeoutError(f"Timed out waiting for run {run_id} to complete.")

            # Sleep until next poll or timeout deadline, whichever is sooner.
            time.sleep(min(poll_interval_s, max(0.0, deadline - now)))

    @staticmethod
    def normalize_job_run_state(job_run: Dict[str, Any]) -> str:
        """
        Normalize Databricks Jobs API 2.2 or 2.1 run payload to one of:
          - "RUNNING"  (includes QUEUED/BLOCKED/PENDING/RUNNING/TERMINATING/WAITING)
          - "SUCCESS"  (TERMINATED + termination_details.code == SUCCESS)
          - "FAILED"   (TERMINATED + anything else, including SUCCESS_WITH_FAILURES)
        Uses run["status"] when present; falls back to run["state"] for 2.1 payloads.
        """
        status = (job_run or {}).get("status") or {}
        status_state = status.get("state") if isinstance(status, dict) else None

        if status_state:
            if status_state != "TERMINATED":
                return "RUNNING"

            term = status.get("termination_details") or {}
            code = term.get("code")

            # Strict success: only plain SUCCESS counts (SUCCESS_WITH_FAILURES -> FAILED)
            return "SUCCESS" if code == "SUCCESS" else "FAILED"

        state = (job_run or {}).get("state") or {}
        life_cycle_state = state.get("life_cycle_state") if isinstance(state, dict) else None
        if life_cycle_state != "TERMINATED":
            return "RUNNING"

        result_state = state.get("result_state") if isinstance(state, dict) else None
        return "SUCCESS" if result_state == "SUCCESS" else "FAILED"

    @staticmethod
    def normalize_job_run_error(job_run: Dict[str, Any]) -> str:
        """Return a best-effort error message from Jobs API 2.2 or 2.1 payloads.

        Returns an empty string when no usable error message is available.
        """
        status = (job_run or {}).get("status") or {}
        termination_details = (
            status.get("termination_details") if isinstance(status, dict) else {}
        ) or {}
        state = (job_run or {}).get("state") or {}

        candidates = [
            termination_details.get("message"),
            termination_details.get("code"),
            status.get("state_message") if isinstance(status, dict) else None,
            state.get("state_message") if isinstance(state, dict) else None,
            (job_run or {}).get("error"),
            (job_run or {}).get("error_message"),
            (job_run or {}).get("error_code"),
            status.get("message") if isinstance(status, dict) else None,
        ]

        for message in candidates:
            if isinstance(message, str):
                cleaned = message.strip()
                if cleaned:
                    return cleaned
        return ""

    @staticmethod
    def extract_first_task_key(job_run: Dict[str, Any]) -> Optional[str]:
        """Return the first task_key from a run payload, if present."""
        tasks = (job_run or {}).get("tasks") or []
        if not tasks:
            return None

        first_task = tasks[0]
        task_key = (
            first_task.get("task_key")
            if isinstance(first_task, dict)
            else getattr(first_task, "task_key", None)
        )
        if not task_key:
            return None
        return task_key

    @staticmethod
    def extract_first_task_run_id(job_run: Dict[str, Any]) -> Optional[int]:
        """Return the run_id for the first task in a run payload, if present."""
        tasks = (job_run or {}).get("tasks") or []
        if not tasks:
            return None

        first_task = tasks[0]
        run_id = (
            first_task.get("run_id")
            if isinstance(first_task, dict)
            else getattr(first_task, "run_id", None)
        )
        if not isinstance(run_id, int):
            return None
        return run_id

    @staticmethod
    def extract_task_run_id(job_run: Dict[str, Any], task_key: str) -> Optional[int]:
        """Return the run_id for a specific task_key in a run payload, if present."""
        tasks = (job_run or {}).get("tasks") or []
        if not tasks:
            return None

        for task in tasks:
            current_key = (
                task.get("task_key") if isinstance(task, dict) else getattr(task, "task_key", None)
            )
            if current_key != task_key:
                continue
            run_id = (
                task.get("run_id") if isinstance(task, dict) else getattr(task, "run_id", None)
            )
            if not isinstance(run_id, int):
                return None
            return run_id

        return None

    @staticmethod
    def extract_notebook_output_result(task_run: Dict[str, Any]) -> Optional[str]:
        """Return notebook_output.result from a run output payload, if present.

        Returns ``None`` when the field is missing or empty.
        """
        notebook_output = (task_run or {}).get("notebook_output") or {}
        result = notebook_output.get("result")
        if isinstance(result, str) and result:
            return result
        return None

    @staticmethod
    def normalize_task_run_error(task_run: Dict[str, Any]) -> Optional[str]:
        """Return the task-run level error string from Databricks run output.

        No fallback behavior is applied; only the top-level ``error`` field is used.
        """
        error = (task_run or {}).get("error")
        if isinstance(error, str):
            return error
        return None
