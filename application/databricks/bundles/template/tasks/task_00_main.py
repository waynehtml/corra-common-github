# Databricks notebook source
# MAGIC %md
# MAGIC TODO:
# MAGIC - Implement a relative date module

# COMMAND ----------

# DBTITLE 1,Show widget for development purpose
# FOR DEV ONLY
dbutils.widgets.removeAll()
dbutils.widgets.text("debug", "True")
dbutils.widgets.text("rundate", "")
dbutils.widgets.text("task", "")
dbutils.widgets.text("args", "")

# COMMAND ----------

# DBTITLE 1,Common notebook
# MAGIC %run ../common

# COMMAND ----------

# DBTITLE 1,Import libraries
# Standard libraries
import logging
# Get logger
logger = logging.getLogger(__name__)

# Get job context
job_ctx = get_job_context()
logger.debug(f"job_ctx: {job_ctx}")

# COMMAND ----------

# MAGIC %md
# MAGIC # Run release or maintenance notebooks

# COMMAND ----------

from pathlib import Path
def safe_join(base_dir: str | Path, rel_path: str | Path) -> Path:
    base = Path(base_dir).resolve()
    target = (base / rel_path).resolve()

    target.relative_to(base)  # raises ValueError if target is outside base
    return target

# COMMAND ----------

# Directly run release or maintenance notebooks with full file path in workspace
task = job_ctx.get("task")
logger.debug(f"task: {task}")
args = job_ctx.get("args")
logger.debug(f"args: {args}")

if len(task) > 0:
    # Prepare task's fully qualified path
    if task.startswith("/Workspace"):
        task_path = task
    else:
        job_root_dir = job_ctx.get("job_root_dir")
        task_path = str(safe_join(job_root_dir, task))
    try:
        logger.info(f"Running notebook: {task_path} with {args}")
        result = dbutils.notebook.run(task_path, 0, args)
        # Skip all other tasks and move to on_complete task by setting flow_action to "complete"
        dbutils.jobs.taskValues.set(key="flow_action", value="complete")
        dbutils.notebook.exit(result)
    except Exception as e:
        raise RuntimeError(f"Notebook run failed for: {task}") from e

# COMMAND ----------

# MAGIC %md
# MAGIC # Prepare business related and shared parameters #
# MAGIC -  keep shared parameters in task value.

# COMMAND ----------

# DBTITLE 1,Prepare business related and shared parameters
# file_date: calculate file_date based on rundate
file_date = job_ctx.get("rundate")
logger.info(f"file_date is {file_date}")
# keep file_date in task value
dbutils.jobs.taskValues.set(key="file_date", value=file_date.isoformat())

# COMMAND ----------

#email_manager.send_email("failure")
