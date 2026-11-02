# Databricks notebook source
# DBTITLE 1,Inlude common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import Common Python Packages
from oeds.core.misc_utils import fn, param

# COMMAND ----------

dbutils.widgets.text("id", "123")
print(f"id: {param('id')}")

# COMMAND ----------

print(dbutils.widgets.get("id").strip())

# COMMAND ----------

print(f"id: {param('id')}")

# COMMAND ----------

def hello():
    print(fn())

hello()

# COMMAND ----------

def outer():
    def inner():
        print(fn())
    inner()

outer()  # Python 3.11+: outer.<locals>.inner ; older: inner

# COMMAND ----------

class A:
    def run(self):
        print(fn())

A().run()  # A.run

# COMMAND ----------

class C:
    @staticmethod
    def run():
        print(fn())

C.run()  # 3.11+: C.run ; older: run
