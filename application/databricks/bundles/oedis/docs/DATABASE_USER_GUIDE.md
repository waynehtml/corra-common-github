# database

Handlers:

- `oedis.handler.database.download`
- `oedis.handler.database.upload`

This handler family uses Spark for Unity Catalog table reads/writes and file reads/writes in `csv` and `parquet` formats.

## Download (`oedis.handler.database.download`)

Downloads data from a Unity Catalog table or SQL query result into a single file under `runtime.inbox`.

### Request fields

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `id` | Yes | — | Used for the default output file name. |
| `select_sql` or `table` | Yes | — | If `table` is provided, the handler generates `SELECT * FROM <table>`. |
| `format` | No | `parquet` | Supported values: `csv`, `parquet`. |
| `write.options` | No | format defaults | Spark writer options passed to `DataFrameWriter.options(**write.options)`. |
| `file` | No | `<id>.<format>` | Output file name or relative path under `runtime.inbox`. Leading `/` is treated as inbox-rooted shorthand. Paths must stay under `runtime.inbox`. |

### Runtime requirements

- `ctx.inbox` must be set. This is the destination root for output files.
- `ctx.inbox_vol` must be set. This is used as a temporary staging location for Spark single-file writes.

### Spark write defaults

#### CSV

Used with `DataFrameWriter.format("csv")`:

```json
{
  "header": "true",
  "delimiter": ",",
  "nullValue": "",
  "emptyValue": ""
}
```

#### Parquet

Used with `DataFrameWriter.format("parquet")`:

```json
{}
```

### Behavior

* If both `select_sql` and `table` are provided, `table` takes precedence.
* Output is always written as a single file.
* If `file` does not already end with `.<format>`, the extension is appended automatically.
* The handler writes through a temporary directory under `ctx.inbox_vol`, then copies the generated single `part-*` file to the final destination.
* `file` is always anchored under `ctx.inbox`. Leading `/` is treated as inbox-rooted shorthand and paths containing `..` are rejected.

### Example: download from table

```json
{
  "requests": [
    {
      "id": "customers",
      "table": "main.sales.customers",
      "format": "csv",
      "file": "exports/customers.csv",
      "write": {
        "options": {
          "header": "true",
          "delimiter": "|"
        }
      }
    }
  ]
}
```

### Example: download from SQL

```json
{
  "requests": [
    {
      "id": "recent_orders",
      "select_sql": "SELECT * FROM main.sales.orders WHERE order_date >= current_date() - INTERVAL 7 DAYS",
      "format": "parquet",
      "file": "exports/recent_orders.parquet"
    }
  ]
}
```

### Result shape

Each output item includes:

```json
{
  "src": {
    "type": "table",
    "path": "main.sales.customers",
    "size": 123
  },
  "dest": {
    "type": "file",
    "path": "/path/to/runtime/inbox/exports/customers.csv",
    "size": 123
  },
  "duration_sec": 0.456789
}
```

For SQL-based requests without `table`, `src.path` is reported as:

```json
"select_sql:<id>"
```

---

## Upload (`oedis.handler.database.upload`)

Uploads a file from `runtime.inbox` into a Unity Catalog table using Spark.

### Request fields

| Field                 | Required | Default              | Notes                                                                                                              |
| --------------------- | -------- | -------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `id`                  | Yes      | —                    | Used for the default source file name when `file` is omitted.                                                      |
| `table`               | Yes      | —                    | Target Unity Catalog table, typically `catalog.schema.table`.                                                      |
| `file`                | No       | `<id>.<format>`      | Relative path under `runtime.inbox`. Leading `/` is treated as inbox-rooted shorthand and paths must stay under `runtime.inbox`. |
| `format`              | No       | inferred / `parquet` | Supported values: `csv`, `parquet`. If omitted, inferred from the file extension; otherwise defaults to `parquet`. |
| `read.options`        | No       | format defaults      | Spark reader options.                                                                                              |
| `select_sql`                 | No       | —                    | SQL transform applied after file read. Use `{source}` as a placeholder for the staged input DataFrame temp view name. |
| `create_sql`          | No       | —                    | One-time table bootstrap SQL. Executed only when target table does not exist. Supports `{columns}` placeholder generated from upload DataFrame schema. |
| `write.mode`          | No       | `append`             | Supported values: `append`, `overwrite`.                                                                           |
| `write.options`       | No       | `{}`                 | Spark writer options passed to Delta `DataFrameWriter.options(**write.options)`.                                   |

### Runtime requirements

* `ctx.inbox` must be set. This is the source root for upload files.
* `ctx.inbox_vol` must be set. This is used as a temporary staging location so Spark can read the file reliably.

### Spark read defaults

#### CSV

Used with `DataFrameReader.format("csv")`:

```json
{
  "header": "true",
  "delimiter": ",",
  "nullValue": "",
  "emptyValue": ""
}
```

#### Parquet

Used with `DataFrameReader.parquet(...)`:

```json
{}
```

### Upload behavior

* The source file is copied to a temporary staging location under `ctx.inbox_vol` before Spark reads it.
* If `format` is omitted:

  * `.csv` => `csv`
  * `.parquet` => `parquet`
  * otherwise => `parquet`
* If `file` is omitted, the default source file is `<id>.<format>`.
* `file` is always resolved under `ctx.inbox`. Leading `/` is treated as inbox-rooted shorthand and paths containing `..` are rejected.
* If `select_sql` is provided, the input DataFrame is registered as a temporary Spark view, and `{source}` in `select_sql` is replaced with that generated view name before execution.
* The source DataFrame is fully built before table existence is checked.
* If `create_sql` is provided and the target table does not exist, `create_sql` is executed once before the write. Existing tables are never auto-altered.
* `{columns}` in `create_sql` is rendered from the Spark DataFrame schema using Spark SQL primitive types.
* Only primitive Spark types are supported for `{columns}` rendering. Unsupported types fail the request with a clear error.
* `write.mode` supports only:

  * `append`
  * `overwrite`
* The write uses:

```python
df.write.format("delta").mode(write.mode).options(**write.options).saveAsTable(table)
```

* If `create_sql` is not provided, missing target tables are still created by Spark through `saveAsTable(table)` behavior.
### Example: simple upload

```json
{
  "requests": [
    {
      "id": "customers",
      "table": "main.sales.customers",
      "file": "incoming/customers.csv",
      "format": "csv",
      "read": {
        "options": {
          "header": "true"
        }
      },
      "write": {
        "mode": "append"
      }
    }
  ]
}
```

### Example: upload with SQL transform and write options

```json
{
  "requests": [
    {
      "id": "customers",
      "table": "main.sales.customers",
      "file": "incoming/customers.csv",
      "format": "csv",
      "read": {
        "options": {
          "header": "true",
          "delimiter": ","
        }
      },
      "select_sql": "SELECT customer_id, upper(customer_name) AS customer_name, current_date() AS business_date FROM {source}",
      "write": {
        "mode": "append",
        "options": {
          "mergeSchema": "true"
        }
      }
    }
  ]
}
```

### Example: upload with bootstrap create SQL

```json
{
  "requests": [
    {
      "id": "customers",
      "table": "main.sales.customers",
      "file": "incoming/customers.csv",
      "format": "csv",
      "create_sql": "CREATE TABLE IF NOT EXISTS main.sales.customers (\\n{columns}\\n) USING DELTA",
      "write": {
        "mode": "append"
      }
    }
  ]
}
```

### Result shape

Each output item includes:

```json
{
  "src": {
    "type": "file",
    "path": "/path/to/runtime/inbox/incoming/customers.csv",
    "size": 123
  },
  "dest": {
    "type": "table",
    "path": "main.sales.customers",
    "size": 123
  },
  "duration_sec": 0.456789
}
```

---

## Supported formats

Supported file formats for both download and upload:

* `csv`
* `parquet`

Default format:

* `parquet`

---

## Summary

### Download

* Source: Unity Catalog table or SQL query
* Destination: single file under `runtime.inbox`
* Formats: `csv`, `parquet`
* Write options: `write.options`

### Upload

* Source: file under `runtime.inbox`
* Destination: Unity Catalog table
* Formats: `csv`, `parquet`
* Read options: `read.options`
* Optional SQL transform: `select_sql` (must include `{source}` placeholder)
* Write mode: `append` or `overwrite`
* Write options: `write.options`
