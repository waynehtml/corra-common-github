from __future__ import annotations

import base64
import json
import logging
import zlib
from dataclasses import dataclass
from typing import Any, Literal

import jmespath

from oeds.core.job_utils import DatabricksJobManager

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Structured ingestion run result."""

    status: Literal["RUNNING", "SUCCESS", "FAILED", "TIMEOUT"]
    job_id: int
    job_parameters: dict[str, str] | None = None
    job_run_id: int | None = None
    job_run_output: dict[str, Any] | None = None
    task_run_id: int | None = None
    task_run_output: dict[str, Any] | None = None
    task_notebook_output: dict[str, Any] | None = None

    def get_status(self) -> Literal["RUNNING", "SUCCESS", "FAILED", "TIMEOUT"]:
        return self.status
    
    def get_job_id(self) -> int:
        return self.job_id

    def get_job_parameters(self) -> dict[str, str] | None:
        return self.job_parameters

    def get_job_run_id(self) -> int | None:
        return self.job_run_id

    def get_job_run_output(self) -> dict[str, Any] | None:
        return self.job_run_output

    def get_job_run_page_url(self) -> str | None:
        if isinstance(self.job_run_output, dict):
            run_page_url = self.job_run_output.get("run_page_url")
            if isinstance(run_page_url, str):
                return run_page_url
        return None

    def get_task_run_id(self) -> int | None:
        return self.task_run_id

    def get_task_run_output(self) -> dict[str, Any] | None:
        return self.task_run_output

    def get_task_run_error(self) -> str | None:
        if isinstance(self.task_run_output, dict):
            return DatabricksJobManager.normalize_task_run_error(self.task_run_output)
        return None

    def get_task_run_error_trace(self) -> str | None:
        if isinstance(self.task_run_output, dict):
            error_trace = self.task_run_output.get("error_trace")
            if isinstance(error_trace, str):
                return error_trace
        return None

    @staticmethod
    def _extract_task_notebook_output_dict(main_task_output: dict[str, Any]) -> dict[str, Any]:
        """Parse notebook output into a dict, handling optional zlib compression."""
        result = DatabricksJobManager.extract_notebook_output_result(main_task_output)
        if result is None:
            raise ValueError("Notebook output result is missing or unavailable.")

        try:
            if isinstance(result, str) and result.startswith("zlib:"):
                encoded_payload = result.removeprefix("zlib:")
                compressed_payload = base64.b64decode(encoded_payload)
                result = zlib.decompress(compressed_payload).decode("utf-8")

            parsed_payload = json.loads(result)
        except Exception as exc:
            raise ValueError("Notebook output result could not be parsed as JSON.") from exc

        if not isinstance(parsed_payload, dict):
            raise ValueError("Notebook output result must be a JSON object.")

        return parsed_payload

    def get_task_notebook_output(self) -> dict[str, Any] | None:
        if isinstance(self.task_notebook_output, dict):
            return self.task_notebook_output

        task_notebook_output = None
        if isinstance(self.task_run_output, dict):
            try:
                task_notebook_output = IngestResult._extract_task_notebook_output_dict(
                    self.task_run_output
                )
            except Exception:
                task_notebook_output = None

        if isinstance(task_notebook_output, dict):
            self.task_notebook_output = task_notebook_output
        return task_notebook_output

    def get_nb_task_result(self, task_id: str) -> dict[str, Any] | None:
        if not task_id.strip():
            raise ValueError("task_id must be a non-empty string.")

        task_notebook_output = self.get_task_notebook_output()
        if not isinstance(task_notebook_output, dict):
            return None

        task_key = task_id.strip()
        task_result = jmespath.search(
            f'task_results."{task_key}"',
            task_notebook_output,
        )
        if not isinstance(task_result, dict):
            return None

        return task_result

    def get_nb_task_result_dst(self, task_id: str) -> list[dict[str, Any]] | None:
        task_result = self.get_nb_task_result(task_id=task_id)
        logger.debug("task_result: %s", task_result)
        if task_result is None:
            return None

        dst_list = jmespath.search("outputs[].dest", task_result)
        if dst_list is None:
            return []

        if not isinstance(dst_list, list):
            return None

        if not all(isinstance(dst, dict) for dst in dst_list):
            return None

        return [dict(dst) for dst in dst_list]

    def get_nb_task_result_dst_path(self, task_id: str) -> list[str] | None:
        task_result = self.get_nb_task_result(task_id=task_id)
        logger.debug("task_result: %s", task_result)
        if task_result is None:
            return None

        path_list = jmespath.search("outputs[].dest.path", task_result)
        if path_list is None:
            return []

        if not isinstance(path_list, list):
            return None

        return [str(path) for path in path_list]
