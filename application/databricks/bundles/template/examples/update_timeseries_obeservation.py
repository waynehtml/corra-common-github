# Databricks notebook source
# MAGIC %run ../common

# COMMAND ----------

# Standard libraries
import logging
# Get logger
logger = logging.getLogger(__name__)

# Get job context
job_ctx = get_job_context()
logger.debug(f"job_ctx: {job_ctx}")

# COMMAND ----------

from datetime import date

from oeds.core.auth_utils import AuthUtils
from oeds_workflow.utils.schema.schema_utils import SchemaManager
from oeds_workflow.utils.timeseries.timeseries_utils import TimeSeriesUtils

# COMMAND ----------

# Initialize Context for Time serires manager
tsm_ctx={
    "metadata_catalog": job_ctx.get("metadata_cat"),
    "pipeline_name": job_ctx.get("job_name"),
    "notebook_path": job_ctx.get("current_notebook_path"),
    "current_user": job_ctx.get("current_user"),
    "job_id": job_ctx.get("job_id"),
    "job_runid": job_ctx.get("job_run_id"),
    "job_trigger_type": job_ctx.get("job_trigger_type"),
}
# Get datamart endpoints
datamart_endpoints = job_ctx.get("datamart_endpoints")
# Initialize AuthUtils with SPN for Datamart API authentication
auth = AuthUtils(
    tenant_id=job_ctx.get("azure_tenant_id"),
    client_id=job_ctx.get("service_spn_id"),
    client_secret=job_ctx.get("service_spn_secrete"),
)
# Initialize schema manager
env = job_ctx.get("env")
schema_manager = SchemaManager(env)

# COMMAND ----------

# Initialize TimeSeriesUtils
timeseries_utils = TimeSeriesUtils(
    tsm_ctx = tsm_ctx,
    datamart_endpoints=datamart_endpoints,
    auth=auth,
    schema_manager=schema_manager,
)

# Update time series
full_table_name = schema_manager.find("tbl_ts").full_name
metadata_audit_skey = timeseries_utils.update_ts(
    data={
        "template.ts1#boc": 4.25,
    },
    full_table_name=full_table_name
)
logger.info(f"metadata_audit_skey: {metadata_audit_skey}")

# Publish updated time serieses to datamart
timeseries_utils.publish_to_datamart(metadata_audit_skey=metadata_audit_skey)
