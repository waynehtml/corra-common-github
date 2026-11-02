# Databricks notebook source
# DBTITLE 1,Inlude common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import Common Python Packages
import posixpath
import oeds.core.file_utils as fileutils 
from oeds.core.env_utils import ENV

# COMMAND ----------

workspace_file = posixpath.join(ENV.CURRENT_DIR, "./files/template.html")
print(f"file path: {workspace_file}")
workspace_url = fileutils.workspace_url(workspace_file) 
print(f"file url: {workspace_url}")

# COMMAND ----------

content = fileutils.read_text_file(workspace_url)
print(f"file content:\n{content}")

# COMMAND ----------

json_file = posixpath.join(ENV.CURRENT_DIR, "./files/sample.json")
print(f"json path: {json_file}")
json_url = fileutils.workspace_url(json_file)
print(f"json url: {json_url}")

# COMMAND ----------

json_content = fileutils.read_json_file(json_url)
print(f"json content: {json_content}")
