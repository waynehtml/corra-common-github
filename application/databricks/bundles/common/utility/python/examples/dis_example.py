# Databricks notebook source
# DBTITLE 1,Include common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import packages
import importlib
import oeds.core.log_utils as logutils
from oeds.domain.dis.client.dis_client import DisClient

# COMMAND ----------

# DBTITLE 1,Logging
# Set up logging level as debug
logutils.setup_logger(level="debug")

# COMMAND ----------

# DBTITLE 1,Input widget
# Input a test config name such as: database1
dbutils.widgets.text("config_name", "")

# COMMAND ----------

# DBTITLE 1,Load secrets and settings
# oeds secrete scope
kv_scope = "lake-kv-oedataservices-scope"

# Databricks environment
host = "https://adb-1700301391505163.3.azuredatabricks.net"
azure_workspace_resource_id = "/subscriptions/1de5fd2d-2313-461a-acc4-07cdc509cdf4/resourceGroups/lake-cac-npd-rg-devplatform/providers/Microsoft.Databricks/workspaces/lake-cac-npd-dbw-devuc01"

# oeds SPN
azure_tenant_id = dbutils.secrets.get(
    scope=kv_scope,
    key="app-2b501945-5940-4527-9cdd-51a07b77ceb7-tenantid")
azure_client_id = dbutils.secrets.get(
    scope=kv_scope,
    key="app-2b501945-5940-4527-9cdd-51a07b77ceb7-appid")
azure_client_secret = dbutils.secrets.get(
    scope=kv_scope,
    key="app-2b501945-5940-4527-9cdd-51a07b77ceb7-secret")

# COMMAND ----------

# DBTITLE 1,Build DIS client
# Test job
dis_job_id = 870190954950100
client = DisClient(
    host=host,
    azure_tenant_id=azure_tenant_id,
    azure_client_id=azure_client_id,
    azure_client_secret=azure_client_secret,
    azure_workspace_resource_id=azure_workspace_resource_id,
    dis_job_id=dis_job_id,
)

# COMMAND ----------

# Get config name
config_name = dbutils.widgets.get("config_name").strip()
print(f"config_name is {config_name}")
if len(config_name) == 0:
    raise ValueError("config_name is missing")

# Import config file
cfg_file = importlib.import_module(f"dis_config.{config_name}")
cfg_file = importlib.reload(cfg_file)
print(f"config_template is \n{cfg_file.config_template}")
print(f"config_params is \n{cfg_file.config_params}")
print(f"service_params is \n{cfg_file.service_params}")

# COMMAND ----------

# DBTITLE 1,Parameterized tester input
# Resolve config template
resolved_config = client.resolve_config_template(
    config_template=cfg_file.config_template,
    template_params=cfg_file.config_params,
)

# COMMAND ----------

# DBTITLE 1,Call DIS ingestion service and capture IngestResult
try:
    ingest_result = client.ingest(
        config=resolved_config,
        rundate=None,
        params=cfg_file.service_params,
        debug=True,
        wait_timeout_sec=1800,
        wait_poll_sec=10,
    )
except Exception as exc:
    print("status: EXCEPTION")
    print(f"exception: {exc}")
    raise

# COMMAND ----------

print(f"status: {ingest_result.status}")
job_id = ingest_result.get_job_id()
job_run_id = ingest_result.get_job_run_id()
job_run_page_url = ingest_result.get_job_run_page_url()
task_run_id = ingest_result.get_task_run_id()
task_run_error = ingest_result.get_task_run_error()
task_run_error_trace = ingest_result.get_task_run_error_trace()
job_run_output = ingest_result.job_run_output
task_run_output = ingest_result.task_run_output
task_notebook_output = ingest_result.get_task_notebook_output()

if ingest_result.status == "RUNNING":
    print(f"job_run_id: {job_run_id}")
    print(f"job_run_page_url: {job_run_page_url}")
elif ingest_result.status == "TIMEOUT":
    print(f"job_run_id: {job_run_id}")
    print(f"job_run_page_url: {job_run_page_url}")
elif ingest_result.status == "FAILED":
    print(f"job_run_id: {job_run_id}")
    print(f"job_run_page_url: {job_run_page_url}")
    print(f"task_run_id: {task_run_id}")
    print(f"job_run_output: {job_run_output}")
    print(f"task_run_output: {task_run_output}")
    # Error related
    print(f"task_run_error: {task_run_error}")
    print(f"task_run_error_trace: {task_run_error_trace}")
elif ingest_result.status == "SUCCESS":
    print(f"job_run_id: {job_run_id}")
    print(f"job_run_page_url: {job_run_page_url}")
    print(f"task_run_id: {task_run_id}")
    print(f"job_run_output: {job_run_output}")
    print(f"task_run_output: {task_run_output}")
    # Success related
    print(f"task_notebook_output: {task_notebook_output}")
    # ingestion related
    print(f"upload task_result: {ingest_result.get_nb_task_result('upload')}")
    print(f"upload outputs[].dest: {ingest_result.get_nb_task_result_dst('upload')}")
    print(f"upload outputs[].dest.path: {ingest_result.get_nb_task_result_dst_path('upload')}")
else:
    print(f"Unhandled status: {ingest_result.status}")
