# Databricks notebook source
# Purpose:
# - Automatically reloads imported Python modules when their source code changes.
# - Makes iterative development faster: you can edit code in workspace files (or repo),
#   re-run cells, and your changes take effect without restarting the notebook/kernel.
#
# Notes:
# - Best for interactive/dev workflows.
# - In production job runs, you can remove or disable to reduce surprises and improve reproducibility:
#   use `%autoreload 0`.
try:
    from IPython import get_ipython

    ipython = get_ipython()
    if ipython:
        ipython.run_line_magic("load_ext", "autoreload")
        ipython.run_line_magic("autoreload", "2")
except Exception:
    # Safe fallback when IPython is unavailable (e.g., scheduled jobs or unit tests).
    pass

# COMMAND ----------

# Python standard libraries
import base64
import json
import logging
import shutil
import zlib
from zoneinfo import ZoneInfo
from dataclasses import is_dataclass, asdict
from datetime import date, datetime
from importlib import resources
from pathlib import Path
from uuid import UUID, uuid4

from jsonschema import FormatChecker, validate
from jsonschema.exceptions import ValidationError

# oeds common modules
from oeds.core.misc_utils import param
from oeds.core.log_utils import setup_logger

# oedis modules
from oedis.engine import RaptorEngine
from oedis.exceptions import (
    ConfigResolutionError,
    PipelineExecutionError,
    RuntimeParamError,
    SchemaValidationError,
    TaskException,
)

# oedis packages
from oedis.task_result import TaskResult
from oedis.common.utils import concat_exception_messages, json_dumps
from oedis.template_functions import _TEMPLATE_FUNCTIONS
from oedis.common.template_resolver import resolve_config
from oedis.runtime import Runtime

# COMMAND ----------

# MAGIC %md
# MAGIC **Parameter contract expected by this notebook when launched as a Databricks job:**
# MAGIC - config: ingestion configuration template string. In non-local environments,
# MAGIC   the caller must pass this as a serialized JSON template.
# MAGIC - rundate: optional run date (`YYYY-MM-DD`). If not provided, defaults to current date in runtime timezone.
# MAGIC - zip: control if ontrols whether notebook output payload is compressed.
# MAGIC - debug: optional boolean-like switch (true/false).
# MAGIC   When true, effective log level is debug; otherwise info.
# MAGIC - params: JSON string containing runtime ingestion options supplied by caller.
# MAGIC   All parameters passed from clients are kept under `runtime.job_params`
# MAGIC   for config resolution and handler usage.
# MAGIC

# COMMAND ----------

# DBTITLE 1,Interactive widget input
# FOR DEVELPMENT ONLY
dbutils.widgets.removeAll()
dbutils.widgets.text("env", "loc")
dbutils.widgets.text("config", "")
dbutils.widgets.text("rundate", "")
dbutils.widgets.text("params","{}")
dbutils.widgets.text("zip", "false")
dbutils.widgets.dropdown("debug", "true", ["false", "true"])
dbutils.widgets.text("temp_dir", "/tmp/oedis")
dbutils.widgets.text("land_dir", "/Volumes/dev_application_cat/oedisint_db/land-oedataservices/oedis")
dbutils.widgets.text("config_dir", "/Volumes/dev_application_cat/oedisint_db/config-oedataservices/oedis")
dbutils.widgets.text("nexus_url","https://fcp4lnexuap01.bocad.bank-banque-canada.ca/nexus/repository/pypi-all/simple")

# COMMAND ----------

# DBTITLE 1,Set up logging
# Configure structured logging early so every step is captured in job logs
setup_logger(level="info")
logger = logging.getLogger(__name__)

# COMMAND ----------

# DBTITLE 1,Get job parameters
## Client parameters
# config: config in json string
config_str = str(param("config", "")).strip()
logger.info(f"config_str: {config_str}")

# rundate
rundate_str = param("rundate", "")
logger.info(f"rundate: {rundate_str}")

# zip: defaults to False; controls whether notebook output payload is compressed.
zip_str = str(param("zip", "false")).strip().lower()
zip = zip_str in {"1", "true", "t", "yes", "y", "on"}
logger.info(f"zip is {zip}")

# debug: defaults to True, set log at debug level
debug_str = str(param("debug", "true")).strip().lower()
debug = debug_str in {"1", "true", "t", "yes", "y", "on"}
logger.info(f"debug: {debug}")

# Task specific parameters passed through runtime.job_params.
params_str = param("params", "")
logger.info(f"params: {params_str}")

## System job parameters
# Job level - mainly for clients
env = param("env", "dev")
logger.info(f"env: {env}")
timezone = param("timezone", "America/Toronto")
logger.info(f"timezone: {timezone}")
temp_dir = param("temp_dir", "/tmp/oedis")
logger.info(f"temp_dir: {temp_dir}")
land_dir = param("land_dir", "/Volumes/dev_application_cat/oedisint_db/land-oedataservices/oedis")
logger.info(f"land_dir: {land_dir}")
config_dir = param("config_dir", "/Volumes/dev_application_cat/oedisint_db/config-oedataservices/oedis")
logger.info(f"config_dir: {config_dir}")
nexus_url = param("nexus_url", "https://fcp4lnexuap01.bocad.bank-banque-canada.ca/nexus/repository/pypi-all/simple")
logger.info(f"nexus_url: {nexus_url}")

# Task level
job_name = param("job_name", f"test")
logger.info(f"job_name: {job_name}")
job_user = param("job_user", "duwe@bank-banque-canada.ca")
logger.info(f"job_user: {job_user}")
job_id = param("job_id", "9999")
logger.info(f"job_id: {job_id}")
job_run_id = param("job_run_id", uuid4().hex)
logger.info(f"job_run_id: {job_run_id}")
job_trigger_type = param("job_trigger_type", "manual")
logger.info(f"job_trigger_type: {job_trigger_type}")
workspace_url = param("workspace_url", "https://adb-1700301391505163.3.azuredatabricks.net/?o=1700301391505163")
logger.info(f"workspace_url: {workspace_url}")

# COMMAND ----------

# DBTITLE 1,Set up logging
try:
    setup_logger(level="DEBUG" if debug else "INFO")
except Exception:
    logger.warning(f"Invalid debug value: {debug}, using default.")

# COMMAND ----------

# DBTITLE 1,Prepare and initiate Runtime
# Config is missing
if len(config_str) == 0:
    raise RuntimeParamError("Missing required 'config' parameter.")

# Determine inbox locations.
inbox_dir_path = Path(temp_dir) / "stage" / job_run_id
inbox_vol_path = Path(land_dir) / "stage" / job_run_id
logger.info(f"Inbox dir (local): {inbox_dir_path}")
logger.info(f"Inbox vol (volume): {inbox_vol_path}")

# rundate: defaults to today in runtime timezone.
try:
    if not rundate_str:
        rundate = datetime.now(ZoneInfo(timezone)).date()
    else:
        rundate = datetime.strptime(rundate_str, "%Y-%m-%d").date()
    logger.info(f"rundate is {rundate}")
except ValueError as exc:
    err_msg = concat_exception_messages(exc)
    raise RuntimeParamError(f"Failed to parse rundate '{rundate_str}'. Expected format YYYY-MM-DD. {err_msg}") from exc

# params: Parse params from string to dict
try:
    if len(params_str) == 0:
        params = {}
    else:
        params = json.loads(params_str)

    if not isinstance(params, dict):
        raise ValueError("'params' must deserialize to a JSON object")

    logger.info(f"params is {params}")
except Exception as exc:
    err_msg = concat_exception_messages(exc)
    raise RuntimeParamError(f"Failed to parse params: {err_msg}") from exc

# Initialize runtime
runtime = Runtime(
    env=env,
    rundate=rundate,
    job_params=params,
    debug=debug,
    timezone=timezone,
    inbox_dir=str(inbox_dir_path),
    inbox_vol=str(inbox_vol_path),
    config_dir = config_dir,
    nexus_url = nexus_url,
    job_name=job_name,
    job_user=job_user,
    job_id=job_id,
    job_run_id=job_run_id,
    job_trigger_type=job_trigger_type,
    workspace_url=workspace_url,
)

# COMMAND ----------

# DBTITLE 1,Run maintenance notebook
# Run maintenance notebook
if config_str.startswith("/"):
    try:
        logger.info(f"Running notebook: {config_str}")
        result = dbutils.notebook.run(
            config_str,
            0,
            runtime.to_dict()
        )
        dbutils.notebook.exit(result)
    except Exception as e:
        raise RuntimeError(f"Notebook run failed: {config_str}") from e

# COMMAND ----------

# DBTITLE 1,Resolve config template
# FOR TESTING ONLY: load config locally using config parameter as a file name
if not config_str.startswith("{"):
    # Read sample config file during development
    config_path = "/Workspace" + dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get().rsplit('/', 1)[0] + f"/../tests/config/{config_str}.json"
    logger.info(f"Try to load config from {config_path} for local testing")
    with open(config_path, "r") as config_file:
        config_str = config_file.read()

# Config must be a valid json string
try:
    config_template = json.loads(config_str)
except Exception as exc:
    err_msg = concat_exception_messages(exc)
    raise ConfigResolutionError(f"Config is not a valid json string: {err_msg}") from exc

# Resolve config template with runtime
try:
    config_resolved = resolve_config(
        config_template,
        render_context={
            "runtime": runtime,
            **_TEMPLATE_FUNCTIONS,
        },
    )
    logger.debug(f"Resolved config template: {config_resolved}")
except Exception as exc:
    err_msg = concat_exception_messages(exc)
    raise ConfigResolutionError(f"Failed to resolve ingestion config: {err_msg}") from exc

# COMMAND ----------

# DBTITLE 1,Start Engine and return result
# Setup staging area
# Create local staging inbox (task handlers continue to use runtime.inbox/inbox_dir).
try:
    dbutils.fs.mkdirs(inbox_dir_path.as_posix())
except Exception as exc:
    err_msg = concat_exception_messages(exc)
    raise RuntimeParamError(
        f"Failed to create/access inbox_dir at '{inbox_dir_path}': {err_msg}"
    ) from exc
# Best-effort create volume inbox for callers that need runtime.inbox_vol.
try:
    dbutils.fs.mkdirs(inbox_vol_path.as_posix())
except Exception as exc:
    err_msg = concat_exception_messages(exc)
    logger.warning("Unable to create/access inbox_vol at '%s': %s", inbox_vol_path, err_msg)


# Start enging and process ingestion pipeline
try:
    # Initiate Engine
    engine = RaptorEngine(
        runtime=runtime,
        config_resolved=config_resolved,
        config_template=config_template,
    )

    # Start Engine
    ctx = engine.run()

    # Process task results
    if ctx is not None:
        # force ctx.task_results into a plain JSON-compatible Python structure
        task_results = json.loads(json_dumps(ctx.task_results))
        payload = {
            "status": "success",
            "task_results": task_results,
        }
        payload_json = json_dumps(payload)
        logger.info(f"Payload: {payload_json}")

        if zip:
            compressed_payload = zlib.compress(payload_json.encode("utf-8"))
            encoded_payload = base64.b64encode(compressed_payload).decode("ascii")
            payload_json = f"zlib:{encoded_payload}"
    else:
        raise RuntimeError("Ingestion workflow failed: missing context.")

except TaskException as exc:
    err_msg = concat_exception_messages(exc)
    raise PipelineExecutionError(f"Ingestion workflow failed due to task error: {err_msg}") from exc
except Exception as exc:
    err_msg = concat_exception_messages(exc)
    raise PipelineExecutionError(f"Ingestion workflow failed: {err_msg}") from exc
finally:
    # Clean up staging area
    if runtime.debug:
        logger.info("Debug mode enabled. Leaving inbox dir in place: %s", runtime.inbox_dir)
        logger.info("Debug mode enabled. Leaving inbox vol in place: %s", runtime.inbox_vol)
    else:
        if runtime.inbox_dir:
            shutil.rmtree(runtime.inbox_dir, ignore_errors=True)
            logger.info("Cleaned up inbox dir: %s", runtime.inbox_dir)

        if runtime.inbox_vol:
            try:
                dbutils.fs.rm(runtime.inbox_vol, recurse=True)
                logger.info("Cleaned up inbox vol: %s", runtime.inbox_vol)
            except Exception as exc:
                err_msg = concat_exception_messages(exc)
                logger.warning("Unable to cleanup inbox vol '%s': %s", runtime.inbox_vol, err_msg)

# COMMAND ----------

# DBTITLE 1,Return task results
# Pass task results to caller
# keep exit in a sepearte cell so that the above cell could show logs
dbutils.notebook.exit(payload_json)
