# Databricks notebook source
# DBTITLE 1,Show all installed packages
# MAGIC %pip list

# COMMAND ----------

# DBTITLE 1,Show all envs
import os

for k, v in sorted(os.environ.items()):
    print(f"{k}={v}")

# COMMAND ----------

# DBTITLE 1,Show all bindings
bindings = dbutils.notebook.entry_point.getCurrentBindings()

for k in sorted(bindings.keys()):
    print(f"{k} = {bindings[k]}")

# COMMAND ----------

# DBTITLE 1,Warm up Spark
# Spark engine
spark.range(1).count()

# SQL engine
spark.sql("SELECT 1 AS ok").collect()
