"""Simple ingestion client for cross-workspace Databricks jobs."""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Any, Dict, List, Sequence

from databricks.sdk.runtime import dbutils
from jinja2 import Environment, StrictUndefined
from pydantic import ConfigDict, ValidationError, validate_call

from oeds.core.job_utils import DatabricksJobManager
from oeds.core.misc_utils import is_valid_json
from oeds.domain.dis.client.ingest_result import IngestResult
from oeds.domain.dis.client.verify_result import VerifyResult

logger = logging.getLogger(__name__)

_VALIDATION_CONFIG = ConfigDict(
    strict=True,
    arbitrary_types_allowed=True,
)


class IngestBaseError(RuntimeError):
    """Base error for ingestion failures."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class IngestValueError(IngestBaseError):
    """Raised when the client receives an invalid input value."""


class IngestServiceError(IngestBaseError):
    """Raised when the ingestion service cannot submit, monitor, or retrieve a run."""

class IngestVerifyError(IngestBaseError):
    """Raised when verification cannot be performed due to invalid inputs or state."""


class DisClient:
    """Client for cross-workspace Databricks ingestion jobs.

    Attributes:
        dis_job_id: Databricks job ID for the ingestion service.
        job_parameters: Parameters passed to the ingestion job run.
        job_run_id: Databricks job run ID after submission.
        job_run_output: Raw Databricks job run output payload.
        task_run_id: Task run ID.
        task_run_output: Raw Databricks task run output payload.
        task_notebook_output: Parsed notebook output result payload.

    Use getter methods such as `get_job_run_state` and
    `get_nb_task_result_dst` for normalized/derived fields.
    """

    @validate_call(config=_VALIDATION_CONFIG)
    def __init__(
        self,
        host: str,
        azure_tenant_id: str,
        azure_client_id: str,
        azure_client_secret: str,
        azure_workspace_resource_id: str,
        dis_job_id: int,
    ) -> None:
        """Initialize a DIS client bound to a specific Databricks ingestion job.

        Args:
            host: Databricks workspace host URL (for example, ``https://adb-<id>.<region>.azuredatabricks.net``).
            azure_tenant_id: Azure AD tenant identifier used for service-principal auth.
            azure_client_id: Client/application ID for the service principal.
            azure_client_secret: Client secret for the service principal.
            azure_workspace_resource_id: Azure resource ID for the target Databricks workspace.
            dis_job_id: Job ID of the ingestion workflow invoked by :meth:`ingest`.

        Raises:
            IngestValueError: If any initialization argument fails pydantic validation.
        """
        try:
            logger.info("Creating Databricks job manager for host %s", host)
            self._job_manager = DatabricksJobManager(
                host=host,
                resource_id=azure_workspace_resource_id,
                spn_tenantid=azure_tenant_id,
                spn_id=azure_client_id,
                spn_secret=azure_client_secret,
            )
            self.dis_job_id = dis_job_id
            self._reset_run_state()
        except Exception as exc:
            raise IngestValueError(f"Invalid DisClient initialization arguments: {exc}") from exc

    def _reset_run_state(self) -> None:
        """Clear cached ingest and verification results from prior executions.

        This method is called during initialization and at the beginning of
        :meth:`ingest` to ensure each ingest cycle starts with a clean state.
        """
        self.ingest_result: IngestResult | None = None
        self.verify_result: VerifyResult | None = None


    #################################################################
    #
    # Resolve client template functions
    #
    #################################################################
    @staticmethod
    @validate_call(config=_VALIDATION_CONFIG)
    def resolve_config_template(
        config_template: str,
        template_params: dict[str, Any] | None = None,
    ) -> str:
        """Render a DIS config template using Jinja with strict undefined handling.

        The template uses ``[[ ... ]]`` for variables and ``[% ... %]`` for blocks
        to avoid collisions with JSON syntax.

        Args:
            config_template: Raw template content to render.
            template_params: Values injected into template variables.

        Returns:
            The fully-rendered config text.

        Raises:
            IngestValueError: If ``config_template`` is empty or whitespace, or 
            template variables are missing.
        """
        if not config_template.strip():
            raise IngestValueError("config_template must be a non-empty string.")

        render_params = {} if template_params is None else template_params

        env = Environment(
            undefined=StrictUndefined,
            variable_start_string="[[",
            variable_end_string="]]",
            block_start_string="[%", 
            block_end_string="%]",
            comment_start_string="[#",
            comment_end_string="#]",
            autoescape=False,
        )
        
        try:
            resolved_config = env.from_string(config_template).render(render_params)
            logger.debug("Resolved config template: %s", resolved_config)
        except Exception as exc:
            raise IngestValueError(f"Failed to resolve config template: {exc}") from exc

        return resolved_config


    #################################################################
    #
    # Ingest functions
    #
    #################################################################
    @validate_call(config=_VALIDATION_CONFIG)
    def ingest(
        self,
        *,
        config: str,
        rundate: date | None = None,
        params: dict[str, Any] | None = None,
        debug: bool = False,
        compress_notebook_output: bool = False,
        wait: bool = True,
        wait_timeout_sec: int = 3600,
        wait_poll_sec: int = 10,
    ) -> IngestResult:
        """Submit a DIS ingestion run and optionally wait for terminal state.

        Args:
            config: JSON string consumed by the ingestion job.
            rundate: Optional business run date sent as ``YYYY-MM-DD``.
            params: Optional JSON-serializable supplemental params.
            debug: Enables debug behavior expected by the notebook/job.
            compress_notebook_output: Requests compressed notebook result payload.
            wait: When ``True``, poll until job termination or timeout.
            wait_timeout_sec: Maximum wait duration when ``wait=True``.
            wait_poll_sec: Polling interval while waiting for job completion.

        Returns:
            Normalized :class:`IngestResult` with status and available run metadata.
            Status is ``RUNNING`` when submitted asynchronously and ``TIMEOUT`` when
            waiting exceeded ``wait_timeout_sec``.

        Raises:
            IngestValueError: If input arguments are invalid.
            IngestServiceError: If Databricks run submission, polling, or task lookup fails.
        """
        self._reset_run_state()

        if not config.strip():
            raise IngestValueError("config must be a non-empty string.")
        if not is_valid_json(config):
            raise IngestValueError("config must be a valid JSON string.")
        if wait_timeout_sec <= 0:
            raise IngestValueError("wait_timeout_sec must be greater than 0.")
        if wait_poll_sec <= 0:
            raise IngestValueError("wait_poll_sec must be greater than 0.")

        rundate_str = "" if rundate is None else rundate.strftime("%Y-%m-%d")
        params_str = "" if params is None else json.dumps(params)
        debug_str = str(debug).lower()
        zip_str = str(compress_notebook_output).lower()

        job_parameters = {
            "config": config,
            "rundate": rundate_str,
            "params": params_str,
            "debug": debug_str,
            "zip": zip_str,
        }
        logger.debug("Job parameter keys: %s", list(job_parameters.keys()))

        try:
            job_run_id = self._job_manager.run_now(
                job_id=self.dis_job_id,
                job_parameters=job_parameters,
            )
            logger.info("Ingestion job run ID: %s", job_run_id)
        except Exception as exc:
            raise IngestServiceError(f"Failed to call job {self.dis_job_id}.") from exc

        # Return immediately without waiting for job completion.
        if not wait:
            logger.info("Submitted ingestion job without waiting. run_id=%s", job_run_id)
            ingest_result = IngestResult(
                status="RUNNING",
                job_id=self.dis_job_id,
                job_parameters=job_parameters,
                job_run_id=job_run_id,
            )
            self.ingest_result = ingest_result
            return ingest_result

        # Wait on job termination
        try:
            job_run_output = self._job_manager.wait_for_job_terminated(
                job_run_id,
                poll_interval_s=wait_poll_sec,
                timeout_s=wait_timeout_sec,
            )
            logger.debug("Job run output: %s", job_run_output)
        except TimeoutError:
            # TIMEOUT
            ingest_result = IngestResult(
                status="TIMEOUT",
                job_id=self.dis_job_id,
                job_parameters=job_parameters,
                job_run_id=job_run_id,
            )
            self.ingest_result = ingest_result
            return ingest_result
        except Exception as exc:
            raise IngestServiceError(f"Failed while waiting for job completion: {exc}") from exc

        # Now, the job finishes running, either success or failed
        # Get task run ID
        task_run_id = DatabricksJobManager.extract_first_task_run_id(job_run_output)
        logger.info("Task run ID: %s", task_run_id)
        if task_run_id is None:
            raise IngestServiceError(f"Failed to extract task run ID from job run: {job_run_id}")

        # Get task run output
        try:
            task_run_output = self._job_manager.get_task_run(task_run_id)
            logger.debug("Task run output: %s", task_run_output)
        except Exception as exc:
            raise IngestServiceError(
                f"Failed to fetch task run output for run {job_run_id}: {exc}"
            ) from exc

        # Return ingest result with job status
        job_run_state = DatabricksJobManager.normalize_job_run_state(job_run_output)
        logger.info("Job run state: %s", job_run_state)
        ingest_result = IngestResult(
            status=job_run_state,
            job_id=self.dis_job_id,
            job_parameters=job_parameters,
            job_run_id=job_run_id,
            job_run_output=job_run_output,
            task_run_id=task_run_id,
            task_run_output=task_run_output,
        )
        self.ingest_result = ingest_result

        return ingest_result


    #################################################################
    #
    # File verification functions
    #
    #################################################################

    @staticmethod
    def _validate_expected_files(
        expected_files: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Validate and normalize expected file metadata entries.

        Each entry must provide:
        - ``path``: non-empty string.
        - ``size``: int-like value coercible to a non-negative integer.

        Args:
            expected_files: Raw expected file objects extracted from notebook output.

        Returns:
            New list containing normalized entries while preserving unknown keys.

        Raises:
            IngestValueError: If any entry is malformed.
        """
        validated: List[Dict[str, Any]] = []

        for index, expected in enumerate(expected_files):
            if not isinstance(expected, dict):
                raise IngestValueError(
                    f"Invalid expected file at index {index}: {expected!r}. "
                    "Entry must be a dict with 'path' and 'size'."
                )

            path = expected.get("path")
            if not isinstance(path, str) or not path.strip():
                raise IngestValueError(
                    f"Invalid expected file at index {index}: {expected!r}. "
                    "Entry must include non-empty string 'path'."
                )

            try:
                size_int = int(expected.get("size"))
            except (TypeError, ValueError) as exc:
                raise IngestValueError(
                    f"Invalid expected file at index {index}: {expected!r}. "
                    "Entry must include int-like 'size'."
                ) from exc

            if size_int < 0:
                raise IngestValueError(
                    f"Invalid expected file at index {index}: {expected!r}. "
                    "Entry must include non-negative 'size'."
                )

            normalized = dict(expected)
            normalized["path"] = path.strip()
            normalized["size"] = size_int
            validated.append(normalized)

        return validated

    @staticmethod
    def _remap_expected_files(
        expected_files: Sequence[Dict[str, Any]],
        dest_base: str,
        land_base: str,
    ) -> List[Dict[str, Any]]:
        """Translate expected destination paths to their corresponding landing paths.

        Args:
            expected_files: Validated expected-file metadata.
            dest_base: Source root prefix found in notebook-produced paths.
            land_base: Target root prefix where files should be verified.

        Returns:
            A remapped list with ``path`` values rewritten from ``dest_base`` to ``land_base``.

        Raises:
            IngestValueError: If an expected path is outside ``dest_base``.
        """
        remapped: List[Dict[str, Any]] = []

        for expected in expected_files:
            path = str(expected["path"])

            # the expected file must live under the destination base.
            prefixed = f"{dest_base}/"
            if not path.startswith(prefixed):
                raise IngestValueError(
                    "Invalid expected file path outside destination base directory: "
                    f"{path!r} is not under {dest_base!r}."
                )
            suffix = path[len(dest_base) :]
            remapped_path = f"{land_base}{suffix}".rstrip("/")

            remapped_item = dict(expected)
            remapped_item["path"] = remapped_path
            remapped.append(remapped_item)

        return remapped

    @staticmethod
    def _group_by_directory(
        expected_files: Sequence[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group expected files by parent directory for efficient directory listings.

        Args:
            expected_files: Expected files with normalized ``path`` values.

        Returns:
            Mapping of directory path to list of expected files in that directory.
        """
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for expected in expected_files:
            cleaned = str(expected["path"]).rstrip("/")
            directory = cleaned if "/" not in cleaned else cleaned.rsplit("/", 1)[0]
            grouped.setdefault(directory, []).append(expected)
        return grouped

    @validate_call(config=_VALIDATION_CONFIG)
    def verify_file_by_file_size(
        self,
        *,
        dest_dir_path: str | None = None,
        land_dir_path: str | None = None,
        timeout_sec: int = 300,
        poll_sec: int = 10,
        upload_task_key: str = "upload",
    ) -> VerifyResult:
        """Verify landed files by repeatedly matching expected paths and byte sizes.

        Expected files are loaded from the prior successful ingest result using
        ``upload_task_key``. Optionally, expected paths can be remapped from a
        destination base path to a landing base path before verification.

        Args:
            dest_dir_path: Destination base path from notebook output.
            land_dir_path: Landing base path to verify against.
            timeout_sec: Maximum verification duration before returning timeout.
            poll_sec: Delay between polling attempts.
            upload_task_key: Task key containing the expected file list in notebook output.

        Returns:
            :class:`VerifyResult` with ``status='success'`` when all files match,
            or ``status='timeout'`` when polling reaches the deadline first.

        Raises:
            IngestValueError: If verification arguments are invalid.
            IngestVerifyError: If ingest_result is not available.
        """

        # Verify timeout_sec and poll_sec
        if timeout_sec <= 0:
            raise IngestValueError("timeout_sec must be greater than 0.")
        if poll_sec <= 0:
            raise IngestValueError("poll_sec must be greater than 0.")

        # Verification only works after a completed successful run.
        if self.ingest_result is None:
            raise IngestVerifyError(
                "No ingest_result is available. Call ingest() before verification."
            )
        if self.ingest_result.status != "SUCCESS":
            raise IngestVerifyError(
                f"ingest_result status must be SUCCESS before verification. Current status: {self.ingest_result.status}"
            )

        # Get dst from specified task key
        expected = self.ingest_result.get_nb_task_result_dst(upload_task_key)
        if expected is None:
            raise IngestValueError(f"Task result not found or invalid for task: {upload_task_key}")
        if not expected:
            raise IngestValueError(f"No expected files found for task: {upload_task_key}")

        # Validate path and size in the list of expected files
        expected = self._validate_expected_files(expected)

        # Remap expected file path from uploaded destination path
        # Normalize path to None if none or empty
        dest_dir_path = dest_dir_path.strip() or None if dest_dir_path is not None else None
        land_dir_path = land_dir_path.strip() or None if land_dir_path is not None else None
        if dest_dir_path is not None and land_dir_path is not None:
            # Remap only if both provided
            dest_base = dest_dir_path.rstrip("/")
            land_base = land_dir_path.rstrip("/")
            # Replace dest_base with land_base
            expected = self._remap_expected_files(
                expected_files=expected,
                dest_base=dest_base,
                land_base=land_base,
            )
        elif (dest_dir_path is None) != (land_dir_path is None):
            # Raise if dest and land path not provided together
            raise IngestValueError(
                "dest_dir_path and land_dir_path must be provided together."
            )

        # Group expected files for verification for better performance
        grouped = self._group_by_directory(expected)

        # Timer is on now
        deadline = time.time() + timeout_sec
        
        # Keep checking file size until time out
        while True:
            matched: List[Dict[str, Any]] = []
            unmatched: List[Dict[str, Any]] = []

            # List each parent directory once per poll cycle, then compare all
            # expected files in that directory against the current listing.
            for directory, files in grouped.items():
                try:
                    entries = dbutils.fs.ls(directory)
                    listing = {entry.path: int(entry.size) for entry in entries}
                except Exception as exc:
                    logger.debug("Failed to list directory %s: %s", directory, exc)
                    listing = {}

                for expected_file in files:
                    expected_path = str(expected_file["path"])
                    expected_size = int(expected_file["size"])
                    found_size = listing.get(expected_path)

                    if found_size == expected_size:
                        matched.append(expected_file)
                    else:
                        unmatched.append(expected_file)

            # Return verify_result if no more unmatched
            if not unmatched:
                logger.info("All expected files matched size expectations.")
                self.verify_result = VerifyResult(
                    status="SUCCESS",
                    expected=expected,
                    matched=matched,
                    unmatched=unmatched,
                )
                return self.verify_result

            # Stop checking if timeout
            now = time.time()
            if now >= deadline:
                logger.warning(
                    "File verification timed out after %s seconds. matched=%s unmatched=%s",
                    timeout_sec, len(matched), len(unmatched),
                )
                self.verify_result = VerifyResult(
                    status="TIMEOUT",
                    expected=expected,
                    matched=matched,
                    unmatched=unmatched,
                )
                return self.verify_result

            time.sleep(min(poll_sec, max(0.0, deadline - now)))
