from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from databricks.sdk.runtime import dbutils
from pyspark.sql import SparkSession
from pyspark.sql.types import (
    BinaryType,
    BooleanType,
    ByteType,
    DataType,
    DateType,
    DecimalType,
    DoubleType,
    FloatType,
    IntegerType,
    LongType,
    ShortType,
    StringType,
    TimestampType,
)

from oedis.handler.lib.utils import resolve_inbox_path, to_dbutils_path
from oedis.task_result import TaskResult

logger = logging.getLogger(__name__)


DEFAULT_FILE_FORMAT = "parquet"
SUPPORTED_FILE_FORMATS = {"csv", "parquet"}

DEFAULT_READ_OPTIONS: Dict[str, Dict[str, str]] = {
    "csv": {
        "header": "true",
        "delimiter": ",",
        "nullValue": "",
        "emptyValue": "",
    },
    "parquet": {},
}

DEFAULT_WRITE_OPTIONS: Dict[str, Dict[str, str]] = {
    "csv": {
        "header": "true",
        "delimiter": ",",
        "nullValue": "",
        "emptyValue": "",
    },
    "parquet": {},
}


def _normalize_format(
    format_value: Any,
    *,
    default: str = DEFAULT_FILE_FORMAT,
    error_prefix: str = "Unsupported format",
) -> str:
    logger.debug(
        "Normalizing format: format_value=%r default=%s error_prefix=%s",
        format_value,
        default,
        error_prefix,
    )
    file_format = str(format_value or default).strip().lower()
    if file_format not in SUPPORTED_FILE_FORMATS:
        logger.error(
            "Unsupported file format encountered: normalized_format=%s supported_formats=%s",
            file_format,
            sorted(SUPPORTED_FILE_FORMATS),
        )
        raise ValueError(f"{error_prefix}: {file_format}")

    logger.debug("Normalized format resolved to: %s", file_format)
    return file_format


def _infer_format_from_filename(
    file_name: str,
    default: str = DEFAULT_FILE_FORMAT,
) -> str:
    lower_name = str(file_name or "").strip().lower()
    logger.debug(
        "Inferring format from filename: file_name=%r normalized_name=%s default=%s",
        file_name,
        lower_name,
        default,
    )
    if lower_name.endswith(".csv"):
        logger.debug("Inferred file format from filename as csv")
        return "csv"
    if lower_name.endswith(".parquet"):
        logger.debug("Inferred file format from filename as parquet")
        return "parquet"

    logger.debug("Could not infer format from filename, using default=%s", default)
    return default


def _resolve_options(
    *,
    file_format: str,
    options: Any,
    defaults: Dict[str, Dict[str, str]],
) -> Dict[str, str]:
    logger.debug(
        "Resolving options for file_format=%s options=%r default_keys=%s",
        file_format,
        options,
        list(defaults.keys()),
    )
    if options is None:
        resolved = dict(defaults[file_format])
        logger.debug(
            "No custom options provided, using default options for format=%s: %s",
            file_format,
            resolved,
        )
        return resolved

    resolved = dict(defaults[file_format])
    resolved.update({str(k): str(v) for k, v in dict(options).items()})
    logger.debug(
        "Resolved options for format=%s: defaults=%s merged=%s",
        file_format,
        defaults[file_format],
        resolved,
    )
    return resolved


def _apply_upload_sql(
    spark: SparkSession,
    df,
    sql: str | None,
):
    if not sql:
        logger.debug("No upload SQL provided, returning input DataFrame unchanged.")
        return df

    temp_view_name = f"_dis_upload_input_df_{uuid.uuid4().hex}"
    rendered_sql = sql.replace("{source}", temp_view_name)

    logger.debug(
        "Applying upload SQL transform: temp_view_name=%s original_sql=%s rendered_sql=%s",
        temp_view_name,
        sql,
        rendered_sql,
    )

    df.createOrReplaceTempView(temp_view_name)
    try:
        result_df = spark.sql(rendered_sql)
        logger.debug(
            "Upload SQL transform executed successfully using temp_view_name=%s",
            temp_view_name,
        )
        return result_df
    finally:
        try:
            spark.catalog.dropTempView(temp_view_name)
            logger.debug("Dropped temp view after upload SQL transform: %s", temp_view_name)
        except Exception as exc:
            logger.warning(
                "Failed to drop temp view %s after upload SQL transform: %s",
                temp_view_name,
                exc,
            )


def _quote_sql_identifier(name: str) -> str:
    quoted = f"`{str(name).replace('`', '``')}`"
    logger.debug("Quoted SQL identifier: original=%r quoted=%s", name, quoted)
    return quoted


def _spark_flat_primitive_type_to_sql(data_type: DataType) -> str:
    """
    Map a flat primitive Spark data type to Databricks/Spark SQL column DDL.

    Only flat primitive schemas are supported for create_sql {columns}.
    Nested or complex types such as ARRAY, MAP, and STRUCT are intentionally
    rejected to keep the generated DDL predictable.
    """
    logger.debug("Mapping Spark type to SQL type: spark_type=%s", data_type.simpleString())

    if isinstance(data_type, StringType):
        return "STRING"
    if isinstance(data_type, BooleanType):
        return "BOOLEAN"
    if isinstance(data_type, ByteType):
        return "TINYINT"
    if isinstance(data_type, ShortType):
        return "SMALLINT"
    if isinstance(data_type, IntegerType):
        return "INT"
    if isinstance(data_type, LongType):
        return "BIGINT"
    if isinstance(data_type, FloatType):
        return "FLOAT"
    if isinstance(data_type, DoubleType):
        return "DOUBLE"
    if isinstance(data_type, DecimalType):
        return f"DECIMAL({data_type.precision},{data_type.scale})"
    if isinstance(data_type, DateType):
        return "DATE"
    if isinstance(data_type, TimestampType):
        return "TIMESTAMP"
    if data_type.simpleString() == "timestamp_ntz":
        return "TIMESTAMP_NTZ"
    if isinstance(data_type, BinaryType):
        return "BINARY"

    logger.error(
        "Unsupported Spark type for create_sql {columns}: %s",
        data_type.simpleString(),
    )
    raise ValueError(
        f"Unsupported Spark type for create_sql {{columns}}: {data_type.simpleString()}. "
        "Only flat primitive schemas are supported."
    )


def _render_create_columns_from_schema(df) -> str:
    """
    Render SQL column definitions from df.schema for create_sql {columns}.

    Supported schemas are limited to flat primitive columns only.
    All generated columns are nullable by default; NOT NULL is not generated.
    """
    logger.debug("Rendering create_sql columns from DataFrame schema: %s", df.schema.simpleString())
    columns: List[str] = []
    for field in df.schema.fields:
        logger.debug(
            "Rendering schema field for create_sql: name=%s type=%s nullable=%s",
            field.name,
            field.dataType.simpleString(),
            field.nullable,
        )
        col_type = _spark_flat_primitive_type_to_sql(field.dataType)
        columns.append(f"{_quote_sql_identifier(field.name)} {col_type}")

    rendered = ",\n".join(columns)
    logger.debug("Rendered create_sql columns:\n%s", rendered)
    return rendered


def _ensure_upload_table_exists(
    spark: SparkSession,
    *,
    table: str,
    df,
    create_sql: str | None,
    request_id: str,
) -> None:
    table_exists = spark.catalog.tableExists(table)
    logger.debug(
        "Upload request_id=%s table=%s table_exists=%s",
        request_id,
        table,
        table_exists,
    )

    if table_exists:
        logger.debug(
            "Upload request_id=%s table=%s create_sql skipped: table already exists.",
            request_id,
            table,
        )
        return

    if not create_sql:
        logger.debug(
            "Upload request_id=%s table=%s create_sql skipped: not provided.",
            request_id,
            table,
        )
        return

    rendered_sql = create_sql.replace("{columns}", _render_create_columns_from_schema(df))
    logger.debug(
        "Upload request_id=%s table=%s rendered create_sql:\n%s",
        request_id,
        table,
        rendered_sql,
    )

    spark.sql(rendered_sql)

    if not spark.catalog.tableExists(table):
        logger.error(
            "Upload request_id=%s table=%s create_sql executed but table still does not exist.",
            request_id,
            table,
        )
        raise RuntimeError(
            f"request_id={request_id} table={table}: create_sql executed but table still does not exist."
        )

    logger.debug(
        "Upload request_id=%s table=%s create_sql executed.",
        request_id,
        table,
    )


def _write_df_to_single_file(
    df,
    dest_file: str,
    temp_volume: str,
    file_format: str,
    options: Dict[str, str],
) -> None:
    dest_path = Path(dest_file)
    dest_uri = to_dbutils_path(dest_path.as_posix())

    logger.debug(
        "Writing DataFrame to single file: dest_file=%s dest_uri=%s temp_volume=%s file_format=%s options=%s",
        dest_file,
        dest_uri,
        temp_volume,
        file_format,
        options,
    )

    try:
        logger.debug("Removing destination before write if it exists: %s", dest_uri)
        dbutils.fs.rm(dest_uri, recurse=True)
    except Exception as exc:
        logger.debug("Destination cleanup skipped or failed for %s: %s", dest_uri, exc)

    temp_path = Path(temp_volume) / ".dis_tmp" / f"{dest_path.name}.{uuid.uuid4().hex}"
    temp_uri = to_dbutils_path(temp_path.as_posix())

    logger.debug(
        "Writing temporary single-file output: temp_path=%s temp_uri=%s",
        temp_path.as_posix(),
        temp_uri,
    )

    (
        df.coalesce(1)
        .write.format(file_format)
        .mode("overwrite")
        .options(**options)
        .save(temp_uri)
    )

    logger.debug("Temporary single-file write completed: temp_uri=%s", temp_uri)

    entries = dbutils.fs.ls(temp_uri)
    part_files = [f for f in entries if f.name.startswith("part-")]

    logger.debug(
        "Temporary output directory listing for %s: entries=%s part_files=%s",
        temp_uri,
        [f.name for f in entries],
        [f.name for f in part_files],
    )

    if len(part_files) != 1:
        logger.error(
            "Expected single output file in %s but found %s. Entries=%s",
            temp_uri,
            len(part_files),
            [f.name for f in entries],
        )
        raise RuntimeError(
            f"Expected single output file in {temp_uri}, found {len(part_files)}: "
            f"{[f.name for f in entries]}"
        )

    logger.debug(
        "Copying final single output file from %s to %s",
        part_files[0].path,
        dest_uri,
    )
    dbutils.fs.cp(part_files[0].path, dest_uri)

    try:
        dbutils.fs.rm(temp_uri, recurse=True)
        logger.debug("Cleaned up temporary output directory: %s", temp_uri)
    except Exception as exc:
        logger.warning("Failed to clean up temp directory %s: %s", temp_uri, exc)


def _stage_upload_source_for_spark(
    source_path: str,
    temp_volume: str,
) -> Dict[str, str]:
    source_name = Path(source_path).name
    staged_file_path = (
        Path(temp_volume) / ".dis_tmp" / f"upload.{uuid.uuid4().hex}" / source_name
    )
    staged_file_uri = to_dbutils_path(staged_file_path.as_posix())
    staged_dir_uri = to_dbutils_path(staged_file_path.parent.as_posix())

    logger.debug(
        "Staging upload source for Spark: source_path=%s source_name=%s staged_file_uri=%s staged_dir_uri=%s",
        source_path,
        source_name,
        staged_file_uri,
        staged_dir_uri,
    )

    dbutils.fs.cp(to_dbutils_path(source_path), staged_file_uri)

    logger.debug(
        "Completed staging upload source for Spark: source_path=%s staged_file_uri=%s",
        source_path,
        staged_file_uri,
    )

    return {
        "staged_file_uri": staged_file_uri,
        "staged_dir_uri": staged_dir_uri,
    }


def _resolve_download_spec(spec: Dict[str, Any], inbox: Path) -> Dict[str, Any]:
    logger.debug("Resolving download spec: %s", spec)

    request_id = str(spec["id"])
    select_sql = spec.get("select_sql")
    table = spec.get("table")

    if table:
        logger.debug(
            "Download spec request_id=%s using table=%s, overriding select_sql with table select.",
            request_id,
            table,
        )
        select_sql = f"SELECT * FROM {table}"

    file_format = _normalize_format(
        spec.get("format"),
        error_prefix="Unsupported download format",
    )

    write_conf = spec.get("write") or {}
    write_options = _resolve_options(
        file_format=file_format,
        options=write_conf.get("options"),
        defaults=DEFAULT_WRITE_OPTIONS,
    )

    file_name = str(spec.get("file") or request_id)
    if not file_name.endswith(f".{file_format}"):
        logger.debug(
            "Download spec request_id=%s appending file extension .%s to file_name=%s",
            request_id,
            file_format,
            file_name,
        )
        file_name = f"{file_name}.{file_format}"

    dest_path = resolve_inbox_path(inbox, file_name, field_name="file").as_posix()

    resolved = {
        "request_id": request_id,
        "select_sql": str(select_sql).strip(),
        "table": table,
        "file_format": file_format,
        "write": write_options,
        "dest_path": dest_path,
    }

    logger.debug("Resolved download spec for request_id=%s: %s", request_id, resolved)
    return resolved


def _resolve_upload_spec(spec: Dict[str, Any], inbox: Path) -> Dict[str, Any]:
    logger.debug("Resolving upload spec: %s", spec)

    request_id = str(spec["id"])
    table = str(spec["table"])
    write_conf = spec.get("write") or {}

    mode = str(write_conf.get("mode") or "append").lower()
    logger.debug(
        "Upload spec request_id=%s resolved write mode=%s from write_conf=%s",
        request_id,
        mode,
        write_conf,
    )

    write_options = {str(k): str(v) for k, v in (write_conf.get("options") or {}).items()}
    logger.debug(
        "Upload spec request_id=%s resolved write options=%s",
        request_id,
        write_options,
    )

    select_sql = spec.get("select_sql")
    if select_sql is not None:
        select_sql = str(select_sql).strip()
        logger.debug(
            "Upload spec request_id=%s validating select_sql=%s",
            request_id,
            select_sql,
        )

        if "{source}" not in select_sql:
            logger.error(
                "Upload spec request_id=%s select_sql missing required {source} placeholder.",
                request_id,
            )
            raise ValueError(
                f"request_id={request_id} table={table}: field 'select_sql' must contain '{{source}}'."
            )

        lower_sql = select_sql.lower()
        if not (lower_sql.startswith("select") or lower_sql.startswith("with")):
            logger.error(
                "Upload spec request_id=%s select_sql must start with SELECT or WITH: %s",
                request_id,
                select_sql,
            )
            raise ValueError(
                f"request_id={request_id} table={table}: field 'select_sql' must start with SELECT or WITH."
            )

        if ";" in select_sql:
            logger.error(
                "Upload spec request_id=%s select_sql contains forbidden semicolon: %s",
                request_id,
                select_sql,
            )
            raise ValueError(
                f"request_id={request_id} table={table}: field 'select_sql' must not contain ';'."
            )

    create_sql = spec.get("create_sql")
    if create_sql is not None:
        create_sql = str(create_sql).strip()
        logger.debug(
            "Upload spec request_id=%s resolved create_sql=%s",
            request_id,
            create_sql,
        )

    source_file = str(spec.get("file") or "")
    source_format = _normalize_format(
        spec.get("format") or _infer_format_from_filename(source_file),
        error_prefix="Unsupported format",
    )

    if not source_file:
        logger.debug(
            "Upload spec request_id=%s no source file provided, defaulting to request-based file name.",
            request_id,
        )
        source_file = f"{request_id}.{source_format}"

    source_path = resolve_inbox_path(inbox, source_file, field_name="file").as_posix()

    read_options = _resolve_options(
        file_format=source_format,
        options=((spec.get("read") or {}).get("options")),
        defaults=DEFAULT_READ_OPTIONS,
    )

    resolved = {
        "request_id": request_id,
        "table": table,
        "mode": mode,
        "source_format": source_format,
        "source_path": source_path,
        "read": read_options,
        "write": write_options,
        "select_sql": select_sql,
        "create_sql": create_sql,
    }

    logger.debug("Resolved upload spec for request_id=%s: %s", request_id, resolved)
    return resolved


def download(ctx) -> TaskResult:
    handler_start = time.perf_counter()
    task_param = getattr(ctx, "task_params", None) or {}
    requests = task_param.get("requests") or []

    logger.debug(
        "Starting database download handler: task_id=%s request_count=%s ctx_inbox=%r ctx_inbox_vol=%r",
        getattr(ctx, "task_id", None),
        len(requests),
        getattr(ctx, "inbox", None),
        getattr(ctx, "inbox_vol", None),
    )

    if not getattr(ctx, "inbox", None):
        logger.error("Download handler missing ctx.inbox")
        raise RuntimeError("ctx.inbox is not set.")
    if not getattr(ctx, "inbox_vol", None):
        logger.error("Download handler missing ctx.inbox_vol")
        raise RuntimeError("ctx.inbox_vol is not set.")

    inbox = Path(ctx.inbox)
    inbox_vol = ctx.inbox_vol
    spark = SparkSession.builder.getOrCreate()

    logger.info("Database download temporary Spark write volume: %s", inbox_vol)
    logger.debug("Database download inbox path resolved to: %s", inbox.as_posix())

    outputs: List[Dict[str, Any]] = []

    for index, spec in enumerate(requests, start=1):
        logger.debug("Processing download request %s/%s: %s", index, len(requests), spec)

        resolved = _resolve_download_spec(spec, inbox)

        request_id = resolved["request_id"]
        select_sql = resolved["select_sql"]
        table = resolved["table"]
        file_format = resolved["file_format"]
        write_options = resolved["write"]
        dest = resolved["dest_path"]

        request_start = time.perf_counter()

        logger.debug(
            "Executing download SQL for request_id=%s: %s",
            request_id,
            select_sql,
        )
        df = spark.sql(select_sql)

        logger.debug("Counting rows for download request_id=%s", request_id)
        row_count = df.count()
        logger.debug("Download request_id=%s row_count=%s", request_id, row_count)

        logger.info(
            "Writing download output id=%s format=%s to %s",
            request_id,
            file_format,
            dest,
        )
        logger.debug(
            "Download request_id=%s select_sql=%s write options=%s",
            request_id,
            select_sql,
            write_options,
        )

        _write_df_to_single_file(
            df=df,
            dest_file=dest,
            temp_volume=inbox_vol,
            file_format=file_format,
            options=write_options,
        )

        duration_sec = round(time.perf_counter() - request_start, 6)
        logger.debug(
            "Completed download request_id=%s table=%s dest=%s duration_sec=%s",
            request_id,
            table,
            dest,
            duration_sec,
        )

        outputs.append(
            {
                "src": {"type": "table", "path": table or f"select_sql:{request_id}", "size": row_count},
                "dest": {"type": "file", "path": dest, "size": row_count},
                "duration_sec": duration_sec,
            }
        )

    total_duration_sec = round(time.perf_counter() - handler_start, 3)
    logger.debug(
        "Database download handler completed: task_id=%s output_count=%s total_duration_sec=%s",
        getattr(ctx, "task_id", None),
        len(outputs),
        total_duration_sec,
    )

    return TaskResult(
        task_id=ctx.task_id or "",
        handler="oedis.handler.database.download",
        status="success",
        outputs=outputs,
        metrics={
            "total_items": len(outputs),
            "total_duration_sec": total_duration_sec,
        },
    )


def upload(ctx) -> TaskResult:
    handler_start = time.perf_counter()
    task_param = getattr(ctx, "task_params", None) or {}
    requests = task_param.get("requests") or []

    logger.debug(
        "Starting database upload handler: task_id=%s request_count=%s ctx_inbox=%r ctx_inbox_vol=%r",
        getattr(ctx, "task_id", None),
        len(requests),
        getattr(ctx, "inbox", None),
        getattr(ctx, "inbox_vol", None),
    )

    if not getattr(ctx, "inbox", None):
        logger.error("Upload handler missing ctx.inbox")
        raise RuntimeError("ctx.inbox is not set.")
    if not getattr(ctx, "inbox_vol", None):
        logger.error("Upload handler missing ctx.inbox_vol")
        raise RuntimeError("ctx.inbox_vol is not set.")

    inbox = Path(ctx.inbox)
    inbox_vol = ctx.inbox_vol
    spark = SparkSession.builder.getOrCreate()

    logger.info("inbox is %s", inbox.as_posix())
    logger.info("Database upload temporary Spark read volume: %s", inbox_vol)

    outputs: List[Dict[str, Any]] = []

    for index, spec in enumerate(requests, start=1):
        logger.debug("Processing upload request %s/%s: %s", index, len(requests), spec)

        resolved = _resolve_upload_spec(spec, inbox)

        request_id = resolved["request_id"]
        table = resolved["table"]
        mode = resolved["mode"]
        source_format = resolved["source_format"]
        source_path = resolved["source_path"]
        read_options = resolved["read"]
        write_options = resolved["write"]
        select_sql = resolved["select_sql"]
        create_sql = resolved["create_sql"]

        logger.info("Upload request_id=%s resolved source file path: %s", request_id, source_path)
        logger.debug("Upload request_id=%s detected source format: %s", request_id, source_format)
        logger.debug("Upload request_id=%s read options=%s", request_id, read_options)
        logger.debug(
            "Upload request_id=%s resolved write options: %s create_sql_present=%s",
            request_id,
            write_options,
            bool(create_sql),
        )
        if select_sql:
            logger.info(
                "Upload request_id=%s applying SQL transform with {source} placeholder.",
                request_id,
            )

        request_start = time.perf_counter()
        staged = _stage_upload_source_for_spark(
            source_path=source_path,
            temp_volume=inbox_vol,
        )

        try:
            logger.debug(
                "Upload request_id=%s staged source for Spark: %s",
                request_id,
                staged,
            )

            if source_format == "csv":
                logger.debug(
                    "Upload request_id=%s reading staged CSV from %s with options=%s",
                    request_id,
                    staged["staged_file_uri"],
                    read_options,
                )
                df = (
                    spark.read
                    .format("csv")
                    .options(**read_options)
                    .load(staged["staged_file_uri"])
                )
            else:
                logger.debug(
                    "Upload request_id=%s reading staged parquet from %s",
                    request_id,
                    staged["staged_file_uri"],
                )
                df = spark.read.parquet(staged["staged_file_uri"])

            logger.debug(
                "Upload request_id=%s input DataFrame schema before SQL transform: %s",
                request_id,
                df.schema.simpleString(),
            )

            df = _apply_upload_sql(spark, df, select_sql)

            logger.debug(
                "Upload request_id=%s DataFrame schema after SQL transform: %s",
                request_id,
                df.schema.simpleString(),
            )

            _ensure_upload_table_exists(
                spark,
                table=table,
                df=df,
                create_sql=create_sql,
                request_id=request_id,
            )

            logger.debug("Counting rows for upload request_id=%s", request_id)
            row_count = df.count()
            logger.debug("Upload request_id=%s row_count=%s", request_id, row_count)

            logger.info(
                "Writing upload request_id=%s table=%s mode=%s format=%s source=%s",
                request_id,
                table,
                mode,
                source_format,
                source_path,
            )
            logger.debug(
                "Upload request_id=%s write options=%s",
                request_id,
                write_options,
            )

            df.write.format("delta").mode(mode).options(**write_options).saveAsTable(table)

            duration_sec = round(time.perf_counter() - request_start, 6)
            logger.debug(
                "Completed upload request_id=%s table=%s source=%s duration_sec=%s",
                request_id,
                table,
                source_path,
                duration_sec,
            )

            outputs.append(
                {
                    "src": {"type": "file", "path": source_path, "size": row_count},
                    "dest": {"type": "table", "path": table, "size": row_count},
                    "duration_sec": duration_sec,
                }
            )
        finally:
            try:
                dbutils.fs.rm(staged["staged_dir_uri"], recurse=True)
                logger.debug(
                    "Cleaned up staged upload directory for request_id=%s path=%s",
                    request_id,
                    staged["staged_dir_uri"],
                )
            except Exception as exc:
                logger.warning(
                    "Failed to clean up staged upload directory path=%s: %s",
                    staged["staged_dir_uri"],
                    exc,
                )

    total_duration_sec = round(time.perf_counter() - handler_start, 3)
    logger.debug(
        "Database upload handler completed: task_id=%s output_count=%s total_duration_sec=%s",
        getattr(ctx, "task_id", None),
        len(outputs),
        total_duration_sec,
    )

    return TaskResult(
        task_id=ctx.task_id or "",
        handler="oedis.handler.database.upload",
        status="success",
        outputs=outputs,
        metrics={
            "total_items": len(outputs),
            "total_duration_sec": total_duration_sec,
        },
    )
