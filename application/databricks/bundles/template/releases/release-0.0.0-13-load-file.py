# Databricks notebook source
# MAGIC %md
# MAGIC ### Run this notebook in the job with following parameters:
# MAGIC - task: releases/release-001-13-load-file
# MAGIC - args: {"ref":"","mode":"overwrite|append"}

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
from oeds_workflow.utils.file.file_loader import FileLoader

# Initiate schema manager
env = job_ctx.get("env")
schema_manager = SchemaManager(env)
land_root = job_ctx.get("land_root")
land_file_dir = f"{land_root}/file"
file_loader = FileLoader(storage_path=land_file_dir, schema_manager=schema_manager)

# Parse args
args_str = job_ctx.get("args")
args = parse_json(args_str)

# Table reference
ref = args.get("ref", "")
logger.info(f"ref: {ref}")
if ref:
    all_refs = [ref]
else:
    all_refs = file_loader.list()
logger.info(f"Files to be loaded: {all_refs}")

# mode
mode = args.get("mode","overwrite")
logger.info(f"mode: {mode}")

# COMMAND ----------

for ref in all_refs:
    logger.info(f"Loading file: {ref}")
    file_loader.load_file(ref, mode=mode)
