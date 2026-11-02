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

# DBTITLE 1,Import libraries
# Starndard libaries
from datetime import date, datetime
from pathlib import Path

# OEDS common libraries
import oeds.core.file_utils as fileutils

# OEDS workflow libraries
from oeds_workflow.exceptions import LandToRawTaskError

# COMMAND ----------

# MAGIC %md
# MAGIC # Get all shared project specific parameters

# COMMAND ----------

# file_date
file_date = dbutils.jobs.taskValues.get(
    taskKey="main",
    key="file_date",
    debugValue=date.today().isoformat()
)

# COMMAND ----------

# MAGIC %md
# MAGIC # Moving files from land to raw

# COMMAND ----------

# Compose landing directory
land_dir = str(Path(job_ctx.get("land_root")) / job_ctx.get("dataset") / file_date)
logger.info(f"land_dir is {land_dir}")
# Compose raw directory
raw_dir = str(Path(job_ctx.get("raw_root")) / job_ctx.get("dataset") / file_date)
logger.info(f"raw_dir is {raw_dir}")

# Moving files from landing to raw
logger.info(f"Moving files from {land_dir} to {raw_dir}")
try:
    fileutils.move_full_folder(land_dir, raw_dir)
except Exception as e:
    logger.error(f"Error moving files from {land_dir} to {raw_dir}. {e}")
    raise LandToRawTaskError(f"Error moving files from {land_dir} to {raw_dir}. {e}")
