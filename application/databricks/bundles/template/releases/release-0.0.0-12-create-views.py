# Databricks notebook source
# MAGIC %md
# MAGIC ### Run this notebook in the job with following parameters:
# MAGIC - task: releases/release-001-11-create-views
# MAGIC - args: {"ref":""}

# COMMAND ----------

# DBTITLE 1,Widgets for Development parameters
# For development only
dbutils.widgets.removeAll()
dbutils.widgets.text("log", "debug")
dbutils.widgets.text("args", '{}')

# COMMAND ----------

# DBTITLE 1,Load common notebook
# MAGIC %run ../common

# COMMAND ----------

# DBTITLE 1,Get job conetxt
# Standard libraries
import logging
# Get logger
logger = logging.getLogger(__name__)

# Get job context
job_ctx = get_job_context()
logger.debug(f"job_ctx: {job_ctx}")

# COMMAND ----------

from oeds_workflow.utils.schema.schema_utils import SchemaManager

# Initiate schema manager
env = job_ctx.get("env")
schema_manager = SchemaManager(env)

# Parse args
args= job_ctx.get("args")

# Table reference
ref = args.get("ref", "")
logger.info(f"ref: {ref}")
if ref:
    all_refs = [ref]
else:
    all_refs = schema_manager.list_views(create_only=True)
logger.info(f"Views to be created: {all_refs}")

# COMMAND ----------

for ref in all_refs:
    logger.info(f"Creating view: {ref}")
    schema_manager.create_view(ref)
