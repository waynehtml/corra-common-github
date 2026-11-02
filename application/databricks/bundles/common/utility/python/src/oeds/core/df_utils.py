from typing import Optional, List, Any, Dict, Union
from pyspark.sql import functions as F, types as T
from pyspark.sql import SparkSession, DataFrame
from delta.tables import DeltaTable
from pyspark.sql.types import (
    StructType, StructField, ArrayType, DoubleType, FloatType
)

def add_missing_cols(
    df: DataFrame, target_schema: StructType, default_values: Any = None
) -> DataFrame:
    """
    Ensures the DataFrame has all columns defined in the target schema.
    Adds missing columns with specified values (defaults to None) of
    the appropriate types.

    Args:
        df (DataFrame): Input DataFrame.
        target_schema (StructType): The target schema.
        default_values (Any): Default values to use for missing columns.
            Defaults to None, which will create null columns.
            If default_values is a dict, it should map column names to their
            default values. If a column is missing in the dict, it will use None.
            If default_values is not a dict, it will use the same value for all
            missing columns.

    Returns:
        DataFrame: A new DataFrame with all schema columns present.
    """
    # Build list of expressions for select()
    final_cols = []
    for field in target_schema.fields:
        name = field.name
        dtype = field.dataType

        # If default_value is a dict, use the value for this field if it exists
        if isinstance(default_values, dict):
            default_value = default_values.get(name, None)
        else:
            default_value = default_values

        # If the column exists, use it as is;
        # otherwise create a new column with the default value
        if name in df.columns:
            final_cols.append(F.col(name))
        else:
            final_cols.append(F.lit(default_value).cast(dtype).alias(name))

    return df.select(*final_cols)

def flatten_all_col(df, sep: str = "_"):
    """
    Utility to flatten nested StructType fields in a Spark DataFrame.

    • Top-level primitive/array/map fields are kept as-is.
    • Nested StructType fields are recursively flattened with parent prefixes.
    • Arrays (including array<struct<...>>) are left untouched (not exploded/flattened).
    """
    def _expand(col_expr: F.Column, out_name: str, dt: T.DataType):
        if isinstance(dt, T.StructType):
            cols = []
            for f in dt.fields:
                child_expr = col_expr.getField(f.name)
                child_name = f"{out_name}{sep}{f.name}"
                cols.extend(_expand(child_expr, child_name, f.dataType))
            return cols
        else:
            return [col_expr.alias(out_name)]

    select_cols = []
    for f in df.schema.fields:
        if isinstance(f.dataType, T.StructType):
            select_cols.extend(_expand(F.col(f.name), f.name, f.dataType))
        else:
            select_cols.append(F.col(f.name))
    return df.select(*select_cols)

def flatten_selected_col(df, sep: str = "_", only: Optional[List[str]] = None):
    """
    Parameters
    ----------
    df : DataFrame
    sep : str
        Separator between parent and child names (default "_").
    only : Optional[List[str]]
        Column paths to flatten (e.g., ["sender", "receiver", "commit.sender"]).
    """

    def _expand(col_expr, out_name, dt):
        # Recursively flatten only StructTypes; leave arrays/maps/primitives as-is.
        if isinstance(dt, T.StructType):
            cols = []
            for f in dt.fields:
                child_expr = col_expr.getField(f.name)
                child_name = "{}{}{}".format(out_name, sep, f.name)
                cols.extend(_expand(child_expr, child_name, f.dataType))
            return cols
        else:
            return [col_expr.alias(out_name)]

    # ---- Helpers for selection paths ----
    def _split_path(p):
        return [x for x in p.split(".") if x]

    def _dtype_for_path(schema, parts):
        dt = schema
        for name in parts:
            if not isinstance(dt, T.StructType):
                raise ValueError("Path {} is not a struct at '{}'".format(".".join(parts), name))
            fld = next((f for f in dt.fields if f.name == name), None)
            if fld is None:
                raise ValueError("Field '{}' not found under schema path: {}".format(name, ".".join(parts)))
            dt = fld.dataType
        return dt

    # Normalize selections
    only_paths = None if only is None else [_split_path(p) for p in only]
    paths_by_root = {}
    if only_paths is not None:
        for parts in only_paths:
            if parts:
                paths_by_root.setdefault(parts[0], []).append(parts)

    select_cols = []

    # No 'only' -> flatten all StructTypes
    if only_paths is None:
        for f in df.schema.fields:
            if isinstance(f.dataType, T.StructType):
                select_cols.extend(_expand(F.col(f.name), f.name, f.dataType))
            else:
                select_cols.append(F.col(f.name))
        return df.select(*select_cols)

    # With 'only' selection:
    for f in df.schema.fields:
        root = f.name
        if root not in paths_by_root:
            select_cols.append(F.col(root))
            continue

        flatten_entire_root = any(len(p) == 1 and p[0] == root for p in paths_by_root[root])

        if flatten_entire_root and isinstance(f.dataType, T.StructType):
            select_cols.extend(_expand(F.col(root), root, f.dataType))
        else:
            # keep original root
            select_cols.append(F.col(root))
            # flatten specific subpaths
            for parts in paths_by_root[root]:
                if len(parts) < 2:
                    continue
                col_expr = F.col(parts[0])
                dt = f.dataType
                for step in parts[1:]:
                    if not isinstance(dt, T.StructType):
                        raise ValueError("Path '{}' traverses a non-struct at '{}'".format(".".join(parts), step))
                    col_expr = col_expr.getField(step)
                    dt = next(ff.dataType for ff in dt.fields if ff.name == step)
                out_name = sep.join(parts)  # e.g., "commit_sender"
                if isinstance(dt, T.StructType):
                    select_cols.extend(_expand(col_expr, out_name, dt))
                else:
                    select_cols.append(col_expr.alias(out_name))

    return df.select(*select_cols)

def contains_zero_values(df: DataFrame, column: str) -> bool:
    """
    Check if a DataFrame column contains zero values.

    Args:
        df (DataFrame): The DataFrame to check.
        column (str): The name of the column to check.

    Returns:
        bool: True if the column contains zero values, False otherwise.
    """
    return df.filter(F.col(column) == 0).count() > 0


def contains_null_or_nan(df: DataFrame, column: str) -> bool:
    """
    Check if a DataFrame column contains null or NaN values.

    Args:
        df (DataFrame): The DataFrame to check.
        column (str): The name of the column to check.

    Returns:
        bool: True if the column contains null or NaN values, False otherwise.
    """
    return (
        df.filter(F.col(column).isNull() | F.isnan(F.col(column))).count() > 0
    )

def check_not_null(df: DataFrame, schema: StructType) -> DataFrame:
    """
    Return a one-row DataFrame with counts of violations for every field
    that is non-nullable in `schema` (nullable=False) and for arrays with
    containsNull=False. Includes NaN checks for float/double.

    Columns in the result are path-like aliases (e.g., "key_contacts[].title").
    A zero means no violations; >0 means that many rows violate the rule.
    """
    aggs = []

    def _sum_when(alias: str, cond):
        aggs.append(F.sum(F.when(cond, 1).otherwise(0)).alias(alias))

    def walk(path: str, field: StructField):
        col_path = f"{path}.{field.name}" if path else field.name
        dt = field.dataType

        # If the field itself must not be null
        if field.nullable is False:
            _sum_when(col_path, F.col(col_path).isNull())

        if isinstance(dt, StructType):
            for sub in dt.fields:
                walk(col_path, sub)

        elif isinstance(dt, ArrayType):
            # Array must not be null (already handled if field.nullable==False)

            # Elements must not be null?
            if dt.containsNull is False:
                _sum_when(col_path + "[]", F.expr(f"exists({col_path}, x -> x IS NULL)"))

            elem = dt.elementType
            if isinstance(elem, StructType):
                # For each non-nullable subfield in the element struct
                for sub in elem.fields:
                    sub_alias = f"{col_path}[].{sub.name}"
                    if sub.nullable is False:
                        _sum_when(sub_alias, F.expr(f"exists({col_path}, x -> x.{sub.name} IS NULL)"))
                    # NaN check on float/double subfields
                    if isinstance(sub.dataType, (DoubleType, FloatType)):
                        _sum_when(sub_alias + "_isnan", F.expr(f"exists({col_path}, x -> isnan(x.{sub.name}))"))
            else:
                # Array of primitives: NaN check if numeric
                if isinstance(elem, (DoubleType, FloatType)):
                    _sum_when(col_path + "[]_isnan", F.expr(f"exists({col_path}, x -> isnan(x))"))

        else:
            # Primitive column: add NaN check if float/double
            if isinstance(dt, (DoubleType, FloatType)):
                _sum_when(col_path + "_isnan", F.isnan(F.col(col_path)))

    for f in schema.fields:
        walk("", f)

    violations = {}
    if aggs:
        # df.agg(*aggs) returns a DataFrame with one row
        row = df.agg(*aggs).first()   # Row object
        # Directly filter key/value pairs
        violations = [{"key": k, "value": int(v)}
                    for k, v in row.asDict().items()
                    if v not in (0, None)]
    return violations
