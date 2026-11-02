"""Permission utilities for applying Unity Catalog grants from packaged config.

This module loads a packaged permission registry, resolves environment-specific
catalog and group configuration, and generates/applies Unity Catalog GRANT
statements for managed schema objects.

Key ideas:
- Permission config is stored in packaged JSON.
- Catalog names in config are base names and are normalized to
  ``{env}_{catalog}`` when loaded.
- Read permissions for a target object may include view dependencies, so users
  can successfully query the final view.
- Write permissions are only supported for tables.
"""

from __future__ import annotations

from collections.abc import Mapping
import importlib
import json
import logging
import re
from importlib.resources import files
from typing import Any

from pyspark.sql import SparkSession

from oeds_workflow.utils.schema.schema_utils import SchemaManager

logger = logging.getLogger(__name__)

# Match fully qualified object names in the form:
#   catalog.schema.object
_OBJECT_REF_PATTERN = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b"
)

_READ_PRIVILEGE = "SELECT"
_WRITE_PRIVILEGE = "MODIFY"


class PermissionManager:
    """Load packaged permission config and apply grants for one environment.

    The permission flow is:

    1. Load the packaged registry JSON.
    2. Normalize catalogs, groups, and permission entries.
    3. Resolve a configured schema ref to its fully qualified object name.
    4. Build read targets:
       - for a table: the table itself
       - for a view: all dependent objects plus the view itself
    5. Generate and optionally execute GRANT statements.
    """

    _CONFIG_FILE = "default.json"
    _CONFIG_PACKAGE = "oeds_workflow.config.permission"

    def __init__(
        self,
        env: str,
        config_file: str | None,
        schema_manager: SchemaManager | None = None,
    ) -> None:
        """Initialize the permission manager for one environment."""
        if not env or not str(env).strip():
            raise ValueError("env is required")
        if schema_manager is not None and not isinstance(schema_manager, SchemaManager):
            raise TypeError("schema_manager must be a SchemaManager")
        if config_file is not None and not str(config_file).strip():
            raise ValueError("config_file cannot be empty when provided")

        self.env = str(env).strip().lower()
        self.config_file = (
            self._CONFIG_FILE if config_file is None else str(config_file).strip()
        )
        self.schema_manager = schema_manager
        self.spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()

        logger.info(
            "Initialized PermissionManager for env '%s' using config package '%s' and file '%s'",
            self.env,
            self._CONFIG_PACKAGE,
            self.config_file,
        )

    def get_registry(self) -> dict[str, Any]:
        """Load the packaged permission registry JSON."""
        config_package = self._CONFIG_PACKAGE
        importlib.import_module(config_package)
        logger.info("Loading permission config from package '%s'", config_package)

        registry_text = files(config_package).joinpath(self.config_file).read_text(encoding="utf-8")
        registry = json.loads(registry_text)
        if not isinstance(registry, dict):
            raise ValueError("Permission registry must be a JSON object.")

        logger.debug("Loaded permission registry keys: %s", sorted(registry.keys()))
        return registry

    def _resolve_groups(self, aliases: Any, available_groups: Mapping[str, str]) -> list[str]:
        """Resolve configured group aliases to full group names."""
        if aliases in (None, []):
            return []
        if not isinstance(aliases, list):
            raise ValueError("Permission groups must be defined as a list.")

        resolved_groups: list[str] = []
        for alias in aliases:
            alias_name = str(alias).strip()
            if not alias_name:
                raise ValueError("Permission groups cannot contain an empty group alias.")
            if alias_name not in available_groups:
                raise ValueError(f"Unknown permission group '{alias_name}'.")
            resolved_groups.append(available_groups[alias_name])

        logger.debug("Resolved permission groups: aliases=%s, groups=%s", aliases, resolved_groups)
        return resolved_groups

    def _load_config(self) -> dict[str, Any]:
        """Validate and normalize the packaged config into one predictable structure.

        Normalization includes:
        - catalogs: convert base catalog names to runtime names using ``{env}_{catalog}``
        - groups: resolve aliases for the current environment
        - permissions: convert to a consistent ``ref -> {read, write}`` mapping
        """
        registry = self.get_registry()

        catalogs = registry.get("catalogs")
        if not isinstance(catalogs, list) or not catalogs:
            raise ValueError("Permission registry must define catalogs as a non-empty list.")

        normalized_catalogs: set[str] = set()
        for catalog_name in catalogs:
            if not catalog_name or not str(catalog_name).strip():
                raise ValueError("Each catalog in the permission registry must be a non-empty string.")

            catalog_value = str(catalog_name).strip()
            normalized_catalogs.add(f"{self.env}_{catalog_value}")

        groups = registry.get("groups")
        if not isinstance(groups, Mapping):
            raise ValueError("Permission registry must define groups as an object.")

        env_groups = groups.get(self.env)
        if not isinstance(env_groups, Mapping) or not env_groups:
            raise ValueError(f"Permission registry must define groups for env '{self.env}'.")

        normalized_groups: dict[str, str] = {}
        for alias, full_name in env_groups.items():
            if not alias or not str(alias).strip():
                raise ValueError("Permission group aliases must be non-empty strings.")
            if not full_name or not str(full_name).strip():
                raise ValueError(f"Permission group '{alias}' must map to a non-empty full name.")
            normalized_groups[str(alias).strip()] = str(full_name).strip()

        raw_permissions = registry.get("permissions")
        permission_entries: dict[str, Any] = {}
        if isinstance(raw_permissions, Mapping):
            permission_entries = dict(raw_permissions)
        elif isinstance(raw_permissions, list) and raw_permissions:
            for index, entry in enumerate(raw_permissions):
                if not isinstance(entry, Mapping):
                    raise ValueError(
                        f"Permission registry entry at index {index} must be an object mapping refs to grants."
                    )
                for schema_ref, permission in entry.items():
                    schema_ref_name = str(schema_ref).strip()
                    if schema_ref_name in permission_entries:
                        raise ValueError(f"Permission ref '{schema_ref_name}' is defined more than once.")
                    permission_entries[schema_ref_name] = permission
        else:
            raise ValueError(
                "Permission registry must define permissions as a non-empty object or list of objects."
            )

        normalized_permissions: dict[str, dict[str, list[str]]] = {}
        for schema_ref, permission in permission_entries.items():
            if not schema_ref or not str(schema_ref).strip():
                raise ValueError("Permission refs must be non-empty strings.")
            if not isinstance(permission, Mapping):
                raise ValueError(f"Permission config for ref '{schema_ref}' must be an object.")

            normalized_permissions[str(schema_ref).strip()] = {
                "read": self._resolve_groups(permission.get("read", []), normalized_groups),
                "write": self._resolve_groups(permission.get("write", []), normalized_groups),
            }

        logger.debug(
            "Loaded permission config summary for env '%s': %s catalog(s), %s group(s), %s permission ref(s)",
            self.env,
            len(normalized_catalogs),
            len(normalized_groups),
            len(normalized_permissions),
        )
        logger.debug("Resolved catalogs for env '%s': %s", self.env, sorted(normalized_catalogs))
        logger.debug("Resolved groups for env '%s': %s", self.env, normalized_groups)
        logger.debug("Resolved permissions for env '%s': %s", self.env, normalized_permissions)

        return {
            "catalogs": normalized_catalogs,
            "permissions": normalized_permissions,
        }

    def _resolve_full_table_name(self, schema_ref: str) -> str:
        """Resolve one permission ref to a fully qualified object name."""
        schema_ref_value = str(schema_ref).strip()
        if not schema_ref_value:
            raise ValueError("schema_ref cannot be empty.")

        if self.schema_manager is not None:
            full_table_name = self.schema_manager.find(schema_ref_value).full_name
            logger.debug(
                "Resolved schema ref '%s' via schema manager to '%s'",
                schema_ref_value,
                full_table_name,
            )
            return full_table_name

        ref_parts = schema_ref_value.split('.')
        if len(ref_parts) != 3 or not all(part.strip() for part in ref_parts):
            raise ValueError(
                "schema_ref must be a fully qualified name in the form 'catalog.schema.object' "
                "when schema_manager is not provided."
            )

        catalog, schema, obj = (part.strip() for part in ref_parts)
        full_catalog = catalog if catalog.startswith(f"{self.env}_") else f"{self.env}_{catalog}"
        full_table_name = f"{full_catalog}.{schema}.{obj}"
        logger.debug(
            "Resolved schema ref '%s' without schema manager to '%s'",
            schema_ref_value,
            full_table_name,
        )
        return full_table_name

    def _get_create_statement(self, object_name: str) -> str:
        """Load the live CREATE statement for a table or view."""
        row = self.spark.sql(f"SHOW CREATE TABLE {object_name}").first()
        if row is None or "createtab_stmt" not in row.asDict():
            raise ValueError(f"Unable to load CREATE statement for object '{object_name}'.")

        create_statement = row["createtab_stmt"]
        if not isinstance(create_statement, str) or not create_statement.strip():
            raise ValueError(f"Object '{object_name}' returned an empty CREATE statement.")

        return create_statement

    def _is_view(self, object_name: str) -> bool:
        """Return True when the object is a view."""
        table_info = self.spark.catalog.getTable(object_name)
        return table_info.tableType == "VIEW"

    def _remove_comment(self, value: str) -> str:
        """Remove full-line SQL comments that start with ``--``."""
        lines = value.split("\n")
        filtered_lines = [line for line in lines if not line.startswith("--")]
        return "\n".join(filtered_lines)

    def _remove_inline_comment(self, value: str) -> str:
        """Remove inline SQL comments starting with ``--``."""
        return re.sub(r"--.*", "", value, flags=re.DOTALL)

    def built_view_dept(
        self,
        view: str,
        *,
        allowed_catalogs: set[str] | None = None,
        visited: set[str] | None = None,
    ) -> list[str]:
        """Build the read dependency chain for a view.

        This method reads the live CREATE statement of a view, extracts fully
        qualified object references, filters them by allowed catalogs, and then
        recursively expands child views.

        The returned list ends with the current view itself.
        """
        if visited is None:
            visited = set()
        if view in visited:
            logger.debug("Skipping already visited view dependency: %s", view)
            return []

        visited.add(view)
        logger.info("Resolving dependencies for view: %s", view)

        create_statement = self._get_create_statement(view)
        create_statement = self._remove_comment(create_statement)
        create_statement = self._remove_inline_comment(create_statement)

        objs: list[str] = []
        for object_name in dict.fromkeys(_OBJECT_REF_PATTERN.findall(create_statement)):
            if object_name == view:
                continue

            dependency_catalog = object_name.split(".", 1)[0]
            if allowed_catalogs is not None and dependency_catalog not in allowed_catalogs:
                logger.debug(
                    "Skipping dependency '%s' for view '%s' because catalog '%s' is not configured",
                    object_name,
                    view,
                    dependency_catalog,
                )
                continue

            objs.append(object_name)

        logger.debug("Direct dependencies for view '%s': %s", view, objs)

        dep_objs: list[str] = []
        for obj in objs:
            if self._is_view(obj):
                dep_objs.extend(
                    self.built_view_dept(
                        obj,
                        allowed_catalogs=allowed_catalogs,
                        visited=visited,
                    )
                )
            else:
                dep_objs.append(obj)

        dep_objs.append(view)

        logger.debug("Accumulated dependencies for view '%s': %s", view, dep_objs)
        return dep_objs

    def _collect_read_targets(self, *, full_table_name: str, allowed_catalogs: set[str]) -> list[str]:
        """Collect the final ordered list of objects that should receive SELECT grants."""
        if self._is_view(full_table_name):
            dep_objs = self.built_view_dept(
                full_table_name,
                allowed_catalogs=allowed_catalogs,
            )
        else:
            dep_objs = [full_table_name]

        targets: list[str] = []
        for object_name in dep_objs:
            if object_name not in targets:
                targets.append(object_name)

        logger.debug("All read targets collected for '%s': %s", full_table_name, targets)
        return targets

    def _grant_permissions(
        self,
        *,
        privilege: str,
        object_names: list[str],
        group_names: list[str],
        statements: list[str],
        execute: bool,
    ) -> None:
        """Generate GRANT statements and optionally execute them."""
        for object_name in object_names:
            for group_name in group_names:
                sql = f"GRANT {privilege} ON {object_name} TO `{group_name}`"
                logger.info("Prepared permission SQL for '%s': %s", object_name, sql)
                statements.append(sql)
                if execute:
                    logger.debug("Executing SQL: %s", sql)
                    self.spark.sql(sql)

    def apply_permissions(self, *, schema: str | None = None, execute: bool = True) -> None:
        """Apply configured permissions.

        If ``schema`` is provided, only that configured ref is processed.
        Otherwise, all configured refs are processed.
        """
        config = self._load_config()
        statements: list[str] = []
        applied_refs: list[str] = []
        skipped_refs: list[dict[str, str]] = []

        selected_permissions = config["permissions"]
        if schema is not None:
            selected_schema = str(schema).strip()
            if not selected_schema:
                raise ValueError("schema cannot be empty.")
            if selected_schema not in config["permissions"]:
                raise ValueError(f"Permission ref '{selected_schema}' is not defined in the config.")
            selected_permissions = {selected_schema: config["permissions"][selected_schema]}

        logger.info(
            "Applying permissions for env '%s' across %s configured ref(s)",
            self.env,
            len(selected_permissions),
        )
        logger.debug("Allowed catalogs for env '%s': %s", self.env, sorted(config["catalogs"]))

        for schema_ref, permission in selected_permissions.items():
            full_table_name = self._resolve_full_table_name(schema_ref)
            object_catalog = full_table_name.split(".", 1)[0]

            logger.debug(
                "Resolved schema ref '%s' to full object name '%s'",
                schema_ref,
                full_table_name,
            )

            if object_catalog not in config["catalogs"]:
                logger.info(
                    "Skipping permission ref '%s' because catalog '%s' is not configured",
                    schema_ref,
                    object_catalog,
                )
                skipped_refs.append(
                    {
                        "ref": schema_ref,
                        "reason": f"Catalog '{object_catalog}' is not in configured catalogs.",
                    }
                )
                continue

            logger.info(
                "Applying permissions for ref '%s' to object '%s'",
                schema_ref,
                full_table_name,
            )
            logger.debug(
                "Resolved permission entry for ref '%s': read_groups=%s, write_groups=%s",
                schema_ref,
                permission["read"],
                permission["write"],
            )

            read_targets = self._collect_read_targets(
                full_table_name=full_table_name,
                allowed_catalogs=config["catalogs"],
            )
            logger.debug("Resolved read targets for ref '%s': %s", schema_ref, read_targets)

            for object_name in read_targets:
                self._grant_permissions(
                    privilege=_READ_PRIVILEGE,
                    object_names=[object_name],
                    group_names=permission["read"],
                    statements=statements,
                    execute=execute,
                )

            if permission["write"]:
                if self._is_view(full_table_name):
                    raise ValueError(
                        f"Permission ref '{schema_ref}' resolves to view '{full_table_name}', "
                        "but write permissions are only supported for tables."
                    )

                logger.debug(
                    "Applying write permissions for ref '%s' to table '%s'",
                    schema_ref,
                    full_table_name,
                )
                self._grant_permissions(
                    privilege=_WRITE_PRIVILEGE,
                    object_names=[full_table_name],
                    group_names=permission["write"],
                    statements=statements,
                    execute=execute,
                )

            applied_refs.append(schema_ref)

        logger.info(
            "Finished applying permissions for env '%s': %s applied, %s skipped, %s statement(s)",
            self.env,
            len(applied_refs),
            len(skipped_refs),
            len(statements),
        )
        logger.debug("Applied refs for env '%s': %s", self.env, applied_refs)
        logger.debug("Skipped refs for env '%s': %s", self.env, skipped_refs)
        logger.debug("Generated GRANT statements for env '%s': %s", self.env, statements)