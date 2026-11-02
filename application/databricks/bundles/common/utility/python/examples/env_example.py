# Databricks notebook source
# DBTITLE 1,Inlude common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import Common Python Packages
from oeds.core.env_utils import ENV
import oeds.core.log_utils as logutils

# COMMAND ----------

# DBTITLE 1,Set up testing environment
logutils.setup_logger(level="debug")

# COMMAND ----------

print(f"Python version: {ENV.PYTHON_VERSION}")
print(f"Spark versione: {ENV.SPARK_VERSION}") 

# COMMAND ----------

print(f"env: {ENV.NAME}")
print(f"land storage 01: {ENV.LANDST1}")
print(f"land storage 02: {ENV.LANDST2}")
print(f"lake storage: {ENV.LAKEST}")
print(f"DATAMART_API_URLS: {ENV.DATAMART}")

# COMMAND ----------

print(f"current notebook path: {ENV.CURRENT_NOTEBOOK_PATH}")
print(f"current notebook name: {ENV.CURRENT_NOTEBOOK_NAME}")
print(f"current path: {ENV.CURRENT_DIR}")
print(f"workspace url: {ENV.WORKSPACE_URL}")
print(f"workspace ID: {ENV.WORKSPACE_ID}")

# COMMAND ----------

print(f"current user: {ENV.CURRENT_USER}")
print(f"Job ID: {ENV.CURRENT_JOB_ID}")
print(f"Job RunID: {ENV.CURRENT_JOB_RUNID}")
