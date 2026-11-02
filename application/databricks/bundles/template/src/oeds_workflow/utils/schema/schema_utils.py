"""Schema registry utilities for Databricks table and view definitions.

This module discovers registry modules under the sibling ``registry`` package.
Each definition module must export a top-level ``DEFINITION`` dictionary with
schema object metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import logging
import pkgutil
import re
from typing import Any

from pyspark.sql import SparkSession
from pyspark.sql.types import LongType, StructField, StructType

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PACKAGE = "oeds_workflow.config.schema"

_PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class SchemaRegistry:
    """Resolved schema registry for a single table or view."""

    catalog: str
    database: str
    name: str
    kind: str
    sql: str | None = None
    schema: StructType | None = None

    @property
    def full_name(self) -> str:
        return f"{self.catalog}.{self.database}.{self.name}"

    @property
    def is_table(self) -> bool:
        return self.kind == "table"

    @property
    def is_view(self) -> bool:
        return self.kind == "view"


class SchemaManager:
    """Discover, resolve, and manage schema registry entries for one environment."""

    def __init__(self, env: str) -> None:
        if not env or not str(env).strip():
            raise ValueError("env is required")

        self.env = env.strip().lower()
        self._schema_map = self._discover_schema_map()

        logger.info(
            "Loaded %s schema definitions for env '%s' from package '%s'",
            len(self._schema_map),
            self.env,
            self._get_config_package_name(),
        )

    def list(self) -> list[str]:
        return list(self._schema_map.keys())

    def list_tables(self, create_only: bool = False) -> list[str]:
        return [
            module_name
            for module_name, schema_registry in self._schema_map.items()
            if schema_registry.is_table and (not create_only or bool(schema_registry.sql))
        ]

    def list_views(self, create_only: bool = False) -> list[str]:
        return [
            module_name
            for module_name, schema_registry in self._schema_map.items()
            if schema_registry.is_view and (not create_only or bool(schema_registry.sql))
        ]

    def find(self, name: str) -> SchemaRegistry:
        logger.debug("Looking up schema registry '%s'", name)
        if not name or not str(name).strip():
            raise ValueError("name is required")

        module_name = str(name).strip()
        if module_name not in self._schema_map:
            raise ValueError(f"'{module_name}' is not defined")

        return self._schema_map[module_name]

    def create_view(self, schema_ref: str) -> None:
        """Create a view from a schema reference."""
        schema_registry = self.find(schema_ref)
        if not schema_registry.is_view:
            raise RuntimeError(f"Schema object '{schema_registry.name}' is not a view")

        sql_body = self.render_view_sql(schema_ref)
        resolved_sql = f"CREATE OR REPLACE VIEW {schema_registry.full_name} AS\n{sql_body.strip()}"
        logger.info("Creating view '%s' for ref '%s'", schema_registry.full_name, schema_ref)
        logger.debug("Resolved view SQL for '%s': %s", schema_registry.name, resolved_sql)
        (SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()).sql(resolved_sql)

    def render_view_sql(self, schema_ref: str) -> str:
        """Render view SQL by resolving schema object placeholders."""
        schema_registry = self.find(schema_ref)
        if not isinstance(schema_registry.sql, str) or not schema_registry.sql.strip():
            raise TypeError(f"View '{schema_registry.name}' must define non-empty SQL text in 'sql'")

        sql_body = schema_registry.sql
        placeholders = list(dict.fromkeys(_PLACEHOLDER_PATTERN.findall(sql_body)))
        for placeholder in placeholders:
            full_name = self.find(placeholder).full_name
            logger.debug(
                "Resolved placeholder '%s' to '%s' while rendering view '%s'",
                placeholder,
                full_name,
                schema_registry.name,
            )
            sql_body = sql_body.replace(f"{{{placeholder}}}", full_name)

        return sql_body

    def create_table(
        self,
        schema_ref: str,
        *,
        if_not_exists: bool = False,
        replace: bool = False,
    ) -> None:
        """Create a Unity Catalog table from a schema reference."""
        schema_registry = self.find(schema_ref)
        if not schema_registry.is_table:
            raise RuntimeError(f"Schema object '{schema_registry.name}' is not a table")

        if replace and if_not_exists:
            raise ValueError("replace and if_not_exists cannot both be True")

        create_prefix = f"CREATE TABLE {schema_registry.full_name}"
        if replace:
            create_prefix = f"CREATE OR REPLACE TABLE {schema_registry.full_name}"
        elif if_not_exists:
            create_prefix = f"CREATE TABLE IF NOT EXISTS {schema_registry.full_name}"

        sql_body = self.render_table_sql(schema_ref)
        resolved_sql = f"{create_prefix}\n{sql_body.strip()}"
        logger.info("Creating table '%s' for ref '%s'", schema_registry.full_name, schema_ref)
        logger.debug("Resolved table SQL for '%s': %s", schema_registry.name, resolved_sql)
        (SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()).sql(resolved_sql)

    def render_table_sql(self, schema_ref: str) -> str:
        """Render table SQL, including optional ``{schema}`` expansion."""
        schema_registry = self.find(schema_ref)
        if not isinstance(schema_registry.sql, str) or not schema_registry.sql.strip():
            raise TypeError(f"Table '{schema_registry.name}' must define non-empty SQL text in 'sql'")

        sql_body = schema_registry.sql
        placeholders = list(dict.fromkeys(_PLACEHOLDER_PATTERN.findall(sql_body)))
        for placeholder in placeholders:
            if placeholder != "schema":
                raise ValueError(
                    f"Table '{schema_registry.name}' uses unsupported placeholder '{placeholder}' in 'sql'"
                )

            if schema_registry.schema is None:
                raise TypeError(
                    f"Table '{schema_registry.name}' uses '{{schema}}' in 'sql' but does not define 'schema'"
                )

            schema_sql = self._render_table_columns(schema_registry)
            logger.debug(
                "Resolved placeholder '%s' while rendering table '%s'",
                placeholder,
                schema_registry.name,
            )
            sql_body = sql_body.replace(f"{{{placeholder}}}", schema_sql)

        return sql_body

    def _discover_schema_map(self) -> dict[str, SchemaRegistry]:
        schema_map: dict[str, SchemaRegistry] = {}
        package_name = self._get_config_package_name()
        registry_package = importlib.import_module(package_name)

        logger.info("Loading schema definitions from package '%s'", package_name)

        for module_info in sorted(pkgutil.iter_modules(registry_package.__path__), key=lambda item: item.name):
            if module_info.name.startswith("_"):
                continue

            module_name = f"{package_name}.{module_info.name}"
            definition = self._load_definition(module_name)

            object_name = self._get_required_field(definition, "name", module_name)
            object_kind = self._get_required_field(definition, "kind", module_name)
            catalog = self._get_required_field(definition, "catalog", module_name)
            database = self._get_required_field(definition, "database", module_name)

            schema_map[module_info.name] = SchemaRegistry(
                kind=object_kind,
                catalog=f"{self.env}_{catalog}",
                database=database,
                name=object_name,
                sql=definition.get("sql"),
                schema=definition.get("schema"),
            )

            logger.debug(
                "Registered %s module '%s' for schema '%s'",
                object_kind,
                module_info.name,
                object_name,
            )

        return schema_map

    def _get_config_package_name(self) -> str:
        return _DEFAULT_CONFIG_PACKAGE

    def _load_definition(self, module_name: str) -> dict[str, Any]:
        module = importlib.import_module(module_name)
        definition = getattr(module, "DEFINITION", None)
        if isinstance(definition, dict):
            return definition

        raise ValueError(
            f"Unable to resolve schema definition from '{module_name}'. "
            "Expected a dict variable named 'DEFINITION'."
        )

    def _get_required_field(self, definition: dict[str, Any], key: str, module_name: str) -> Any:
        if key in definition:
            return definition[key]

        raise ValueError(f"Definition '{module_name}' must provide '{key}'")

    def _render_table_columns(self, schema_registry: SchemaRegistry) -> str:
        """Render table column DDL from ``StructType`` including identity metadata."""
        if schema_registry.schema is None:
            raise TypeError(f"Table '{schema_registry.name}' must define 'schema' to render column SQL")

        identity_field = self._find_identity_field(schema_registry)
        col_defs: list[str] = []
        for field in schema_registry.schema.fields:
            if identity_field and field.name == identity_field.name:
                col_defs.append(self._render_identity_column(field, schema_registry))
                continue

            nullability = "" if field.nullable else " NOT NULL"
            base_sql = f"    `{field.name}` {field.dataType.simpleString()}{nullability}"
            col_defs.append(self._append_comment_clause(base_sql, field))

        return ",\n".join(col_defs)

    def _find_identity_field(self, schema_registry: SchemaRegistry) -> StructField | None:
        """Return the single identity field declared in schema metadata, if any."""
        if schema_registry.schema is None:
            return None

        identity_fields = [field for field in schema_registry.schema.fields if "identity" in (field.metadata or {})]
        if not identity_fields:
            return None

        if len(identity_fields) > 1:
            field_names = ", ".join(field.name for field in identity_fields)
            raise ValueError(f"Table '{schema_registry.name}' defines multiple identity columns: {field_names}")

        field = identity_fields[0]
        identity_config = field.metadata.get("identity")
        if not isinstance(identity_config, dict):
            raise TypeError(
                f"Table '{schema_registry.name}' identity metadata for column '{field.name}' must be a dict"
            )

        return field

    def _render_identity_column(self, field: StructField, schema_registry: SchemaRegistry) -> str:
        """Render one identity column from ``StructField`` metadata."""
        identity_config = field.metadata.get("identity")
        if not isinstance(field.dataType, LongType):
            raise TypeError(
                f"Table '{schema_registry.name}' identity column '{field.name}' must use LongType/BIGINT"
            )

        if field.nullable:
            raise ValueError(
                f"Table '{schema_registry.name}' identity column '{field.name}' must be non-nullable"
            )

        start = identity_config.get("start", 1)
        always = identity_config.get("always", True)

        if not isinstance(start, int):
            raise TypeError(
                f"Table '{schema_registry.name}' identity column '{field.name}' metadata 'start' must be an int"
            )

        if not isinstance(always, bool):
            raise TypeError(
                f"Table '{schema_registry.name}' identity column '{field.name}' metadata 'always' must be a bool"
            )

        mode = "ALWAYS" if always else "BY DEFAULT"
        base_sql = (
            f"    `{field.name}` BIGINT NOT NULL "
            f"GENERATED {mode} AS IDENTITY (START WITH {start} INCREMENT BY 1)"
        )
        return self._append_comment_clause(base_sql, field)


    def _append_comment_clause(self, base_sql: str, field: StructField) -> str:
        """Append a SQL comment clause when ``StructField`` metadata includes one."""
        comment = (field.metadata or {}).get("comment")
        if comment is None:
            return base_sql

        if not isinstance(comment, str):
            raise TypeError(f"Column '{field.name}' metadata 'comment' must be a string")

        escaped_comment = comment.replace("'", "''")
        return f"{base_sql} COMMENT '{escaped_comment}'"

