# Databricks notebook source
# DBTITLE 1,Inlude common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import Common Python Packages
from oeds.core.auth_utils import AuthUtils

# COMMAND ----------

scopes = dbutils.secrets.listScopes()
display(scopes) 

# COMMAND ----------

dbutils.secrets.list(scope="lake-kv-triton-scope")

# COMMAND ----------

au = AuthUtils(
    kv_scope="lake-kv-triton-scope",
    tenant_id_key="lake-tenant-id",
    client_id_key="spn-tritonservices-id",
    client_secret_key="spn-tritonservices-key",
)

# COMMAND ----------

# DBTITLE 1,Storage
token=au.get_storage_token()
print(f"storage token: {token}")

# COMMAND ----------

# DBTITLE 1,keyvault
token=au.get_keyvault_token()
print(f"keyvault token: {token}")

# COMMAND ----------

# DBTITLE 1,Graph API
token=au.get_graph_token()
print(f"keyvault token: {token}")

# COMMAND ----------

# DBTITLE 1,Azure SQL server
token=au.get_sql_token()
print(f"Azure SQl server token: {token}")
