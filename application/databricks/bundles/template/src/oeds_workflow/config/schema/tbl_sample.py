"""Schema definition for the tbl_sample table."""

from __future__ import annotations

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
        StructField(
            "key", LongType(), False,
            metadata={
                "identity": {"start": 1, "always": True},
                "comment": "Surrogate key for CORRA rate history rows.",
            },
        ),
        StructField(
            "number", IntegerType(), False,
            metadata={"comment": "number value."},
        )
    ]
)

SQL = """
(
{schema}
)
USING DELTA
"""

DEFINITION = {
    "catalog": "application_cat",
    "database": "oedisint_db",
    "name": "tbl_sample",
    "kind": "table",
    "schema": SCHEMA,
    "sql": SQL,
}
