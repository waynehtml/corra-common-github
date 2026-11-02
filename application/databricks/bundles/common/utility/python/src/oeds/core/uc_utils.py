"""Unity Catalog and Delta Lake utilities for Spark-based workflows."""

from typing import Optional, List, Any, Dict, Union, Tuple
import logging
import json
import time
import fnmatch
import random
import re
from pyspark.sql import functions as F
from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.types import StructType, StructField
from delta.tables import DeltaTable
from py4j.protocol import Py4JJavaError
import oeds.core.misc_utils as tk


logger = logging.getLogger(__name__)

_CATALOG_NAME = {
    "application": "application_cat",
    "internal": "boc_internal_src_cat",
    "crypto": "crypto_cat",
    "currency": "currency_cat",
    "dm": "dm_cat",
    "financial_institutions": "financial_institutions_cat",
    "triton": "financial_markets_cat",
    "fmd": "financial_markets_cat",
    "macroeconomics": "macroeconomics_cat",
    "metadata": "metadata_cat",
    "news": "news_and_analytics_cat",
    "paycan": "oversight_cat",
    "oversight": "oversight_cat",
}

# ------------------------------------------------------
#
# Table metadata functions
#
# ------------------------------------------------------
def resolve_catalog(name: str, env: str) -> str:
    """Build a catalog name for the given environment.

    Args:
        name: Logical catalog name (for example, ``"finance"``).
        env: Environment suffix (``"dev"``, ``"stg"``, or ``"prd"``). Defaults to the current environment.

    Returns:
        Catalog name with environment prefix (for example, ``"dev_finance"``).
    """
    if name is None or not name.strip():
        raise ValueError("Catalog name cannot be empty")

    if env is None or not env.strip():
        raise ValueError("Environment cannot be empty")
    
    _name = _CATALOG_NAME.get(name, name)
    return f"{env}_{_name}"

def table_exists(full_name: str) -> bool:
    """Determine whether a Unity Catalog table exists.

    Args:
        full_name: Fully qualified table name (``catalog.schema.table``).

    Returns:
        True if the table exists; otherwise False.
    """
    spark = SparkSession.builder.getOrCreate()
    return spark.catalog.tableExists(full_name)

def extract_table_schema(
    fully_qualified_table_name: str,
    *,
    include_metadata: bool = True,
    exclude_idenity_column: bool = False
) -> StructType:
    """Retrieve the schema structure of a Unity Catalog table.

    Args:
        fully_qualified_table_name: Fully qualified table name (``catalog.schema.table``).
        include_metadata: Whether to retain column metadata such as comments. Defaults to True.
        exclude_idenity_column: Whether to remove idenity column from schema.

    Returns:
        ``StructType`` describing the table schema.

    Raises:
        ValueError: If the table name is empty.
        Exception: Any Spark errors encountered while reading the table metadata.
    """
    # Validate input
    if not fully_qualified_table_name or not fully_qualified_table_name.strip():
        raise ValueError("Table name cannot be empty")
    
    try:
        spark = SparkSession.getActiveSession()

        # Fetch the table DataFrame (metadata only, no data read)
        table_df = spark.table(fully_qualified_table_name)
        
        # Extract and reconstruct the schema
        # Using fromJson ensures a clean copy without DataFrame references
        schema_json = table_df.schema.json()
        table_schema = StructType.fromJson(json.loads(schema_json))
        
        # Optionally strip metadata if not needed
        if not include_metadata:
            # Create clean schema without metadata
            table_schema = StructType([
                StructField(field.name, field.dataType, field.nullable)
                for field in table_schema.fields
            ])
        
        if exclude_idenity_column:
            identity_col = get_identity_column(fully_qualified_table_name)
            if identity_col:
                table_schema = StructType([f for f in table_schema.fields if f.name != identity_col])

        return table_schema
        
    except Exception as e:
        # Log and re-raise with context
        raise type(e)(
            f"Failed to retrieve schema for table '{fully_qualified_table_name}': {str(e)}"
        ) from e


def find_matching_tables(
    catalog_name: str,
    schema_name: str,
    table_pattern: str
) -> List[str]:
    """Retrieve Unity Catalog tables matching a glob pattern.

    Args:
        catalog_name: Catalog name (for example, ``"main"``).
        schema_name: Schema/database name (for example, ``"bronze"``).
        table_pattern: Case-insensitive glob pattern for table names.

    Returns:
        List of fully qualified table names matching the pattern.

    Raises:
        ValueError: If any argument is empty.
        RuntimeError: If no active SparkSession exists.
        Exception: For any Spark errors resolving the catalog or schema.
    """
    # Validate inputs
    if not catalog_name or not catalog_name.strip():
        raise ValueError("Catalog name cannot be empty")
    
    if not schema_name or not schema_name.strip():
        raise ValueError("Schema name cannot be empty")
    
    if not table_pattern or not table_pattern.strip():
        raise ValueError("Table pattern cannot be empty")
    
    # Get active SparkSession
    spark = SparkSession.getActiveSession()
    if spark is None:
        raise RuntimeError(
            "No active SparkSession found. Please create a SparkSession before calling this function."
        )
    
    try:
        # Use the specified catalog and schema
        qualified_schema = f"{catalog_name}.{schema_name}"
        spark.sql(f"USE {qualified_schema}")
        
        # Retrieve all tables in the schema
        all_tables_df = spark.sql("SHOW TABLES")
        all_tables = [row.tableName for row in all_tables_df.collect()]
        
        # Perform case-insensitive glob pattern matching
        matching_tables = [
            table for table in all_tables 
            if fnmatch.fnmatch(table.lower(), table_pattern.lower())
        ]
        
        # Build fully qualified names
        qualified_tables = [
            f"{catalog_name}.{schema_name}.{table}" 
            for table in matching_tables
        ]
        
        return qualified_tables
        
    except Exception as e:
        # Provide context in error message
        raise type(e)(
            f"Failed to retrieve tables matching pattern '{table_pattern}' "
            f"in catalog '{catalog_name}', schema '{schema_name}': {str(e)}"
        ) from e


def get_identity_column(table_name: str) -> Optional[str]:
    """Return the identity column name for a Delta table, if present.

    The lookup first checks ``system.information_schema`` (for three-part names) and then
    parses ``SHOW CREATE TABLE`` output as a fallback.

    Args:
        table_name: Fully qualified or two-part table name to inspect.

    Returns:
        Identity column name if found; otherwise ``None``.
    """
    spark = (SparkSession.getActiveSession()
             or SparkSession.builder.getOrCreate())

    # -------- 1) information_schema (best effort) --------
    parts = [p.strip("`") for p in table_name.split(".")]
    if len(parts) == 3:
        catalog, schema, tbl = parts
        try:
            # Works if your workspace/runtime exposes identity fields.
            df = spark.sql(f"""
                SELECT column_name
                FROM system.information_schema.columns
                WHERE table_catalog = '{catalog}'
                  AND table_schema  = '{schema}'
                  AND table_name    = '{tbl}'
                  AND (is_identity = 'YES' OR identity_generation IS NOT NULL)
                ORDER BY ordinal_position
                LIMIT 1
            """)
            row = df.first()
            if row:
                return row["column_name"]
        except Exception:
            # Fall through to DDL parsing
            pass

    # -------- 2) Fallback: parse SHOW CREATE TABLE DDL --------
    ddl_row = spark.sql(f"SHOW CREATE TABLE {table_name}").first()
    if not ddl_row:
        return None

    ddl = ddl_row[0]
    # Find the first column that has a GENERATED ... AS IDENTITY clause.
    m = re.search(
        r"""(?im)^\s*`?([A-Za-z0-9_]+)`?\s+   # column name
            [^\n,]*?                         # type/attrs
            GENERATED\s+(?:ALWAYS|BY\s+DEFAULT)\s+
            AS\s+IDENTITY\b                  # identity marker
        """,
        ddl,
        flags=re.VERBOSE
    )
    return m.group(1) if m else None

def get_partition_columns(table_name: str) -> List[str]:
    """Return the list of partition column names for a table.

    The function uses several strategies in order: ``DESCRIBE DETAIL``, ``SHOW CREATE
    TABLE``, and ``SHOW PARTITIONS`` inference.

    Args:
        table_name: Fully qualified table name to inspect.

    Returns:
        List of partition column names, or an empty list when none are defined.
    """
    spark = (SparkSession.getActiveSession()
             or SparkSession.builder.getOrCreate())

    # ---- 1) DESCRIBE DETAIL (best) ----
    try:
        df = spark.sql(f"DESCRIBE DETAIL {table_name}")
        if "partitionColumns" in df.columns:
            row = df.select("partitionColumns").first()
            if row and row[0]:
                return list(row[0])
    except Exception:
        pass  # fall through

    # ---- 2) SHOW CREATE TABLE (parse DDL) ----
    try:
        ddl_row = spark.sql(f"SHOW CREATE TABLE {table_name}").first()
        if ddl_row:
            ddl = ddl_row[0]
            m = re.search(r"(?is)PARTITIONED\s+BY\s*\((.*?)\)", ddl)
            if m:
                inside = m.group(1)
                # Extract simple column identifiers within the parentheses
                cand = re.findall(r"`?([A-Za-z_][A-Za-z0-9_]*)`?", inside)
                # Remove obvious non-column tokens that might appear in exotic DDL
                bad = {"RANGE", "BUCKET", "BY", "INTO"}
                cols: List[str] = []
                seen = set()
                for c in cand:
                    if c.upper() in bad: 
                        continue
                    if c not in seen:
                        seen.add(c)
                        cols.append(c)
                if cols:
                    return cols
    except Exception:
        pass  # fall through

    # ---- 3) SHOW PARTITIONS (infer from 'col=value/...' path) ----
    try:
        part_df = spark.sql(f"SHOW PARTITIONS {table_name}").limit(1)
        row = part_df.first()
        if row and row[0]:
            spec = row[0]  # e.g., "dt=2025-10-19/region=CA"
            cols = [kv.split("=", 1)[0] for kv in spec.split("/") if "=" in kv]
            return cols
    except Exception:
        pass

    # Not partitioned or no metadata available
    return []


def delete_table(table_name: str):
    """Delete a table if it exists.

    Args:
        table_name: Fully qualified table name to drop.

    Returns:
        None. The table is dropped as a side effect.
    """

    exists = table_exists(table_name)
    if not exists:
        logger.debug(f"Table {table_name} does not exist; nothing to delete.")
        return

    spark = SparkSession.builder.getOrCreate()
    spark.sql(f"DROP TABLE {table_name}")
    logger.debug(f"Table {table_name} deleted.")


# ----------------------------------------------------------
# Create a Delta table using StructType
# ----------------------------------------------------------
RUNTIME_PROPERTY_PRESETS = {
    "14.3": {
        "delta.minReaderVersion": "2",
        "delta.minWriterVersion": "7",
        "delta.enableChangeDataFeed": "true",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
        "delta.columnMapping.mode": "name",
    },
}

def _default_tblproperties_for_runtime(runtime_version: str) -> Dict[str, str]:
    """Resolve default Delta table properties for a runtime version.

    Args:
        runtime_version: Databricks runtime version string (for example, ``"14.3"``).

    Returns:
        Dictionary of table properties to apply.
    """

    # Exact match case
    if runtime_version in RUNTIME_PROPERTY_PRESETS:
        return dict(RUNTIME_PROPERTY_PRESETS[runtime_version])

    # Default: first key in dict
    first_key = next(iter(RUNTIME_PROPERTY_PRESETS))
    return dict(RUNTIME_PROPERTY_PRESETS[first_key])

def create_table(
    table_name: str,
    schema: StructType,
    runtime_version: str,
    partition_cols: Optional[List[str]] = None,
    identity_col: Optional[Tuple[str, int, bool]] = None,  # tuple: (col_name, start_value, ALWAYS)
    tblproperties: Optional[Dict[str, str]] = None,
    comment: Optional[str] = None,
    replace: bool = False,
):
    """Create or replace a Delta table with optional identity column.

    Args:
        table_name: Fully qualified target table name.
        schema: Spark ``StructType`` defining table columns.
        runtime_version: Databricks runtime version used to select table properties.
        partition_cols: Optional partition column names.
        identity_col: Optional tuple of column name, start value, and whether the identity
            is ``ALWAYS`` (True) or ``BY DEFAULT`` (False).
        tblproperties: Additional table properties to merge with runtime defaults.
        comment: Optional table comment.
        replace: Whether to replace an existing table.

    Returns:
        None. The table is created as a side effect.
    """

    # --- Existence check ---
    exists = table_exists(table_name)
    if exists and not replace:
        logger.info(f"Table {table_name} already exists; replace=False, skipping create.")
        return

    if exists and replace:
        create_keyword = "CREATE OR REPLACE TABLE"
        logger.info(f"Table {table_name} exists; replace=True, will REPLACE it.")
    else:
        create_keyword = "CREATE TABLE"
        logger.info(f"Table {table_name} does not exist; will CREATE it.")

    # ---------------------------
    # Column DDL construction
    # ---------------------------
    col_defs = []

    # --- Identity column first ---
    if identity_col:
        ic_name, ic_start, ic_always = identity_col
        if ic_always:
            ic_mode = "ALWAYS"
        else:
            ic_mode = "BY DEFAULT"

        col_defs.append(
            f"`{ic_name}` BIGINT NOT NULL "
            f"GENERATED {ic_mode} AS IDENTITY (START WITH {ic_start} INCREMENT BY 1)"
        )

    # --- Other columns from schema ---
    for field in schema.fields:
        # Skip duplicated identity column if schema contains it
        if identity_col and field.name == identity_col[0]:
            continue

        data_type = field.dataType.simpleString()
        col_comment = ""

        if field.metadata and "comment" in field.metadata:
            comment_content = field.metadata['comment'].replace("'", "''")
            col_comment = f" COMMENT '{comment_content}'"

        col_defs.append(f"`{field.name}` {data_type}{col_comment}")

    columns_ddl = ",\n  ".join(col_defs)

    # ---------------------------
    # Partition clause
    # ---------------------------
    partition_clause = ""
    if partition_cols:
        cols = ", ".join(f"`{c}`" for c in partition_cols)
        partition_clause = f"\nPARTITIONED BY ({cols})"

    # ---------------------------
    # Runtime properties
    # ---------------------------
    runtime_props = _default_tblproperties_for_runtime(runtime_version)
    final_props = {**runtime_props, **(tblproperties or {})}

    tblprops_clause = ""
    if final_props:
        props_str = ",\n  ".join(f"'{k}' = '{v}'" for k, v in final_props.items())
        tblprops_clause = f"\nTBLPROPERTIES (\n  {props_str}\n)"

    # ---------------------------
    # Comment clause
    # ---------------------------
    comment_clause = ""
    if comment:
        comment_content = comment.replace("'", "''")
        comment_clause = f"\nCOMMENT '{comment_content}'"

    # ---------------------------
    # Final DDL
    # ---------------------------
    ddl = f"""
        {create_keyword} {table_name} (
        {columns_ddl}
        )
        USING DELTA{partition_clause}{comment_clause}{tblprops_clause}
    """

    logger.info(f"Executing DDL: {ddl}")
    spark = SparkSession.builder.getOrCreate()
    spark.sql(ddl)


# ------------------------------------------------------
#
# Read data from Delta by SQL
#
# ------------------------------------------------------
def get_single_val_sql(sql_query: str, *, col: Optional[str] = None, default: Any = None):
    """Return a single value from a SQL query.

    Args:
        sql_query: SQL query to execute.
        col: Column name to extract (case-insensitive). If omitted, the first column is used.
        default: Value returned if no rows are found or the column is missing.

    Returns:
        The value from the requested column, or ``default`` when unavailable.
    """
    spark = SparkSession.builder.getOrCreate()
    df = spark.sql(sql_query)

    # Fetch first row only (efficient)
    rows = df.head(1)
    if not rows:
        return default

    # Case-insensitive column lookup
    columns = df.columns
    if col:
        # Find matching column ignoring case
        matched_col = next((c for c in columns if c.lower() == col.lower()), None)
    else:
        matched_col = columns[0] if columns else None

    if not matched_col:
        return default

    return rows[0][matched_col]


def get_col_val_sql(sql_query: str, *, col: Optional[str] = None) -> List[Any]:
    """Execute a SQL query and return values from one column.

    Args:
        sql_query: SQL query to execute.
        col: Column name to extract (case-insensitive). If omitted, uses the first column.

    Returns:
        List of values from the specified column, or an empty list if no rows exist or the
        column is absent.

    Raises:
        ValueError: If ``sql_query`` is empty.
    """
    if not sql_query or not sql_query.strip():
        raise ValueError("sql_query must be a non-empty string.")

    spark = SparkSession.builder.getOrCreate()
    df = spark.sql(sql_query)

    # Handle empty DataFrame
    if not df.columns:
        return []

    # Case-insensitive column matching
    if col:
        matched_col = next((c for c in df.columns if c.lower() == col.lower()), None)
        if not matched_col:
            return []
    else:
        matched_col = df.columns[0]

    # Efficient collection — only bring needed column to driver
    values = df.select(matched_col).toLocalIterator()
    return [row[matched_col] for row in values]


def get_first_row_sql(sql_query: str) -> Dict[str, Any]:
    """Execute a SQL query and return the first row as a dictionary.

    Args:
        sql_query: SQL query to execute.

    Returns:
        Dictionary mapping column names to values for the first row, or an empty dictionary
        if no rows are returned.

    Raises:
        ValueError: If ``sql_query`` is empty.
    """
    if not sql_query or not sql_query.strip():
        raise ValueError("sql_query must be a non-empty string.")

    spark = SparkSession.builder.getOrCreate()
    df = spark.sql(sql_query)

    # Efficiently get the first row without triggering a full count
    rows = df.head(1)
    if not rows:
        return {}

    # Convert to dictionary (column names as keys)
    return rows[0].asDict(recursive=True)

def get_min_max_records(
    table_path: str,
    filter_column: str,
    filter_operation: str = "MAX",
    group_by: Optional[str] = None,
    additional_where_clause: Optional[str] = None,
) -> DataFrame:
    """Retrieve records filtered by a MIN or MAX aggregation on a column.

    Args:
        table_path: Source table path or name.
        filter_column: Column to aggregate.
        filter_operation: Aggregation to apply (``"MAX"`` or ``"MIN"``).
        group_by: Optional comma-separated grouping columns.
        additional_where_clause: Optional predicate appended to the query.

    Returns:
        Spark ``DataFrame`` containing rows matching the aggregated value.

    Raises:
        ValueError: If ``filter_operation`` is not ``"MAX"`` or ``"MIN"``.
    """
    # Validate filter operation
    valid_operations = {"MAX", "MIN"}
    if filter_operation.upper() not in valid_operations:
        raise ValueError(
            f"Invalid filter_operation. Must be one of: {valid_operations}"
        )

    filter_op = filter_operation.upper()

    # Build the GROUP BY clause
    group_by_clause = f"GROUP BY {group_by}" if group_by else ""

    # Build the JOIN condition
    if group_by:
        join_condition = f"ON t.{filter_column} = f.agg_value AND t.{group_by} = f.{group_by}"
    else:
        join_condition = f"ON t.{filter_column} = f.agg_value"

    # Update the SQL query to include grouping
    query = f"""
        WITH filtered_agg AS (
            SELECT 
                {group_by + ', ' if group_by else ''}{filter_op}({filter_column}) AS agg_value
            FROM {table_path}
            {f"WHERE {additional_where_clause}" if additional_where_clause else ""}
            {group_by_clause}
        )
        SELECT t.*
        FROM {table_path} t
        INNER JOIN filtered_agg f
        {join_condition}
        {f"WHERE {additional_where_clause}" if additional_where_clause else ""}
    """

    spark = SparkSession.builder.getOrCreate()
    df = spark.sql(query)

    return df


# ------------------------------------------------------
#
# Write Dataframe to a Delta table
#
# ------------------------------------------------------
def batch_update(
    df: DataFrame,
    full_table_name: str,
    *,
    mode: str = "replace",
    batch_column: Optional[str] = None,
    batch_id: Optional[str] = None,
    evolve_schema: bool = False,
):
    """Write a DataFrame to a Delta table.

    Args:
        df: Input Spark ``DataFrame``.
        full_table_name: Fully qualified table name (for example, ``"cat.db.tbl"``).
        mode: ``"replace"`` (replace rows matching ``batch_column``/``batch_id``) or
            ``"overwrite"`` (replace the entire table).
        batch_column: Column used in the ``replaceWhere`` predicate for replace mode.
        batch_id: Value used in the predicate when ``mode`` is ``"replace"``.
        evolve_schema: If True:
            - replace mode: enable ``mergeSchema`` (add new columns)
            - overwrite mode: enable ``overwriteSchema`` (table schema becomes df schema)

    Raises:
        ValueError: If ``mode`` is invalid, ``batch_column`` is missing, or ``batch_id`` is
            missing for replace mode.
    """
    mode_lc = mode.lower()
    if mode_lc not in {"replace", "overwrite"}:
        raise ValueError("`mode` must be 'replace' or 'overwrite'.")

    if mode_lc == "replace":
        if batch_column is None:
            raise ValueError("`batch_column` is required for mode='replace'.")
        if batch_column not in df.columns:
            raise ValueError(f"Batch column '{batch_column}' not found in DataFrame.")
        if batch_id is None:
            raise ValueError("`batch_id` is required for mode='replace'.")

        safe_batch_id = batch_id.replace("'", "''")
        predicate = f"{batch_column} = '{safe_batch_id}'"

        w = (df.write.format("delta")
             .mode("overwrite")
             .option("replaceWhere", predicate))
        if evolve_schema:
            w = w.option("mergeSchema", "true")

    else:
        w = df.write.format("delta").mode("overwrite")
        if evolve_schema:
            w = w.option("overwriteSchema", "true")

    w.saveAsTable(full_table_name)

# ------------------------------------------------------
# Safe write with retry to avoid collision
# ------------------------------------------------------
def _is_retryable_error(err: Exception) -> bool:
    """Determine whether an exception is retryable for Delta writes.

    Args:
        err: Exception raised during a Delta write or merge.

    Returns:
        True if the error is considered retryable; otherwise False.
    """
    s = repr(err)
    if isinstance(err, Py4JJavaError):
        s += " " + str(err.java_exception)
    return (
        "MetadataChangedException" in s
        or "ConcurrentAppendException" in s
        or "ConcurrentModificationException" in s
        or "temporarily unavailable" in s.lower()
    )


def safe_write_to_delta(
    df,
    table_name: str,
    *,
    merge_schema: bool = False,
    mode: str = "append",
    max_retry: int = 5,
    retry_delay: float = 2.0,
) -> None:
    """Write a DataFrame to Delta with retries on transient concurrency errors.

    Args:
        df: Spark ``DataFrame`` to write.
        table_name: Fully qualified Delta table name.
        merge_schema: Whether to allow schema evolution. Defaults to False.
        mode: Write mode (for example, ``"append"`` or ``"overwrite"``). Defaults to ``"append"``.
        max_retry: Maximum number of retries on retryable errors. Defaults to 5.
        retry_delay: Base delay in seconds before each retry. Defaults to 2.0.

    Returns:
        None. Data is written as a side effect.

    Raises:
        Exception: Propagates exceptions when retries are exhausted or errors are non-retryable.
    """

    attempt = 0
    while attempt <= max_retry:
        try:
            (
                df.write
                  .format("delta")
                  .option("mergeSchema", str(merge_schema).lower())
                  .mode(mode)
                  .saveAsTable(table_name)
            )
            logger.debug(f"{tk.fn()}: Write succeeded on attempt {attempt + 1}")
            return

        except Exception as e:
            if _is_retryable_error(e) and attempt < max_retry:
                # Exponential backoff with small random jitter
                delay = retry_delay * (2 ** attempt) + random.random()
                logger.debug(
                    f"{tk.fn()}: MetadataChangedException on attempt {attempt + 1}, "
                    f"retrying in {delay:.2f} seconds..."
                )
                time.sleep(delay)
                attempt += 1
                continue
            else:
                logger.debug(f"{tk.fn()}: Write failed after {attempt + 1} attempt(s): {e}")
                raise

def safe_merge_df_to_delta(
    source_df: DataFrame,
    target_table: str,
    join_keys: List[str],
    *,
    update_cols: Optional[List[str]] = None,
    insert_cols: Optional[List[str]] = None,
    condition: Optional[str] = None,
    max_retry: int = 5,
    retry_delay: float = 2.0,
) -> None:
    """Perform an upsert (MERGE INTO) with retries on transient errors.

    Schema evolution is not enabled.

    Args:
        source_df: Spark ``DataFrame`` containing rows to merge.
        target_table: Fully qualified Delta table name.
        join_keys: Column names used to match source and target rows.
        update_cols: Columns to update when matches occur. Defaults to all non-key columns.
        insert_cols: Columns to insert for unmatched rows. Defaults to all source columns.
        condition: Optional additional predicate for the ``WHEN MATCHED`` clause.
        max_retry: Maximum retry attempts. Defaults to 5.
        retry_delay: Base delay in seconds before each retry. Defaults to 2.0.

    Returns:
        None. The merge is executed as a side effect.

    Raises:
        ValueError: If ``join_keys`` is empty or retry settings are negative.
        Exception: Propagates Delta errors when retries are exhausted or errors are non-retryable.
    """
    if not join_keys:
        raise ValueError("join_keys must be a non-empty list")
    if max_retry < 0 or retry_delay < 0:
        raise ValueError("max_retry and retry_delay must be non-negative")

    spark = SparkSession.builder.getOrCreate()
    source_alias, target_alias = "s", "t"

    # Join condition for one or multiple keys
    on_condition = " AND ".join([f"{target_alias}.{k} = {source_alias}.{k}" for k in join_keys])

    # Derive update/insert columns
    source_columns = source_df.columns
    if update_cols is None:
        update_cols = [c for c in source_columns if c not in join_keys]
    if insert_cols is None:
        insert_cols = source_columns

    update_set = {c: f"{source_alias}.{c}" for c in update_cols}
    insert_values = {c: f"{source_alias}.{c}" for c in insert_cols}

    attempt = 0
    while attempt <= max_retry:
        try:
            target = DeltaTable.forName(spark, target_table)
            mb = target.alias(target_alias).merge(source_df.alias(source_alias), on_condition)

            if condition:
                mb = mb.whenMatchedUpdate(condition=condition, set=update_set)
            else:
                mb = mb.whenMatchedUpdate(set=update_set)

            mb.whenNotMatchedInsert(values=insert_values).execute()
            logger.debug(f"{tk.fn()}: Merge succeeded on attempt {attempt + 1}")
            return

        except Exception as e:
            if _is_retryable_error(e) and attempt < max_retry:
                delay = retry_delay * (2 ** attempt) + random.random()
                logger.debug(
                    f"{tk.fn()}: Retryable merge error on attempt {attempt + 1}: {e}\n"
                    f"Retrying in {delay:.2f} seconds..."
                )
                time.sleep(delay)
                attempt += 1
            else:
                logger.debug(f"{tk.fn()}: Merge failed after {attempt + 1} attempt(s): {e}")
                raise


def safe_run_sql(
    query: str,
    *,
    max_retry: int = 5,
    retry_delay: float = 2.0,
) -> DataFrame:
    """Execute a SQL query with automatic retry handling.

    Args:
        query: SQL query to execute.
        max_retry: Maximum number of retries. Defaults to 5.
        retry_delay: Base delay in seconds before each retry. Defaults to 2.0.

    Returns:
        Spark ``DataFrame`` containing the query result.

    Raises:
        ValueError: If ``max_retry`` or ``retry_delay`` is negative.
        Exception: Propagates Spark errors when retries are exhausted or errors are non-retryable.
    """

    if max_retry < 0:
        raise ValueError("max_retry must be non-negative")
    if retry_delay < 0:
        raise ValueError("retry_delay must be non-negative")

    spark = SparkSession.builder.getOrCreate()
    attempt = 0

    while attempt <= max_retry:
        try:
            df = spark.sql(query)
            return df

        except Exception as e:
            if _is_retryable_error(e) and attempt < max_retry:
                delay = retry_delay * (2 ** attempt) + random.random()
                logger.debug(
                    f"{tk.fn()}: Retryable error on attempt {attempt + 1}: {e}\n"
                    f"Retrying in {delay:.2f} seconds..."
                )
                time.sleep(delay)
                attempt += 1
            else:
                logger.debug(f"{tk.fn()}: Query failed after {attempt + 1} attempt(s): {e}")
                raise
