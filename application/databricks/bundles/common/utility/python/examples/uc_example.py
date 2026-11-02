# Databricks notebook source
# DBTITLE 1,Inlude common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import Common Python Packages
import oeds.core.uc_utils as ucutils
import oeds.core.log_utils as logutils

# COMMAND ----------

# DBTITLE 1,Set up testing environment
logutils.setup_logger(level="debug")

# COMMAND ----------

# DBTITLE 1,resolve_catalog
name = "metadata"
catalog = ucutils.resolve_catalog(name,"prd")
print(f"{name} catalog in prd env: {catalog}")

# COMMAND ----------

# DBTITLE 1,table_exists
table1 = 'dev_oversight_cat.standardized_db.tbl_paycan_lynx_payment'
ret = ucutils.table_exists(table1)
print(f"does table:{table1} exist? {ret}")

# COMMAND ----------

# DBTITLE 1,table_exists
table2 = 'dev_oversight_cat.standardized_db.tbl_paycan_lynx_payment1'
ret = ucutils.table_exists(table2)
print(f"does table:{table2} exist? {ret}")

# COMMAND ----------

# DBTITLE 1,extract_table_schema
table = 'dev_oversight_cat.standardized_db.tbl_paycan_lynx_payment'
schema = ucutils.extract_table_schema(table)
print(f"schema for table:{table} is {schema}")

# COMMAND ----------

# DBTITLE 1,find_matching_tables
catalog = 'dev_oversight_cat'
schema = 'standardized_db'
table_pattern = "tbl_paycan_lynx_*"
matched_tables = ucutils.find_matching_tables(catalog, schema,table_pattern)
print(f"tables matching {table_pattern} in {catalog}.{schema} are {matched_tables}")

# COMMAND ----------

# DBTITLE 1,get_single_val_sql
sql = "select batch_id from dev_oversight_cat.standardized_db.tbl_paycan_lynx_payment"
value = ucutils.get_single_val_sql(sql)
print(f"value:{value}")

# COMMAND ----------

# DBTITLE 1,get_single_val_sql
sql = "select * from dev_oversight_cat.standardized_db.tbl_paycan_lynx_payment"
value = ucutils.get_single_val_sql(sql, col="batch_id", default=123)
print(f"value:{value}")

# COMMAND ----------

# DBTITLE 1,get_row_val_sql
sql = "select * from dev_oversight_cat.standardized_db.tbl_paycan_lynx_payment"
dic = ucutils.get_first_row_sql(sql)
print(f"value:{dic}")

# COMMAND ----------

# DBTITLE 1,get_min_max_records

table = "dev_metadata_cat.metadata_db.time_series_metadata"
df = ucutils.get_min_max_records(table,filter_column="time_series_metadata_skey",group_by="provider_name")
df.limit(10).display()

# COMMAND ----------

# DBTITLE 1,get_identity_column
table = "dev_metadata_cat.metadata_db.time_series_metadata"
column = ucutils.get_identity_column(table)
print(f"{table} identity column is {column}")
table = "dev_oversight_cat.standardized_db.tbl_paycan_lynx_payment"
column = ucutils.get_identity_column(table)
print(f"{table} identity column is {column}")

# COMMAND ----------

# DBTITLE 1,get_partition_columns
table = "dev_metadata_cat.metadata_db.time_series_metadata"
column = ucutils.get_partition_columns(table)
print(f"{table} partition columns are {column}")
table = "dev_oversight_cat.standardized_db.tbl_paycan_lynx_payment"
column = ucutils.get_partition_columns(table)
print(f"{table} partition columns are {column}")

# COMMAND ----------

from pyspark.sql.types import StructType, StructField, StringType, IntegerType

table = "dev_test_financial_markets_cat.standardized_db.test_table"
schema = StructType([
    StructField("id", IntegerType(), True),
    StructField("name", StringType(), True),
])
ucutils.create_table(
    table,
    schema,
    runtime_version="14.3",
    partition_cols=["id"],
    identity_col=("skey", 1, True),
    comment="test table", 
    replace=True,
)

# COMMAND ----------

table = "dev_test_financial_markets_cat.standardized_db.test_table"
ucutils.delete_table(table)
