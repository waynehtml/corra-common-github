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

# DBTITLE 1,Set flow_action
# Set default flow_action as continue. 
# In case that you want to exit the whole pipeline gracefully, set the value to "complete". 
try:
    dbutils.jobs.taskValues.set(key="flow_action", value="continue")
except Exception:
    pass

# COMMAND ----------

# Standard libraries
from datetime import date, datetime
import json
import uuid
from pathlib import Path

# OEDS common libraries
from oeds.core.misc_utils import param
from oeds.core.env_utils import ENV

# COMMAND ----------

def parse_bool(value: str, default: bool = False) -> bool:
    """
    Parse a truthy/falsey string to bool. If parsing fails, return default.
    """
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n"):
        return False
    return default

def parse_date(date_str: str, python_date_format: str, default=None) -> date:

    if not date_str:
        return default
    try:
        return datetime.strptime(date_str, python_date_format).date()
    except ValueError as exc:
        raise ValueError(
            f"Invalid date format '{date_str}'. Expected format: {python_date_format}"
        ) from exc

def parse_json(value: str):
    """
    Parse a JSON string.

    - If value is empty or only whitespace, return {}.
    - If parsing fails, raise
    """
    if value is None:
        return {}

    text = str(value).strip()
    if not text:
        return {}

    return json.loads(text)

# COMMAND ----------

# DBTITLE 1,Build job context
def get_job_context():
    ctx = {}

    # ------------------------------------------------------------------
    # Base task parameters
    # ------------------------------------------------------------------
    # Dataset name
    ctx["dataset"] = param("dataset", "template").strip().lower()

    # Environment
    ctx["env"] = param("env", "dev").strip().lower()

    # Business notification email list
    job_notify_emails_str = param(
        "job_notify_emails",
        '{"DDA":"dda-support-dev@bank-banque-canada.ca","DM":"dda-support-dev@bank-banque-canada.ca"}'
    )
    ctx["job_notify_emails"] = parse_json(job_notify_emails_str)

    # Time zone / date formats
    ctx["timezone"] = param("timezone", "America/Toronto").strip()
    ctx["human_date_format"] = param("human_date_format", "YYYY-MM-DD")
    ctx["python_date_format"] = param("python_date_format", "%Y-%m-%d")
    ctx["spark_date_format"] = param("spark_date_format", "yyyy-MM-dd")

    # Standard volume paths
    env = ctx["env"]
    ctx["land_root"] = param(
        "land_root",
        f"/Volumes/dev_application_cat/oedisint_db/land-oedataservices/dis/template/data/land"
    )
    ctx["config_root"] = param(
        "config_root",
        f"/Volumes/dev_application_cat/oedisint_db/land-oedataservices/dis/template/data/conifg"
    )
    ctx["raw_root"] = param(
        "raw_root",
        f"/Volumes/dev_application_cat/oedisint_db/land-oedataservices/dis/template/data/raw"
    )

    # project/default secrete scope
    ctx["secrete_scope"] = param("secrete_scope","lake-kv-oedataservices-scope")

    # email service URL key
    ctx["email_service_url_key"] = param("email_service_url_key","oeds-email-service-url")
    ctx["email_service_url"] = dbutils.secrets.get(
        scope=ctx["secrete_scope"], 
        key=ctx["email_service_url_key"])

    # Service SPN
    ctx["azure_tenant_id_key"] = param(
        "azure_tenant_id_key",
        "app-2b501945-5940-4527-9cdd-51a07b77ceb7-tenantid"
    )
    ctx["azure_tenant_id"] = dbutils.secrets.get(
        scope=ctx["secrete_scope"], 
        key=ctx["azure_tenant_id_key"])

    ctx["service_spn_id_key"] = param(
        "service_spn_id_key",
        "app-2b501945-5940-4527-9cdd-51a07b77ceb7-appid"
    )
    ctx["service_spn_id"] = dbutils.secrets.get(
        scope=ctx["secrete_scope"], 
        key=ctx["service_spn_id_key"])

    ctx["service_spn_secrete_key"] = param(
        "service_spn_secrete_key",
        "app-2b501945-5940-4527-9cdd-51a07b77ceb7-secret"
    )
    ctx["service_spn_secrete"] = dbutils.secrets.get(
        scope=ctx["secrete_scope"],
        key=ctx["service_spn_secrete_key"])

    # Databricks workspace/resource
    ctx["internal_databricks_workspace_url"] = param(
        "internal_databricks_workspace_url",
        "https://adb-1700301391505163.3.azuredatabricks.net"
    )
    ctx["internal_databricks_resource_id"] = param(
        "internal_databricks_resource_id",
        "/subscriptions/1de5fd2d-2313-461a-acc4-07cdc509cdf4/resourceGroups/lake-cac-npd-rg-devplatform/providers/Microsoft.Databricks/workspaces/lake-cac-npd-dbw-devuc01"
    )
    internal_dis_job_id_str = param(
        "internal_dis_job_id",
        "140598343443137"
    )
    ctx["internal_dis_job_id"] = int(internal_dis_job_id_str)
    ctx["external_databricks_workspace_url"] = param(
        "external_databricks_workspace_url",
        "https://adb-7405611352003885.5.azuredatabricks.net"
    )
    ctx["external_databricks_resource_id"] = param(
        "external_databricks_resource_id",
        "/subscriptions/1de5fd2d-2313-461a-acc4-07cdc509cdf4/resourceGroups/lake-cac-npd-rg-devplatform/providers/Microsoft.Databricks/workspaces/lake-cac-npd-dbw-devdmz01"
    )
    external_dis_job_id_str = param("external_dis_job_id", "0")
    ctx["external_dis_job_id"] = int(external_dis_job_id_str)

    # Platform / infrastructure
    datamart_endpoints_str = param(
        "datamart_endpoints",
        '[{"URL":"https://api-dev.bank-banque-canada.ca/datamart/v1/api/uc/pipeline/ingest", "SCOPE":"api://1dae7513-113d-460e-a2a8-3f4045ca7e25/.default"}]'
    )
    ctx["datamart_endpoints"] = parse_json(datamart_endpoints_str)
    #
    ctx["nexus_url"] = param(
        "nexus_url",
        "https://fcp4lnexuap01.bocad.bank-banque-canada.ca/nexus/repository/pypi-all/simple"
    )
    ctx["dbk_runtime_version"] = param("dbk_runtime_version", "16.4.x-scala2.13")

    # Properties provided by Databricks job runtime
    ctx["job_name"] = param("job_name", "OEDS-SAMPLE-JOB")
    ctx["job_id"] = param("job_id", "123456789")
    ctx["job_run_id"] = param("job_run_id", str(uuid.uuid4()))
    ctx["job_trigger_type"] = param("job_trigger_type", "manual")
    ctx["workspace_url"] = param("workspace_url", "")

    # ------------------------------------------------------------------
    # Properties passed by job parameters
    # ------------------------------------------------------------------
    # Run date, default as today if empty
    rundate_str = param("rundate", "").strip()
    ctx["rundate"] = parse_date(rundate_str, ctx["python_date_format"], default=date.today())
    # Debug mode
    debug_str = param("debug", "true").strip()
    ctx["debug"] = parse_bool(debug_str, default=False)
    # task
    ctx["task"] = param("task", "").strip()
    # args must be a valid JSON string
    args_str = param("args", "").strip()
    ctx["args"] = parse_json(args_str)

    # ------------------------------------------------------------------
    # Properties from current execution context
    # ------------------------------------------------------------------
    ctx["current_user"] = param("current_user", ENV.CURRENT_USER)
    ctx["current_notebook_path"] = param("current_notebook_path", ENV.CURRENT_NOTEBOOK_PATH)
    ctx["current_notebook_dir"] = param("current_notebook_dir", ENV.CURRENT_DIR)
    # We assume all notebooks that will include common are in the ajecent sub folder.
    ctx["job_root_dir"] = str(Path(ctx["current_notebook_dir"]).parent)
    ctx["job_src_dir"] = str(Path(ctx["job_root_dir"]) / "src")

    # ------------------------------------------------------------------
    # Wheelhouse setup for nexus resilence
    # ------------------------------------------------------------------
    ctx["cluster_init_path"] = param("cluster_init_path", "")
    ctx["cluster_wheelhouse_path"] = param("cluster_wheelhouse_path", "")

    # ------------------------------------------------------------------
    # constants
    # ------------------------------------------------------------------
    ctx["metadata_cat"] = f'{ctx["env"]}_metadata_cat'

    return ctx

# COMMAND ----------

# Get job context
_ctx = get_job_context()

# COMMAND ----------

# Set up logging
import logging
import oeds.core.log_utils as logutils
log_level = "debug" if _ctx.get("debug") else "info"
logutils.setup_logger(level=log_level)
logger = logging.getLogger(__name__)

# COMMAND ----------

# Add the src directory into Python import path
import sys
src_path = _ctx.get("job_src_dir")
if str(src_path) not in sys.path:
    logger.info(f"Adding import path: {src_path}")
    sys.path.insert(0, str(src_path))

# COMMAND ----------

# Initialize email manager
from oeds_workflow.utils.email.email_utils import EmailManager

# Get email_service_url from OEDS secrete scope
secrete_scope = _ctx.get("secrete_scope")
logger.debug(f"secrete_scope is {secrete_scope}")
email_service_url_key = _ctx.get("email_service_url_key")
logger.debug(f"email_service_url_key is {email_service_url_key}")
email_service_url = dbutils.secrets.get(scope=secrete_scope, key=email_service_url_key)
logger.debug(f"email_service_url is {email_service_url}")

# Get email mappings
email_map = _ctx.get("job_notify_emails")
logger.debug(f"email_map is {email_map}")

# Initiate email manager
logger.info(f"Initiating email manager ...")
email_manager = EmailManager(email_service_url=email_service_url, email_map=email_map)
