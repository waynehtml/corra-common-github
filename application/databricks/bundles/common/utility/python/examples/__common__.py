# Databricks notebook source
dbutils.library.restartPython()

# COMMAND ----------

# DBTITLE 1,Import Python Packages
import os
import sys

# COMMAND ----------

notebook_dir = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
current_dir = "/Workspace" + os.path.dirname(notebook_dir)
python_utils_dir = os.path.abspath(os.path.join(current_dir, '../src'))
print(f"Add oeds library into sys.path: {python_utils_dir}")

# Put the dev path at the front so it takes priority over installed packages
if python_utils_dir in sys.path:
    sys.path.remove(python_utils_dir)
sys.path.insert(0, python_utils_dir)

# COMMAND ----------

test_file_root = "abfss://data@lakecacnpdstdev01.dfs.core.windows.net/raw/internal/_test/dda-common"

# COMMAND ----------

def show(result, title = ""):
    if len(title) > 0:
        print(f"\n# {title} #:")

    print(result)
