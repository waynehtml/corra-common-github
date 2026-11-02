"""Environment metadata helpers for Databricks workspaces."""

import logging
import platform
from functools import lru_cache
from typing import Dict, Any, Optional
import posixpath
import json

__all__ = ["ENV"]

logger = logging.getLogger(__name__)

class Environment:
    """Provide environment-specific constants and notebook context helpers."""

    ENVIRONMENTS = {
        "adb-1700301391505163.3.azuredatabricks.net": {
            "NAME": "dev",
            "LANDST1": "lakecacnpdstdevland01",
            "LANDST2": "lakecacnpdstdevland02",
            "LAKEST": "lakecacnpdstdev01",
            "DATAMART": [{"URL":"https://api-dev.bank-banque-canada.ca/datamart/v1/api/uc/pipeline/ingest",
                          "SCOPE":"api://1dae7513-113d-460e-a2a8-3f4045ca7e25/.default"}],
        },
        "adb-13344226264609.9.azuredatabricks.net": {
            "NAME": "stg",
            "LANDST1": "lakecacprdststgland01",
            "LANDST2": "lakecacprdststgland02",
            "LAKEST": "lakecacprdststg01",
            "DATAMART": [{"URL":"https://api-stg.bank-banque-canada.ca/datamart/v1/api/uc/pipeline/ingest",
                          "SCOPE":"api://22c7a03f-742e-457a-a90a-5008c243e925/.default"}],
        },
        "adb-642333651898149.9.azuredatabricks.net": {
            "NAME": "prd",
            "LANDST1": "lakecacprdstprdland01",
            "LANDST2": "lakecacprdstprdland02",
            "LAKEST": "lakecacprdstprd01",
            "DATAMART": [{"URL":"https://api.bank-banque-canada.ca/datamart/v1/api/uc/pipeline/ingest",
                          "SCOPE":"api://a685fe05-33c2-4c83-b97e-c5a0981bbb4b/.default"},
                         {"URL":"https://api-cae.bank-banque-canada.ca/datamart/v1/api/uc/pipeline/ingest",
                          "SCOPE":"api://8b1fcf20-b910-4ec7-a653-d040bb5b2879/.default"}],
        },
    }

    @lru_cache(maxsize=1)
    def _get_config(self) -> Dict[str, Any]:
        """Fetch and cache the configuration for the active workspace.

        Returns:
            Configuration dictionary for the current workspace environment.
        """
        workspaceUrl = self.WORKSPACE_URL
        logger.debug(f"workspaceUrl is {workspaceUrl}")
        if not workspaceUrl:
            raise RuntimeError("No workspace URL found. This helper requires a Databricks cluster.")
        return self.ENVIRONMENTS[workspaceUrl]

    def __getattr__(self, name: str) -> Any:
        """Resolve configuration keys as attributes.

        Args:
            name: Attribute name to retrieve from the environment configuration.

        Returns:
            Value associated with the requested configuration key.

        Raises:
            AttributeError: If the configuration key is not defined.
        """
        config = self._get_config()
        if name in config:
            return config[name]
        raise AttributeError(f"'{self.__class__.__name__}' has no attribute '{name}'")

    def __repr__(self) -> str:
        # use config NAME (or _get_env()) to avoid referencing a non-existent self.env
        return f"ENVIRONMENTS(env='{self._get_config()['NAME']}')"

    def __dir__(self):
        """List attributes including configuration keys."""
        try:
            config_keys = list(self._get_config().keys())
        except (RuntimeError, ValueError):
            config_keys = []
        return sorted(set(dir(self.__class__) + config_keys))

    # --- Properties from notebook context ---
    def _notebook_ctx(self):
        """Return the current notebook context Java object.

        Returns:
            Java notebook context object for the active notebook.
        """
        try:
            from databricks.sdk.runtime import dbutils
        except Exception:
            raise RuntimeError("Failed to import dbutils.")

        return dbutils.notebook.entry_point.getDbutils().notebook().getContext()

    def _notebook_ctx_safe_get(self, option):
        """Safely unwrap a Scala ``Option`` into a string representation.

        Args:
            option: Scala ``Option`` object obtained from the notebook context.

        Returns:
            String value if defined; otherwise ``None``.
        """
        try:
            if option is not None and option.isDefined():
                return str(option.get())
        except Exception:
            pass
        return None

    # Properties from Notebook Context
    @property
    def CURRENT_NOTEBOOK_PATH(self) -> Optional[str]:
        """Raw path of the current notebook (may be repo-relative)."""
        return "/Workspace" + self._notebook_ctx_safe_get(self._notebook_ctx().notebookPath())

    @property
    def CURRENT_NOTEBOOK_NAME(self) -> Optional[str]:
        """Filename portion of the current notebook path."""
        return posixpath.basename(self.CURRENT_NOTEBOOK_PATH) if self.CURRENT_NOTEBOOK_PATH else None

    @property
    def CURRENT_DIR(self) -> Optional[str]:
        """Directory containing the current notebook."""
        return posixpath.dirname(self.CURRENT_NOTEBOOK_PATH) if self.CURRENT_NOTEBOOK_PATH else None
    
    @property
    def WORKSPACE_URL(self) -> Optional[str]:
        """
        Workspace URL for the current environment.
        Recommend getting this property from job parameters if running as a job.
        """
        return self._notebook_ctx_safe_get(self._notebook_ctx().browserHostName())

    @property
    def WORKSPACE_ID(self) -> Optional[str]:
        """
        Workspace ID for the current environment.
        Recommend getting this property from job parameters if running as a job.
        """
        return self._notebook_ctx_safe_get(self._notebook_ctx().workspaceId())

    @property
    def CURRENT_USER(self) -> Optional[str]:
        """
        Username executing the current notebook.
        Recommend getting this property from job parameters if running as a job.
        """
        return self._notebook_ctx_safe_get(self._notebook_ctx().userName())

    @property
    def CURRENT_JOB_ID(self) -> Optional[str]:
        """
        Databricks job ID for the current run, if available.
        Recommend getting this property from job parameters if running as a job.
        """
        return self._notebook_ctx_safe_get(self._notebook_ctx().jobId())
    
    @property
    def CURRENT_JOB_RUNID(self) -> Optional[str]:
        """Databricks job run ID for the current run, if available."""
        return self._notebook_ctx_safe_get(self._notebook_ctx().jobRunId())


    @property
    def PYTHON_VERSION(self) -> Optional[str]:
        """Python version for the current runtime."""
        return str(platform.python_version())
    
    @property
    def SPARK_VERSION(self) -> Optional[str]:
        """Spark version for the current runtime. """

        try:
            from pyspark.sql import SparkSession
            spark_session = SparkSession.getActiveSession()
        except Exception:
            raise

        if spark_session is None:
            raise RuntimeError("An active seesion is not available.")

        return spark_session.version

# Global instance
ENV = Environment() 
