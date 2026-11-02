# Databricks notebook source
# MAGIC %run ../common

# COMMAND ----------

# Standard libraries
import logging

#
from oeds_workflow.utils.schema.schema_utils import SchemaManager

# COMMAND ----------

# Get logger
logger = logging.getLogger(__name__)

# Get job context
job_ctx = get_job_context()
logger.debug(f"job_ctx: {job_ctx}")

# 
env = job_ctx.get("env")
scmgr = SchemaManager(env)

# COMMAND ----------

scmgr.list()

# COMMAND ----------

scmgr.list_tables()

# COMMAND ----------

scmgr.find("tbl_corra_rate_history").full_name


