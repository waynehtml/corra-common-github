# Databricks notebook source
# MAGIC %pip show databricks-sdk

# COMMAND ----------

import logging, sys
logging.getLogger("databricks.sdk").setLevel(logging.DEBUG)

# COMMAND ----------

# DBTITLE 1,Inlude common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import Common Python Packages
from oeds.core.job_utils import DatabricksJobManager, AmbiguityError, NotFoundError

# COMMAND ----------

# DBTITLE 1,Set up connection details
# NOTE: Replace the values below with your own Databricks workspace details.
# - host: workspace URL without protocol (or full https:// URL)
# - resource_id: Azure Databricks workspace resource ID
# - spn_*: service principal credentials for API access

host = "https://adb-1700301391505163.3.azuredatabricks.net/"
resource_id = "/subscriptions/1de5fd2d-2313-461a-acc4-07cdc509cdf4/resourceGroups/lake-cac-npd-rg-devplatform/providers/Microsoft.Databricks/workspaces/lake-cac-npd-dbw-devuc01"
spn_tenantid = "d5d43af4-5942-46a8-907b-711a17e948bd"
spn_id = "28d3da8e-87ab-4c6d-a82d-86fc3b17ccb2"
spn_secret = "UFx8Q~X9wB3PtIVRsQRH7ykjVx3n6zqISdAaTdkD"

job_manager = DatabricksJobManager(
    host=host,
    resource_id=resource_id,
    spn_tenantid=spn_tenantid,
    spn_id=spn_id,
    spn_secret=spn_secret,
)

# COMMAND ----------

# DBTITLE 1,Look up a job ID by name
job_name = "ingestion"

try:
    job_id = job_manager.get_job_id(job_name)
    print(f"Job '{job_name}' -> ID: {job_id}")
except NotFoundError as exc:
    print(f"Job lookup failed: {exc}")
except AmbiguityError as exc:
    print(f"Job lookup ambiguous: {exc}")

# COMMAND ----------

# DBTITLE 1,Run job
run_id = job_manager.run_now(job_id, job_parameters={"task": "test"})
print(f"Run ID: {run_id}")

# COMMAND ----------

# DBTITLE 1,Wait for terminated
completed_run = job_manager.wait_for_job_terminated(
    run_id,
    poll_interval_s=15,
    timeout_s=1800,
)
print(f"Completed run details: {completed_run}")

# COMMAND ----------

normalized_state = DatabricksJobManager.normalize_job_run_state(completed_run)
error_details = DatabricksJobManager.normalize_job_run_error(completed_run)
print(f"Normalized state: {normalized_state}")
print(f"Termination details: {error_details}")

# COMMAND ----------

task_summaries = DatabricksJobManager.summarize_tasks(completed_run)
print(f"Task summaries:{task_summaries}")

# COMMAND ----------

# DBTITLE 1,Get job run
job_run = job_manager.get_job_run(run_id)
print(f"Job run payload:{job_run}")

# COMMAND ----------

# DBTITLE 1,Extract first task id
first_task_key = DatabricksJobManager.extract_first_task_key(job_run)
first_task_run_id = DatabricksJobManager.extract_first_task_run_id(job_run)
print(f"First task key: {first_task_key}")
print(f"First task run ID: {first_task_run_id}")

# COMMAND ----------

# DBTITLE 1,Get task run
task_run = job_manager.get_task_run(first_task_run_id)
print(f"First task run payload:{task_run}")
