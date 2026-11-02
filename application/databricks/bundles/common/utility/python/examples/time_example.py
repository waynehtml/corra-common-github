# Databricks notebook source
# DBTITLE 1,Inlude common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import Common Python Packages
from datetime import datetime
import oeds.core.time_utils as timeutils

# COMMAND ----------

# DBTITLE 1,Set up testing environment
def setup():
    pass

# COMMAND ----------

# DBTITLE 1,date_to_utc_datetime
print(timeutils.date_to_utc_datetime(timeutils.today_local(), time_mode="noon"))

# COMMAND ----------

print(timeutils.now_utc())
print(timeutils.now_utc("min"))
print(timeutils.now_utc("max"))

# COMMAND ----------

print(timeutils.now_local())
print(timeutils.now_local("min"))
print(timeutils.now_local("max"))

# COMMAND ----------

print(timeutils.today_utc())
print(timeutils.today_local())

# COMMAND ----------

print(timeutils.to_unix(timeutils.now_local()))
print(timeutils.to_unix(datetime.now()))

# COMMAND ----------

print(timeutils.datetime_to_utc_iso_str(datetime.now(),ms=True))
print(timeutils.utc_iso_str_to_datetime("2025-10-17T14:45:32-04:00"))
