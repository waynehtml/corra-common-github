"""File registry definition for the CORRA rate history source file."""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql.types import (
    DecimalType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

SCHEMA = StructType(
    [
        StructField("number", IntegerType(), True),
    ]
)


def transform(df: DataFrame, context: dict[str, object] | None = None) -> DataFrame:
    return df

DEFINITION = {
    "path": "tbl_sample.csv",
    "schema": SCHEMA,
    "table": "tbl_sample",
    "encoding": "windows-1252",
    "transform": transform,
}
