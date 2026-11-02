# Databricks notebook source
# FOR DEV ONLY
dbutils.widgets.removeAll()
dbutils.widgets.text("debug", "True")

# COMMAND ----------

# MAGIC %run ../common

# COMMAND ----------

# Get logger
import logging
logger = logging.getLogger(__name__)

# Get job context
job_ctx = get_job_context()
logger.debug(f"job_ctx: {job_ctx}")

# COMMAND ----------

from datetime import date, datetime
from pathlib import Path
from oeds.domain.dis.client.dis_client import DisClient
from oeds_workflow.exceptions import IngestTaskError
from oeds_workflow.utils.ingest.ingest_utils import count_files, has_files, load_config

# COMMAND ----------

file_date = dbutils.jobs.taskValues.get(
    taskKey="main",
    key="file_date",
    debugValue=date.today().isoformat()
)

# COMMAND ----------

# MAGIC %md
# MAGIC # Prepare DIS connection and Init DIS client

# COMMAND ----------

# Databricks environment for job calling
databricks_workpsace_url = job_ctx.get("internal_databricks_workspace_url")
logger.debug(f"databricks_workpsace_url: {databricks_workpsace_url}")
databricks_resource_id = job_ctx.get("internal_databricks_resource_id")
logger.debug(f"databricks_resource_id: {databricks_resource_id}")
dis_job_id = int(job_ctx.get("internal_dis_job_id"))
logger.debug(f"dis_job_id: {dis_job_id}")

# Secrete scope and SPN secrete keys for authentication
azure_tenant_id=job_ctx.get("azure_tenant_id")
logger.debug(f"azure_tenant_id: {azure_tenant_id}")
service_spn_id=job_ctx.get("service_spn_id")
logger.debug(f"service_spn_id: {service_spn_id}")
service_spn_secrete=job_ctx.get("service_spn_secrete")
logger.debug(f"service_spn_secrete: {service_spn_secrete}")

# Initialize ingestion client
dis_client = DisClient(
    host=databricks_workpsace_url,
    azure_tenant_id=azure_tenant_id,
    azure_client_id=service_spn_id,
    azure_client_secret=service_spn_secrete,
    azure_workspace_resource_id=databricks_resource_id,
    dis_job_id=dis_job_id,
)

# COMMAND ----------

# MAGIC %md
# MAGIC # Load ingestion config file and resolve template

# COMMAND ----------

# Load ingestion config file
conifg_file_name = "default.json"
try:
    config_template = load_config()
    logger.debug(f"config_template is {config_template}")
except Exception as exc:
    logger.error(f"Failed to read config file: {conifg_file_name}. {exc}")
    raise IngestTaskError(f"Failed to read ingstion config file: {conifg_file_name}") from exc


# Prepare client parameters and resolve config template
env = job_ctx.get("env")
land_dir = str(Path(job_ctx.get("land_root")) / job_ctx.get("dataset") / file_date)

# Resolve config template
try:
    resolved_config = dis_client.resolve_config_template(
        config_template=config_template,
        template_params={
            "env": env,
            "land_dir": land_dir
        }
    )
except Exception as exc:
    logger.error(f"Failed to resolve config file: {exc}")
    raise IngestTaskError(f"Failed to resolve ingstion config file") from exc

# COMMAND ----------

# MAGIC %md
# MAGIC # Call ingestion serivce

# COMMAND ----------

# Request ingestion service
service_params = {}
try:
    ingest_result = dis_client.ingest(
        config=resolved_config,
        rundate=None,
        params=service_params,
        debug=True,
        wait_timeout_sec=1800,
        wait_poll_sec=10,
    )
except Exception as exc:
    logger.error(f"Failed to call ingestion service: {exc}")
    raise IngestTaskError(f"Failed to call ingestion service: {exc}") from exc

# COMMAND ----------

# MAGIC %md
# MAGIC # Show ingestion result

# COMMAND ----------

print(f"status: {ingest_result.status}")
job_run_id = ingest_result.get_job_run_id()
job_run_page_url = ingest_result.get_job_run_page_url()
task_run_id = ingest_result.get_task_run_id()
task_run_error = ingest_result.get_task_run_error()
task_run_error_trace = ingest_result.get_task_run_error_trace()
job_run_output = ingest_result.job_run_output
task_run_output = ingest_result.task_run_output
task_notebook_output = ingest_result.get_task_notebook_output()

if ingest_result.status == "RUNNING":
    logger.debug(f"job_run_id: {job_run_id}")
    logger.debug(f"job_run_page_url: {job_run_page_url}")
elif ingest_result.status == "TIMEOUT":
    logger.debug(f"job_run_id: {job_run_id}")
    logger.debug(f"job_run_page_url: {job_run_page_url}")
elif ingest_result.status == "FAILED":
    logger.debug(f"job_run_id: {job_run_id}")
    logger.debug(f"job_run_page_url: {job_run_page_url}")
    logger.debug(f"task_run_id: {task_run_id}")
    logger.debug(f"job_run_output: {job_run_output}")
    logger.debug(f"task_run_output: {task_run_output}")
    # Error related
    logger.error(f"task_run_error: {task_run_error}")
    logger.error(f"task_run_error_trace: {task_run_error_trace}")
elif ingest_result.status == "SUCCESS":
    logger.info(f"job_run_id: {job_run_id}")
    logger.info(f"job_run_page_url: {job_run_page_url}")
    logger.info(f"task_run_id: {task_run_id}")
    logger.info(f"job_run_output: {job_run_output}")
    logger.info(f"task_run_output: {task_run_output}")
    # Success related
    logger.info(f"task_notebook_output: {task_notebook_output}")
    # ingestion related
    logger.info(f"upload task_result: {ingest_result.get_nb_task_result('upload')}")
    logger.info(f"upload outputs[].dest: {ingest_result.get_nb_task_result_dst('upload')}")
    logger.info(f"upload outputs[].dest.path: {ingest_result.get_nb_task_result_dst_path('upload')}")

    logger.info(f"Verifying landed files under {land_dir}")
    try:
        landed_file_count = count_files(land_dir)
        logger.info(f"landed_file_count: {landed_file_count}")

        if landed_file_count <= 0:
            raise IngestTaskError(f"No landed files found after ingestion under {land_dir}")

        if not has_files(land_dir, pattern="*.csv"):
            raise IngestTaskError(f"No CSV files found after ingestion under {land_dir}")
    except Exception as exc:
        logger.error(f"Failed to verify landed files under {land_dir}. {exc}")
        raise IngestTaskError(f"Failed to verify landed files under {land_dir}. {exc}") from exc
else:
    logger.warning(f"Unhandled status: {ingest_result.status}")
