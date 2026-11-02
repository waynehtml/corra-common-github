# Databricks notebook source
# DBTITLE 1,Inlude common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import Common Python Packages
import oeds.core.log_utils as logutils

# COMMAND ----------

logutils.setup_logger(level="debug")

# COMMAND ----------

import logging
logger = logging.getLogger(__name__)

# COMMAND ----------

logger.info("Hello World")

# COMMAND ----------

logger.warning("Hello World")
