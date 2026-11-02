"""Schema definition for vw_sample view."""

SQL = """
SELECT
    *
FROM {tbl_sample}
"""

DEFINITION = {
    "catalog": "application_cat",
    "database": "oedisint_db",
    "name": "vw_sample",
    "kind": "view",
    "sql": SQL,
}
