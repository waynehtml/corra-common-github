"""Manage time series data ingestion, metadata, and audit logging."""

import datetime as dt
import time
from datetime import datetime, timezone, date
from typing import Optional, Union, Dict, List, Any
from enum import Enum
import re
import logging
import requests
from pyspark.sql import functions as F
from pyspark.sql.functions import lit
from pyspark.sql import SparkSession, DataFrame
from delta.tables import DeltaTable
import oeds.core.misc_utils as miscutils
import oeds.core.time_utils as timeutils
import oeds.core.df_utils as dfutils
import oeds.core.uc_utils as ucutils
from oeds.core.auth_utils import AuthUtils

logger = logging.getLogger(__name__)

class Freq(str, Enum):
    DAILY = "Daily"
    MONTHLY = "Monthly"
    BUSINESS = "Business"
    MINUTELY_30 = "Minutely (30)"
    WEEKLY = "Weekly"
    WEEKLY_MON = "Weekly (Mon)"
    WEEKLY_TUE = "Weekly (Tue)"
    WEEKLY_WED = "Weekly (Wed)"
    WEEKLY_THU = "Weekly (Thu)"
    WEEKLY_FRI = "Weekly (Fri)"
    WEEKLY_SAT = "Weekly (Sat)"
    WEEKLY_SUN = "Weekly (Sun)"
    ANNUAL = "Annual"
    ANNUAL_JAN = "Annual (Jan)"
    ANNUAL_FEB = "Annual (Feb)"
    ANNUAL_MAR = "Annual (Mar)"
    ANNUAL_APR = "Annual (Apr)"
    ANNUAL_MAY = "Annual (May)"
    ANNUAL_JUN = "Annual (Jun)"
    ANNUAL_JUL = "Annual (Jul)"
    ANNUAL_AUG = "Annual (Aug)"
    ANNUAL_SEP = "Annual (Sep)"
    ANNUAL_OCT = "Annual (Oct)"
    ANNUAL_NOV = "Annual (Nov)"
    ANNUAL_DEC = "Annual (Dec)"
    QUARTERLY = "Quarterly"
    QUARTERLY_DEC = "Quarterly (Dec)"
    QUARTERLY_NOV = "Quarterly (Nov)"
    QUARTERLY_OCT = "Quarterly (Oct)"
    SEMIANNUAL = "Semiannual"
    SEMIANNUAL_JUL = "Semiannual (Jul)"
    SEMIANNUAL_AUG = "Semiannual (Aug)"
    SEMIANNUAL_SEP = "Semiannual (Sep)"
    SEMIANNUAL_OCT = "Semiannual (Oct)"
    SEMIANNUAL_NOV = "Semiannual (Nov)"
    SEMIANNUAL_DEC = "Semiannual (Dec)"


class TimeSeriesCollection:
    """Container for multiple time series entries."""

    def __init__(self,):
        # Internal list to store all time series records
        self._data: List[dict] = []

    def add(
        self,
        *,
        time_series_name: str,
        date: dt.date = None,
        observation: Optional[Union[int, float]] = None,
        observation_str: Optional[str] = None
    ) -> None:
        """Add a new data point to the collection.

        Args:
            time_series_name: Name of the time series (for example, ``"CORRA"``).
            date: Observation date.
            observation: Numeric value (float or int).
            observation_str: Optional string value when observation is non-numeric.

        Returns:
            None. The entry is appended to the internal collection.

        Raises:
            ValueError: If ``time_series_name`` is empty or ``date`` is missing.
        """

        if not isinstance(time_series_name, str) or not time_series_name.strip():
            raise ValueError("time_series_name must be a non-empty string.")
        if date is None:
            raise ValueError("date is required.")

        # Collect locals into a dict, remove 'self'
        cols = locals().copy()
        del cols["self"]

        # Normalize
        cols["observation"] = float(cols["observation"]) if cols["observation"] is not None else None
        cols["time_series_name"] = cols["time_series_name"].strip()

        # Append to internal list
        self._data.append(cols)

    def get_unique_time_series_names(self) -> List[str]:
        """Return sorted unique time series names contained in the collection."""
        return sorted({record["time_series_name"] for record in self._data})

    def to_list(self) -> List[dict]:
        """Return the list of stored time series records."""
        return self._data

    def __len__(self) -> int:
        """Return number of time series records stored."""
        return len(self._data)

    def __repr__(self) -> str:
        return f"<TimeSeriesCollection: {len(self._data)} records>"

    def __iter__(self):
        """Allow iteration over all records in the collection."""
        return iter(self._data)


class TimeSeriesManager:
    """Orchestrate time series ingestion, metadata management, and audit logging."""


    # Define

    def __init__(self, *, 
                 metadata_catalog: str,
                 pipeline_name: str, 
                 notebook_path: str, 
                 current_user: str, 
                 job_id: str=None, 
                 job_runid: str=None, 
                 job_trigger_type:str = "one_time"):
        """Initialize the manager with pipeline context and Spark session.

        Args:
            pipeline_name: Name of the pipeline invoking the manager.
            notebook_path: Notebook path for audit logging.
            current_user: Executing user name.
            job_id: Optional Databricks job identifier.
            job_runid: Optional Databricks job run identifier.
            job_trigger_type: Trigger type for the job (for example, ``"one_time"``).
        """
        self.spark = SparkSession.builder.getOrCreate()
        self.pipeline_name = pipeline_name
        self.notebook_path = notebook_path
        self.current_user = current_user
        self.job_id = job_id
        self.job_runid = job_runid
        self.job_trigger_type = job_trigger_type

        # Define database tables and locations
        CAT_MET = metadata_catalog
        DB_META = "metadata_db"
        TABLE_METADATA = "time_series"
        TABLE_PROVIDER = "data_provider"
        TABLE_DATASET = "data_set"
        TABLE_AUDIT_LOG = "audit_log"
        TABLE_TRANS_LOG = "time_series_transaction_log"

        self.FQN_AUDIT_LOG = f"{CAT_MET}.{DB_META}.{TABLE_AUDIT_LOG}"
        self.FQN_METADATA = f"{CAT_MET}.{DB_META}.{TABLE_METADATA}"
        self.FQN_PROVIDER = f"{CAT_MET}.{DB_META}.{TABLE_PROVIDER}"
        self.FQN_DATASET = f"{CAT_MET}.{DB_META}.{TABLE_DATASET}"
        self.FQN_TRANS_LOG = f"{CAT_MET}.{DB_META}.{TABLE_TRANS_LOG}"

    def _get_timestamp_long(self, human_readable: bool = False) -> int:

        """
        Returns the current timestamp as a 64-bit integer which can fit in a spark long column.
        - param: human_readable (bool) - Whether to return a human-readable or Unix timestamp.
        - returns: int - The current timestamp as a long integer.
        """

        if human_readable:
            timestamp_long = int(datetime.now().strftime("%Y%m%d%H%M%S%f")[:19])
        else:
            timestamp_long = int(time.time() * 10000000)
        
        return timestamp_long

    def create_audit_log(self) -> int:
        """Insert a new audit log entry and return the generated key.

        Returns:
            Auto-generated ``audit_log_skey`` value for the inserted record.
        """
        # ----------------------------
        # 1. Prepare log record data
        # ----------------------------
        timestamp = timeutils.now_utc()
        audit_log_skey = self._get_timestamp_long(human_readable=True)
        logger.debug(f"audit_log_skey is: {audit_log_skey}")

        log_data = {
            "audit_log_skey": audit_log_skey,
            "pipeline_name": self.pipeline_name,
            "notebook_path": self.notebook_path,
            "pipeline_status": "success",
            "start_time": timestamp,
            "end_time": timestamp,
            "user_id": self.current_user,
            "job_id": str(self.job_id) if self.job_id else None,
            "job_run_id": str(self.job_runid) if self.job_runid else None,
            "job_trigger_type": self.job_trigger_type,
        }
        logger.info(f"Creating audit log record: {log_data}")

        # ----------------------------
        # 3. Extract schema from existing table to ensure column consistency
        # ----------------------------
        schema = ucutils.extract_table_schema(self.FQN_AUDIT_LOG)

        # Create a DataFrame for the single new log record
        df = self.spark.createDataFrame([log_data], schema)

        # ----------------------------
        # 4. Align columns to target schema and drop auto-generated key
        # ----------------------------
        df = dfutils.add_missing_cols(df, target_schema=schema, default_values=None)

        # ----------------------------
        # 5. Append the record to the audit log table
        # ----------------------------
        ucutils.safe_write_to_delta(df,table_name=self.FQN_AUDIT_LOG, merge_schema=False, mode="append")

        return audit_log_skey


    # -------------------------------------------------------------------------
    #
    # Registration functions
    #
    # -------------------------------------------------------------------------
    def get_data_provider(self, short_name_en: str) -> Dict[str, Any]:
        """Retrieve data provider details by English short name."""
        if not short_name_en:
            raise ValueError("short_name_en is required.")
        query = f"""SELECT * FROM {self.FQN_PROVIDER} WHERE short_name_en = '{short_name_en}'"""
        return ucutils.get_first_row_sql(query)

    def register_data_provider(
        self,
        *,
        audit_log_skey: int,
        short_name_en: str,
        short_name_fr: Optional[str] = None,
        name_en: str,
        name_fr: Optional[str] = None,
        description_en: Optional[str] = None,
        description_fr: Optional[str] = None,
        org_type: Optional[str] = None,
        
    ):
        """Create or update a data provider record.

        Args:
            audit_log_skey: Audit key for the data provider record.
            name_en: English name of the data provider.
            short_name_en: English short name of the data provider.
            name_fr: Optional French name.
            short_name_fr: Optional French short name.
            description_en: Optional English description.
            description_fr: Optional French description.
            org_type: Optional organization type.

        Returns:
            Newly created or updated provider record.

        Raises:
            ValueError: If ``short_name_en`` is invalid or already exists.
            Exception: If the provider cannot be created or retrieved.
        """
        # get the locals for later
        cols = locals().copy()
        del cols["self"]

        # Validate the short name to ensure it's compatible with being used in a series name
        if not re.match(r"^[a-zA-Z0-9_@]+$", short_name_en):
            raise Exception(f"Invalid short name: {short_name_en}")
        
        # Check if the data provider already exists
        data_provider = self.get_data_provider(short_name_en)
        if data_provider:
            raise ValueError(f"The data provider {short_name_en} already exists.")
        
        # Set skey
        cols["data_provider_skey"] = self._get_timestamp_long()

        # add cols values for the audit columns
        dt = timeutils.now_utc()
        cols["audit_creation_date"] = dt
        cols["audit_last_update_user"] = self.current_user
        cols["audit_last_update_date"] = dt

        # create a dataframe and upsert it into the provider table
        schema = ucutils.extract_table_schema(self.FQN_PROVIDER)
        df = self.spark.createDataFrame([cols], schema)
        ucutils.safe_write_to_delta(df, table_name=self.FQN_PROVIDER)

        # get the data provider skey from the database
        data_provider = self.get_data_provider(short_name_en)
        if data_provider:
            return data_provider
        else:
            raise Exception(f"Data provider could not be found nor created.")

    def get_data_set(self, data_provider_short_name_en: str, name_en: str) -> Dict[str, Any]:
        """Retrieve data set details by provider key and English name."""
        if not data_provider_short_name_en:
            raise ValueError("data_provider_short_name_en is required.")
        if not name_en:
            raise ValueError("name_en is required.")

        query = f"""
            SELECT ds.*
            FROM {self.FQN_DATASET} ds
            JOIN {self.FQN_PROVIDER} dp
            ON ds.data_provider_skey = dp.data_provider_skey
            WHERE dp.short_name_en = '{data_provider_short_name_en}'
            AND ds.name_en = '{name_en}'
        """
        return ucutils.get_first_row_sql(query)

    def delete_data_set(self, data_provider_short_name_en: str, name_en: str):
        """
        Delete a data set
        """
        if not data_provider_short_name_en:
            raise ValueError("data_provider_short_name_en is required.")
        if not name_en:
            raise ValueError("name_en is required.")

        # Get first so we can return what we deleted
        deleted = self.get_data_set(data_provider_short_name_en, name_en)
        if deleted:
            # Find the dataset key to delete
            data_set_skey = deleted.get("data_set_skey")
            query = f"""
                DELETE FROM {self.FQN_DATASET}
                WHERE data_set_skey = {int(data_set_skey)}
            """
            self.spark.sql(query)

    def register_data_set(
        self,
        *,
        audit_log_skey: int,
        data_provider_short_name_en: str,
        name_en: str,
        name_fr: Optional[str] = None,
        description_en: Optional[str] = None,
        description_fr: Optional[str] = None,
        boc_contact: Optional[str] = None,
        source_type: Optional[str] = None,
        confidentiality_status: Optional[str] = None,
        raw_location: Optional[str] = None,
        destination_uc_tables: Optional[Dict] = None,
    ) -> int:
        """Create or update a data set record.

        Args:
            data_provider_skey: Foreign key to the data provider.
            name_en: English name of the data set.
            audit_log_skey: Audit key for the data set record.
            description_en: Optional English description.
            name_fr: Optional French name.
            description_fr: Optional French description.
            boc_contact: Optional business owner contact.
            source_type: Optional source type (for example, ``"sftp"``, ``"api"``, ``"rrs"``).
            confidentiality_status: Optional confidentiality status.
            raw_location: Optional path to raw data files.
            destination_uc_tables: Mapping of fully qualified Unity Catalog table names to descriptions.

        Returns:
            Newly created or updated data set record.

        Raises:
            ValueError: If required keys are missing.
            Exception: If the data set cannot be created or retrieved.
        """
        # Set destination_uc_tables to an empty dict if not provided
        if destination_uc_tables is None:
            destination_uc_tables = {}

        # get the locals for later
        cols = locals().copy()
        del cols["self"]
        del cols["data_provider_short_name_en"]

        # save the data set name for later use
        if not name_en:
            raise ValueError("name_en is required.")
        if not data_provider_short_name_en:
            raise ValueError("data_provider_short_name_en is required.")

        # Check if the data set already exists
        data_set = self.get_data_set(data_provider_short_name_en, name_en)
        if data_set:
            raise Exception(f"Data set {name_en} already exists.")

        # Get data privider skey
        data_provider = self.get_data_provider(data_provider_short_name_en)
        if data_provider:
            data_provider_skey = data_provider["data_provider_skey"]
        else:
            raise Exception(f"Data provider {data_provider_short_name_en} not found.")
        logger.debug(f"Found data provider skey: {data_provider_skey}")
        cols["data_provider_skey"] = data_provider_skey

        # Set skey
        cols["data_set_skey"] = self._get_timestamp_long()

        # add cols values for the audit columns
        dt = timeutils.now_utc()
        cols["audit_creation_date"] = dt
        cols["audit_last_update_user"] = self.current_user
        cols["audit_last_update_date"] = dt

        # create a dataframe and upsert it into the data set table
        schema = ucutils.extract_table_schema(self.FQN_DATASET)
        df = self.spark.createDataFrame([cols], schema)
        ucutils.safe_write_to_delta(df, table_name=self.FQN_DATASET)
        logger.debug(f"Data set was created: {name_en}")

        # get the data set skey from the database
        data_set = self.get_data_set(data_provider_short_name_en, name_en)
        if data_set:
            logger.debug(f"Data set skey is: {data_set['data_set_skey']}")
            return data_set
        else:
            raise Exception(f"Data set skey could not be found nor created.")

    def get_time_series(self, time_series_name: str) -> Dict[str, Any]:
        """
        Retrieve series details
        """
        if not time_series_name:
            raise ValueError("time_series_name is required.")

        query = f"""
            SELECT *
            FROM {self.FQN_METADATA}
            WHERE time_series_name = '{time_series_name}'
        """
        return ucutils.get_first_row_sql(query)
    
    def delete_time_series(self, time_series_name: str):
        """
        Delete a time series row from metadata.
        """
        if not time_series_name:
            raise ValueError("time_series_name is required.")
        
        # Normalize the name to lowercase
        time_series_name = time_series_name.lower()

        # Get first so we can return what we deleted
        deleted = self.get_time_series(time_series_name)
        if deleted:
            query = f"""
                DELETE FROM {self.FQN_METADATA}
                WHERE time_series_name = '{time_series_name}'
            """
            self.spark.sql(query)
    
    def register_time_series(
        self,
        data_provider_short_name_en: str,
        data_set_name_en: str,
        provider_series_name: str,
        frequency: Union[str, Freq],
        destination_uc_table: str,
        description_en: Optional[str] = None,
        description_fr: Optional[str] = None,
        priority_ind: int = 0,
        generation_method: Optional[str] = None,
        basis: Optional[str] = None,
        observed_method: Optional[str] = None,
        data_type: str = "Numeric",
        number_of_decimals: Optional[int] = None,
        magnitude: Optional[str] = None,
        status: str = "Active",
        unit_of_measure: Optional[str] = None,
        dissemination_to_fame_ind: int = 0,
        alias_list: Optional[list] = None,
        seasonally_adjusted: Optional[bool] = None,
        statcan_coordinates: Optional[str] = None,
        object_class: str = "Series",
        misc_fields: Optional[dict] = None,
    ) -> None:
        """Build and append a metadata row for a time series.

        Args:
            data_provider_short_name_en: Data provider short name to compose series name.
            data_set_name_en: Data set name.
            provider_series_name: Series name from the data provider.
            frequency: Time series frequency value or ``Freq`` enum.
            destination_uc_table: Destination Unity Catalog table name.
            description_en: Optional English description.
            description_fr: Optional French description.
            priority_ind: Priority indicator.
            generation_method: Time series generation method.
            basis: Basis descriptor.
            observed_method: Observation method.
            data_type: Data type label.
            number_of_decimals: Optional decimal precision.
            magnitude: Optional magnitude descriptor.
            status: Status value.
            unit_of_measure: Optional unit of measure.
            dissemination_to_fame_ind: Indicator for dissemination to FAME.
            alias_list: Optional list of aliases.
            seasonally_adjusted: Whether data is seasonally adjusted.
            statcan_coordinates: Optional StatCan coordinate string.
            object_class: Object class label.
            misc_fields: Additional metadata fields.

        Returns:
            None. Metadata is appended to the internal collection.

        Raises:
            ValueError: If required parameters are missing.
        """

        # check key parameters
        if not data_provider_short_name_en:
            raise ValueError("data_provider_en_short_name is required.")
        if not data_set_name_en:
            raise ValueError("data_set_name_en is required.")
        if not provider_series_name:
            raise ValueError("provider_series_name is required.")

        # get the locals for later use
        cols = locals().copy()
        del cols["self"]
        del cols["data_provider_short_name_en"]
        del cols["data_set_name_en"]

        # Check if the data set exists
        data_set = self.get_data_set(data_provider_short_name_en, data_set_name_en)
        if data_set is None:
            raise ValueError(f"Data set {data_set_name_en} of {data_provider_short_name_en} not found.")
        
        # Get the data set skey and data provider skey
        cols["data_set_skey"] = data_set["data_set_skey"]

        # if the misc_fields is not specified, initialize it
        if cols["misc_fields"] is None:
            cols["misc_fields"] = {}

        # if the alias list is not specified, initialize it
        if cols["alias_list"] is None:
            cols["alias_list"] = []
        else:
            cols["alias_list"] = [x.lower() for x in cols["alias_list"]]

        # define missing columns
        cols["provider_name"] = provider_series_name.lower()
        cols["time_series_name"] = (
            f"{provider_series_name}#{data_provider_short_name_en}".lower()
        )

        # Audit columns
        dt = timeutils.now_utc()
        cols["audit_creation_date"] = dt
        cols["audit_last_update_user"] = self.current_user
        cols["audit_last_update_date"] = dt
        cols["alias_list"].append(provider_series_name.lower())

        # if the generation method wasn't specified and the provider short name is not BOC
        # set the generation method to "Data Feed"
        if cols["generation_method"] is None and data_provider_short_name_en != "BOC":
            cols["generation_method"] = "Data Feed"

        # Standardize the frequency type.
        cols["frequency"] = cols["frequency"].value

        # Check if the time series already exists
        time_series = self.get_time_series(cols["time_series_name"])
        if time_series:
            raise ValueError(f"Time series {cols['time_series_name']} already exists.")

        # create a dataframe and upsert it into the data set table
        schema = ucutils.extract_table_schema(self.FQN_METADATA)
        df = self.spark.createDataFrame([cols], schema)
        ucutils.safe_write_to_delta(df, table_name=self.FQN_METADATA)

        # Get the data set skey from the database
        time_series = self.get_time_series(cols["time_series_name"])
        if time_series:
            return time_series
        else:
            raise Exception(f"Time series metadata could not be found nor created.")

    # -------------------------------------------------------------------------
    #
    # Observation functions
    #
    # -------------------------------------------------------------------------
    def _get_table_time_series_map(self, time_series_names: list) -> list[tuple[str, list[str]]]:
        """
        Return a list of (destination_uc_table, [time_series_names]) tuples
        from self.FQN_METADATA for the given list of keys.

        Example output:
            [('uc_table_1', ['abc', 'xyz']), ('uc_table_2', ['mno'])]
        """
        if not time_series_names:
            return []
        
        logger.debug(f"{miscutils.fn()}: Getting table-time series map for {time_series_names}")

        df = (
            self.spark.table(self.FQN_METADATA)
            .select("destination_uc_table", "time_series_name")
            .where(F.col("time_series_name").isin(time_series_names))
            .groupBy("destination_uc_table")
            .agg(F.collect_set("time_series_name").alias("time_series_names"))
        )

        # Convert to list of tuples
        return [(r["destination_uc_table"], r["time_series_names"]) for r in df.collect()]
    
    def upsert_observation(
        self,
        *,
        series_collection: TimeSeriesCollection,
        table_name: str,
        audit_skey: int
    ) -> int:
        """Upsert time series data into destination tables.

        Each destination table is processed separately based on the series collection.

        Args:
            series_collection: Collection of time series records to process.

        Returns:
            Number of destination tables that were processed.
        """
        series_list = series_collection.to_list()

        # Convert date to datetime in UTC
        for series in series_list:
            series['date'] = timeutils.date_to_utc_datetime(series['date'])

        # Extract schema from Unity Catalog (ensures correct data types)
        schema = ucutils.extract_table_schema(table_name, exclude_idenity_column=True)

        # Create Spark DataFrame with the extracted schema
        df = self.spark.createDataFrame(series_list, schema)

        # Only keep the time series name without the provider name
        df = df.withColumn(
                    "time_series_name",
                    F.split(F.col("time_series_name"), "#").getItem(0)
                )
        
        # Set audit_skey
        df = df.withColumn("audit_skey", F.lit(audit_skey))

        # Set current timestamp to update_date
        df = df.withColumn("update_date", F.lit(timeutils.now_utc()))

        # Merge the new/updated data into the target Delta table
        ucutils.safe_merge_df_to_delta(
            source_df=df,
            target_table=table_name,
            join_keys=["time_series_name", "date"])

    def _update_time_series_transaction_log(self, time_series_names: List[str], audit_log_skey: int):
        """
        Update 'audit_log_skey' for all rows in self.FQN_METADATA whose time_series_name 
        is in the provided list. Uses direct Delta update with a PySpark Column condition.

        Args:
            time_series_names (List[str]): List of time series names to update.
            audit_log_skey (int): The value to set for audit_log_skey.

        """
        if not isinstance(time_series_names, list) or not time_series_names:
            raise ValueError("time_series_names must be a non-empty list of strings.")
        if audit_log_skey is None:
            raise ValueError("audit_log_skey must not be None.")

        # Build a Spark DF from the list
        df = self.spark.createDataFrame([(x.strip(),) for x in time_series_names], ["time_series_name"])
        df = (
            df
            .withColumn("action_type", F.lit("Data Feed"))
            .withColumn("audit_log_skey", F.lit(int(audit_log_skey)).cast("long"))
            .withColumn("action_timestamp", F.current_timestamp())
        )
        
        ucutils.safe_write_to_delta(df, table_name=self.FQN_TRANS_LOG)

    def merge_observation(
        self,
        series_collection: TimeSeriesCollection,
        table_name: str
    ) -> int:
        """Upsert observations and update related metadata with a new audit key."""

        # Register audit key
        audit_skey = self.create_audit_log()
        logger.debug(f"{miscutils.fn()}: audit_skey {audit_skey} is created")
        if not audit_skey:
            raise RuntimeError("Failed to create audit_skey")

        # Upsert series to the series table(s)
        self.upsert_observation(
            series_collection=series_collection, 
            table_name=table_name, 
            audit_skey=audit_skey)
        logger.debug(f"{miscutils.fn()}: Time series observations have updated.")

        # Update transaction log with audit_skey
        unique_series_names = series_collection.get_unique_time_series_names()
        self._update_time_series_transaction_log(unique_series_names, audit_log_skey=audit_skey)
        logger.debug(f"{miscutils.fn()}: Time series transaction log have been updated with audit_skey: {audit_skey}.")
        
        return audit_skey

    def publish_batch_to_datamart(
        self,
        endpoints: List[Dict[str, str]],
        auth: AuthUtils,
        audit_skey: Union[str, int],
        timeout: int = 600
    ):
        """
        Trigger the Datamart pipeline using multiple (url, scope) pairs.
        Stops at the first 200 OK.

        Args:
            endpoints: A non-empty list of dicts like:
                [
                    {"URL": "https://.../ingest", "SCOPE": "api://.../.default"},
                    {"URL": "https://.../ingest", "SCOPE": "api://.../.default"}
                ]
            audit_skey: The pipeline_run_id to send to Datamart.
            timeout: Request timeout in seconds. Defaults to 600.

        Raises:
            ValueError: If endpoints is empty or all requests fail.
        """
        if not endpoints or not isinstance(endpoints, list):
            raise ValueError("endpoints must be a non-empty list of {'URL', 'SCOPE'} pairs.")

        ex = None
        payload = {"pipeline_run_id": str(audit_skey)}
        for ep in endpoints:
            url = ep.get("URL") or ep.get("url")
            scope = ep.get("SCOPE") or ep.get("scope")

            if not url or not scope:
                raise ValueError(f"Invalid endpoint entry: {ep}. Each endpoint must have 'URL' and 'SCOPE'.")

            try:
                token = auth.get_token_with_scope(scope)
                logger.debug(f"Got token for scope: {scope}")

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                }
                response = requests.post(url, json=payload, headers=headers, timeout=timeout)
                logger.debug(f"Datamart trigger [{url}] -> {response.status_code}: {response.text}")

                if response.status_code == 200:
                    return response  # success, stop here
                else:
                    # Datamart error
                    ex = RuntimeError(f"Datamart request failed ({response.status_code}): {response.text}")
                    logger.warning(f"Datamart request failed ({response.status_code}) for {url}: ({response.status_code})")

            except Exception as e:
                # Connection errors
                ex = e
                logger.warning(f"Error calling {url}: {e}")

        if ex is not None:
            raise ex
        else:
            raise RuntimeError(f"Failed to publish to datamart.")
