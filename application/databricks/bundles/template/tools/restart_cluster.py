# Databricks notebook source
# MAGIC %md
# MAGIC This notebook will restart the current cluster, which is required to reload libraries. Run the job with following parameters 
# MAGIC - task: `tools`/restart_cluster
# MAGIC - mode: sys

# COMMAND ----------

from databricks.sdk import WorkspaceClient

cluster_id = (
    dbutils.notebook.entry_point.getDbutils()
    .notebook()
    .getContext()
    .clusterId()
    .get()
)

print(f"Restarting cluster: {cluster_id}")

w = WorkspaceClient()
w.clusters.restart(cluster_id=cluster_id)
