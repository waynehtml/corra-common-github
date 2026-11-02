"""Utilities for packaged timeseries resources and registration workflows."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date
import importlib
import json
import logging
from importlib.resources import files
from typing import Any

import oeds.core.time_utils as timeutils
from oeds.domain.time_series_manager import Freq, TimeSeriesCollection, TimeSeriesManager
from oeds_workflow.utils.schema.schema_utils import SchemaManager

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_FILE = "default.json"
_DEFAULT_CONFIG_PACKAGE = "oeds_workflow.config.timeseries"


class TimeSeriesUtils:
    """Manage timeseries registry, registration, publishing, and updates."""


    def __init__(
        self,
        *,
        tsm_ctx: Mapping[str, Any],
        datamart_endpoints: Any,
        auth: Any | None = None,
        schema_manager: SchemaManager,
        config_file: str = _DEFAULT_CONFIG_FILE,
    ) -> None:
        if not config_file or not str(config_file).strip():
            raise ValueError("config_file is required")
        if not isinstance(tsm_ctx, Mapping):
            raise TypeError("tsm_ctx must be a mapping")
        if not isinstance(schema_manager, SchemaManager):
            raise TypeError("schema_manager must be a SchemaManager")

        self.config_file = str(config_file).strip()
        self.tsm_ctx = dict(tsm_ctx)
        self.datamart_endpoints = datamart_endpoints
        self.schema_manager = schema_manager
        self.auth = auth
        self._tsm = TimeSeriesManager(
            metadata_catalog=self.tsm_ctx.get("metadata_catalog"),
            pipeline_name=self.tsm_ctx.get("pipeline_name"),
            notebook_path=self.tsm_ctx.get("notebook_path"),
            current_user=self.tsm_ctx.get("current_user"),
            job_id=self.tsm_ctx.get("job_id"),
            job_runid=self.tsm_ctx.get("job_runid"),
            job_trigger_type=self.tsm_ctx.get("job_trigger_type"),
        )

        logger.info(
            "Initialized TimeseriesUtils for pipeline '%s' using config package '%s' and file '%s'",
            self.tsm_ctx.get("pipeline_name"),
            _DEFAULT_CONFIG_PACKAGE,
            self.config_file,
        )

    def get_registry(self) -> dict[str, Any]:
        """Load the packaged timeseries registry definition."""
        config_package = _DEFAULT_CONFIG_PACKAGE
        importlib.import_module(config_package)
        logger.info("Loading timeseries config from package '%s'", config_package)

        registry_text = files(config_package).joinpath(self.config_file).read_text(encoding="utf-8")
        registry = json.loads(registry_text)
        if not isinstance(registry, dict):
            raise ValueError("Timeseries registry must be a JSON object.")

        providers = registry.get("providers")
        if not isinstance(providers, list) or not providers:
            raise ValueError("Timeseries registry must define providers as a non-empty list.")

        logger.debug("Loaded timeseries registry with %s provider(s)", len(providers))
        return registry

    def update_ts(
        self,
        data: Mapping[str, Any],
        full_table_name: str,
        ts_date: date | None = None,
        none_str: str = "ND",
    ) -> int:
        """Merge one batch of timeseries observations."""
        if not isinstance(data, Mapping):
            raise TypeError("data must be a mapping")

        if not full_table_name or not str(full_table_name).strip():
            raise ValueError("full_table_name is required")

        resolved_date = ts_date or timeutils.today_local()
        table_name = str(full_table_name).strip()
        collection = TimeSeriesCollection()

        for name, value in data.items():
            time_series_name = str(name).strip()
            observation = None if value is None else value
            observation_str = none_str if value is None else None

            collection.add(
                time_series_name=time_series_name,
                date=resolved_date,
                observation=observation,
                observation_str=observation_str,
            )

        logger.info(
            "Merging %s timeseries observation(s) into '%s' on '%s'",
            len(data),
            table_name,
            resolved_date,
        )
        metadata_audit_skey = self._tsm.merge_observation(
            series_collection=collection,
            table_name=table_name,
        )
        logger.info("Merged observations with metadata audit skey '%s'", metadata_audit_skey)
        return metadata_audit_skey

    def publish_to_datamart(self, metadata_audit_skey: int, timeout: int = 600) -> Any:
        """Publish one merged batch to the configured datamart endpoints."""
        if metadata_audit_skey is None:
            raise ValueError("metadata_audit_skey is required.")
        if self.auth is None:
            raise ValueError("auth is required.")

        endpoints = self.datamart_endpoints
        if endpoints is None:
            raise ValueError("datamart_endpoints is required.")

        logger.info(
            "Publishing metadata audit skey '%s' to datamart with timeout=%s",
            metadata_audit_skey,
            timeout,
        )
        logger.debug("Resolved datamart endpoints: %s", endpoints)
        return self._tsm.publish_batch_to_datamart(
            endpoints=endpoints,
            auth=self.auth,
            audit_skey=metadata_audit_skey,
            timeout=timeout,
        )

    def register(self) -> int:
        """Register the dataset and series defined in the packaged registry."""
        registry = self.get_registry()
        providers = registry["providers"]
        audit_log_skey: int | None = None

        for provider in providers:
            if not isinstance(provider, dict):
                raise TypeError("Each provider in the timeseries registry must be an object.")

            provider_name = str(provider["short_name_en"]).strip()
            datasets = provider["datasets"]
            if not isinstance(datasets, list):
                raise ValueError(f"Provider '{provider_name}' must define datasets as a list.")

            logger.info(
                "Registering %s timeseries dataset(s) for provider '%s'",
                len(datasets),
                provider_name,
            )
            audit_log_skey = self._tsm.create_audit_log()
            logger.info("Created audit log skey '%s' for provider '%s'", audit_log_skey, provider_name)

            for dataset in datasets:
                if not isinstance(dataset, dict):
                    raise TypeError("Each dataset in the timeseries registry must be an object.")

                if set(dataset.keys()) == {"name_en"}:
                    logger.info(
                        "Skipping dataset '%s' for provider '%s' because it only defines name_en",
                        dataset["name_en"],
                        provider_name,
                    )
                    continue

                dataset_payload = deepcopy(dataset)
                data_set_name_en = dataset_payload.get("name_en")
                if not data_set_name_en:
                    raise ValueError("Each dataset must define name_en.")

                series_list = dataset_payload.pop("series", [])
                if not isinstance(series_list, list):
                    raise ValueError(f"Dataset '{data_set_name_en}' must define series as a list.")

                destination_table_refs = dataset_payload.get("destination_uc_tables")
                if not isinstance(destination_table_refs, dict) or not destination_table_refs:
                    raise ValueError(
                        f"Dataset '{data_set_name_en}' must define destination_uc_tables as a non-empty dict."
                    )

                dataset_payload["data_provider_short_name_en"] = provider_name
                dataset_payload["destination_uc_tables"] = self._resolve_destination_uc_tables(destination_table_refs)
                dataset_payload["audit_log_skey"] = audit_log_skey

                logger.info(
                    "Registering dataset '%s' with %s series for provider '%s'",
                    data_set_name_en,
                    len(series_list),
                    provider_name,
                )
                self._tsm.delete_data_set(
                    data_provider_short_name_en=provider_name,
                    name_en=data_set_name_en,
                )
                registered_dataset = self._tsm.register_data_set(**dataset_payload)
                logger.debug("Registered dataset '%s': %s", data_set_name_en, registered_dataset)

                for series in series_list:
                    if not isinstance(series, dict):
                        raise TypeError(f"Dataset '{data_set_name_en}' contains a non-object series definition.")

                    series_payload = deepcopy(series)
                    provider_series_name = series_payload.get("provider_series_name")
                    if not provider_series_name:
                        raise ValueError(
                            f"Dataset '{data_set_name_en}' contains a series without provider_series_name."
                        )

                    destination_uc_table_ref = series_payload.get("destination_uc_table")
                    if not destination_uc_table_ref or not str(destination_uc_table_ref).strip():
                        raise ValueError(
                            f"Series '{provider_series_name}' in dataset '{data_set_name_en}' must define "
                            "destination_uc_table."
                        )

                    series_payload["destination_uc_table"] = self._resolve_table_name(
                        str(destination_uc_table_ref).strip()
                    )

                    frequency = series_payload.get("frequency")
                    if isinstance(frequency, str):
                        try:
                            series_payload["frequency"] = Freq(frequency.strip())
                        except ValueError as exc:
                            raise ValueError(
                                f"Series '{provider_series_name}' has invalid frequency "
                                f"'{frequency}'."
                            ) from exc

                    series_payload["data_provider_short_name_en"] = provider_name
                    series_payload["data_set_name_en"] = data_set_name_en

                    time_series_name = f"{provider_series_name}#{provider_name}".lower()
                    self._tsm.delete_time_series(time_series_name)
                    registered_series = self._tsm.register_time_series(**series_payload)
                    logger.info("Registered timeseries '%s'", time_series_name)
                    logger.debug(
                        "Registered timeseries payload '%s': %s",
                        time_series_name,
                        registered_series,
                    )

        if audit_log_skey is None:
            raise ValueError("Timeseries registry must define at least one provider.")

        return audit_log_skey

    def _resolve_destination_uc_tables(self, destination_uc_tables: Mapping[str, Any]) -> dict[str, str]:
        resolved_tables: dict[str, str] = {}
        for table_ref, alias in destination_uc_tables.items():
            if not table_ref or not str(table_ref).strip():
                raise ValueError("destination_uc_tables table ref must be a non-empty string.")
            if not alias or not str(alias).strip():
                raise ValueError("destination_uc_tables alias must be a non-empty string.")

            resolved_table_name = self._resolve_table_name(str(table_ref).strip())
            resolved_tables[resolved_table_name] = str(alias).strip()

        return resolved_tables

    def _resolve_table_name(self, table_ref: str) -> str:
        if not table_ref or not str(table_ref).strip():
            raise ValueError("table_ref is required")

        resolved_table = self.schema_manager.find(str(table_ref).strip())
        resolved_value = str(resolved_table.full_name).strip()
        logger.debug("Resolved table ref '%s' to '%s'", table_ref, resolved_value)
        return resolved_value

