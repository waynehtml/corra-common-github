# Databricks notebook source
# DBTITLE 1,Inlude common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import Common Python Packages
from datetime import date
from oeds.core.env_utils import ENV
from oeds.core.auth_utils import AuthUtils
import oeds.core.time_utils as timeutils
import oeds.core.log_utils as logutils
from oeds.domain.time_series_manager import TimeSeriesManager, Freq, TimeSeriesCollection

# COMMAND ----------

logutils.setup_logger(level="debug") 

# COMMAND ----------

# DBTITLE 1,Set up testing environment
def setup():
    spark.sql("""
        DELETE FROM dev_metadata_cat.metadata_db.data_provider
        WHERE short_name_en LIKE 'duwe%'
    """)
    spark.sql("""
        DELETE FROM dev_metadata_cat.metadata_db.data_set
        WHERE name_en LIKE 'duwe%'
    """)
    spark.sql("""
        DELETE FROM dev_metadata_cat.metadata_db.time_series
        WHERE provider_name LIKE 'duwe%'
    """)

setup()

# COMMAND ----------

ts = TimeSeriesManager(
        metadata_catalog = "dev_metadata_cat",
        pipeline_name="duwe", 
        notebook_path=ENV.CURRENT_NOTEBOOK_PATH,
        current_user=ENV.CURRENT_USER, 
        job_id=ENV.CURRENT_JOB_ID,
        job_runid=ENV.CURRENT_JOB_RUNID,
        job_trigger_type="one_time",                    # one_time, period, debug etc.
)

# COMMAND ----------

# MAGIC %md
# MAGIC # Registration

# COMMAND ----------

provider = {
    "short_name_en": "duwe_p1",
    "short_name_fr": "[fr]duwe_p1",
    "name_en": "duwe_p1",
    "name_fr": "[fr]duwe_p1",
    "description_en": "duwe p1 desc",
    "description_fr": "[fr]duwe p1 desc",
    "org_type": "Financial Institution"
}
provider["audit_log_skey"] = ts.create_audit_log()
data_provider = ts.register_data_provider(**provider)
print(data_provider)

# COMMAND ----------

dataset = {
    "data_provider_short_name_en": "duwe_p1",
    "name_en": "duwe_p1_ds1",
    "name_fr": "[fr]duwe_p1_ds1",
    "description_en": "duwe_p1_ds1 desc",
    "description_fr": "[fr]duwe_p1_ds1 desc",
    "boc_contact": "DSO-Data Management",
    "source_type": "External",
    "confidentiality_status": "Public - Free usage of data",
    "raw_location": None,
    "destination_uc_tables": {
        "dev_financial_markets_cat.derived_db.tbl_corra_ts": "corra"
    },
}
dataset["audit_log_skey"] = ts.create_audit_log()
data_set = ts.register_data_set(**dataset)
print(data_set)

# COMMAND ----------

timeseries = {
    "data_provider_short_name_en": "duwe_p1",
    "data_set_name_en": "duwe_p1_ds1",
    "provider_series_name": "duwe_p1_d1_ts1",
    "frequency": Freq.DAILY,
    "description_en": "duwe_p1_d1_ts1 desc",
    "description_fr": "[fr]duwe_p1_d1_ts1 desc",
    "priority_ind": 0,
    "generation_method": None,
    "basis": "Daily",
    "observed_method": "End",
    "data_type": "Precision",
    "number_of_decimals": 2,
    "magnitude": None,
    "status": "Active",
    "unit_of_measure": None,
    "dissemination_to_fame_ind": 1,
    "alias_list": [],
    "seasonally_adjusted": None,
    "statcan_coordinates": None,
    "object_class": "Series",
    "misc_fields": {"publish_to_webmart": "true"},
    "destination_uc_table": "dev_financial_markets_cat.derived_db.tbl_corra_ts",
}
time_series = ts.register_time_series(**timeseries)
print(time_series)

# COMMAND ----------

timeseries = {
    "data_provider_short_name_en": "duwe_p1",
    "data_set_name_en": "duwe_p1_ds1",
    "provider_series_name": "duwe_p1_d1_ts2",
    "frequency": Freq.DAILY,
    "description_en": "duwe_p1_d1_ts2 desc",
    "description_fr": "[fr]duwe_p1_d1_ts2 desc",
    "priority_ind": 0,
    "generation_method": None,
    "basis": "Daily",
    "observed_method": "End",
    "data_type": "Precision",
    "number_of_decimals": 2,
    "magnitude": None,
    "status": "Active",
    "unit_of_measure": None,
    "dissemination_to_fame_ind": 1,
    "alias_list": [],
    "seasonally_adjusted": None,
    "statcan_coordinates": None,
    "object_class": "Series",
    "misc_fields": {"publish_to_webmart": "true"},
    "destination_uc_table": "dev_financial_markets_cat.derived_db.tbl_corra_ts",
}
time_series = ts.register_time_series(**timeseries)
print(time_series)

# COMMAND ----------

# MAGIC %md
# MAGIC # Update observation and publish

# COMMAND ----------

tsc = TimeSeriesCollection()
tsc.add(
        time_series_name="duwe_p1_d1_ts1#duwe_p1", # Time series full name is required
        date = timeutils.today_local(),
        observation = 2.50,
        observation_str = None)
tsc.add(
        time_series_name="duwe_p1_d1_ts2#duwe_p1", # Time series full name is required
        date = timeutils.today_local(),
        observation = 2.51,
        observation_str = None)
skey = ts.merge_observation(
        tsc,
        table_name="dev_financial_markets_cat.derived_db.tbl_corra_ts")

# COMMAND ----------

# DBTITLE 1,Publish to DataMart
kv_scope="lake-kv-triton-scope"
tenant_id = dbutils.secrets.get(scope=kv_scope, key="lake-tenant-id")
client_id = dbutils.secrets.get(scope=kv_scope, key="spn-tritonservices-id")
client_secret = dbutils.secrets.get(scope=kv_scope, key="spn-tritonservices-key")

au = AuthUtils(
    tenant_id=tenant_id,
    client_id=client_id,
    client_secret=client_secret
)

ts.publish_batch_to_datamart(endpoints=ENV.DATAMART, auth=au, audit_skey=skey)
