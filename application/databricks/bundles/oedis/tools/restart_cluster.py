# Databricks notebook source
# MAGIC %md
# MAGIC This notebook will set up SSH/SFTP server.
# MAGIC Run the OEDS-DIS-JOB job with following parameters 
# MAGIC - config: /Workspace/oedataservices/.bundle/oedis/files/tools/restart_cluster

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
