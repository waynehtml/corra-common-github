# Databricks notebook source
# MAGIC %md
# MAGIC ## This notebook will remove the wheelhouse for Nexus caching. 
# MAGIC Run the job with following parameters 
# MAGIC - task: tools/remove_wheelhouse
# MAGIC - mode: sys

# COMMAND ----------

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

# Get job parameters
cluster_wheelhouse_path = job_ctx.get("cluster_wheelhouse_path")
logger.info(f"cluster_wheelhouse_path is {cluster_wheelhouse_path}")

try:
    print(f"Removing wheelhouse folder: {cluster_wheelhouse_path}")
    dbutils.fs.rm(cluster_wheelhouse_path, True)
    print("Done.")
except Exception:
    print(f"Wheelhouse folder may not exist or is already removed: {cluster_wheelhouse_path}")
