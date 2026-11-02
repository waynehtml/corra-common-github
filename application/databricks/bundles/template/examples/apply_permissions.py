# Databricks notebook source
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

from oeds_workflow.utils.permission import PermissionManager
from oeds_workflow.utils.schema.schema_utils import SchemaManager

# COMMAND ----------

# Initialize schema manager and permission manager
env = job_ctx.get("env")
schema_manager = SchemaManager(env)
permission_manager = PermissionManager(
    env=env,
    config_file=None,
    schema_manager=schema_manager,
)

# COMMAND ----------

# Preview the configured GRANT statements for one schema ref without executing SQL.
schema_ref = "vw_sample"
permission_manager.apply_permissions(schema=schema_ref, execute=False)

# COMMAND ----------

# Apply the configured permissions for all schema refs in the packaged registry.
permission_manager.apply_permissions(execute=False)
