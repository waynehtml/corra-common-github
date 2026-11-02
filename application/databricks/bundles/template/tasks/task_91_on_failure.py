# Databricks notebook source
dbutils.widgets.removeAll()
dbutils.widgets.text("debug", "True")

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

email_manager.send_email("failure")
