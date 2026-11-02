"""File loader helpers for template bundle datasets.

This module discovers file registry definitions from the sibling ``registry``
package so callers can import and use them as normal Python modules instead of
relying on Databricks notebook ``%run`` side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import logging
import pkgutil
from typing import Any, Callable

from pyspark.sql import DataFrame, SparkSession

import oeds.core.uc_utils as ucutils
from oeds_workflow.utils.schema.schema_utils import SchemaManager

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PACKAGE = "oeds_workflow.config.file"


@dataclass(frozen=True)
class FileRegistry:
    """Resolved file registry for one source dataset."""

    ref: str
    path: str
    schema: Any
    table: str
    encoding: str | None = None
    transform: Callable[[DataFrame, dict[str, Any] | None], DataFrame] | None = None


class FileLoader:
    """Discover, resolve, and load file registry entries."""

    def __init__(self, storage_path: str, schema_manager: SchemaManager) -> None:
        if not storage_path or not str(storage_path).strip():
            raise ValueError("storage_path is required")
        if not isinstance(schema_manager, SchemaManager):
            raise TypeError("schema_manager must be a SchemaManager")

        self.storage_path = str(storage_path).strip().rstrip("/")
        self.schema_manager = schema_manager
        self._file_map = self._discover_file_map()
        logger.info(
            "Loaded %s file definitions from package '%s' using storage path '%s'",
            len(self._file_map),
            self._get_config_package_name(),
            self.storage_path,
        )

    def list(self) -> list[str]:
        """Return all discovered file references."""
        return list(self._file_map.keys())

    def find(self, name: str) -> FileRegistry:
        """Return one resolved file registry by reference."""
        logger.debug("Looking up file registry '%s'", name)
        if not name or not str(name).strip():
            raise ValueError("name is required")

        file_ref = str(name).strip()
        if file_ref not in self._file_map:
            raise ValueError(f"File registry for '{file_ref}' not found")

        return self._file_map[file_ref]

    def load_file(
        self,
        file_ref: str,
        *,
        mode: str = "overwrite",
        context: dict[str, Any] | None = None,
    ) -> DataFrame:
        """Load one configured file into its target Unity Catalog table."""
        file_registry = self.find(file_ref)
        full_table_name = self.schema_manager.find(file_registry.table).full_name

        logger.info("Processing dataset '%s'", file_registry.ref)
        logger.info("Reading file from '%s'", file_registry.path)
        logger.info("Resolved target table '%s' for file ref '%s'", full_table_name, file_ref)

        spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
        df = (
            spark.read.options(mode="FAILFAST", encoding=file_registry.encoding or "windows-1252")
            .option("header", "true")
            .schema(file_registry.schema)
            .csv(file_registry.path)
        )

        df = df.toDF(*[column.lower() for column in df.columns])

        if file_registry.transform:
            logger.info("Applying custom transform for '%s'", file_registry.ref)
            df = file_registry.transform(df, context)

        logger.info("Writing dataset '%s' to table '%s'", file_registry.ref, full_table_name)
        ucutils.safe_write_to_delta(df, full_table_name, mode=mode)
        return df

    def _discover_file_map(self) -> dict[str, FileRegistry]:
        """Load all file registry entries from the registry package."""
        file_map: dict[str, FileRegistry] = {}
        package_name = self._get_config_package_name()
        file_package = importlib.import_module(package_name)

        logger.info("Loading file definitions from package '%s'", package_name)

        for module_info in sorted(pkgutil.iter_modules(file_package.__path__), key=lambda item: item.name):
            if module_info.name.startswith("_"):
                continue

            module_name = f"{package_name}.{module_info.name}"
            module = importlib.import_module(module_name)
            definition = getattr(module, "DEFINITION", None)
            if not isinstance(definition, dict):
                raise ValueError(
                    f"Unable to resolve file definition from '{module_name}'. "
                    "Expected a dict variable named 'DEFINITION'."
                )

            relative_path = self._get_required_field(definition, "path", module_name)
            file_ref = module_info.name
            file_map[file_ref] = FileRegistry(
                ref=file_ref,
                path=f"{self.storage_path}/{str(relative_path).lstrip('/')}",
                schema=self._get_required_field(definition, "schema", module_name),
                table=self._get_required_field(definition, "table", module_name),
                encoding=definition.get("encoding"),
                transform=definition.get("transform"),
            )
            logger.debug(
                "Registered file module '%s' as ref '%s' for source '%s'",
                module_info.name,
                file_ref,
                relative_path,
            )

        return file_map

    def _get_config_package_name(self) -> str:
        return _DEFAULT_CONFIG_PACKAGE

    def _get_required_field(self, definition: dict[str, Any], key: str, module_name: str) -> Any:
        if key in definition:
            return definition[key]

        raise ValueError(f"Definition '{module_name}' must provide '{key}'")

