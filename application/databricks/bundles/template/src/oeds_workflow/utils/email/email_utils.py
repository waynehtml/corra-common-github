"""Email helpers for the template bundle workflow.

This module provides a package-backed email registry and template loader so
workflow code can send templated emails without relying on Databricks notebook
``%run`` side effects.
"""

from __future__ import annotations

import importlib
import json
import logging
from importlib.resources import files
from typing import Any, Mapping

import pandas as pd
from pyspark.sql import DataFrame

from oeds.domain.email_client import EmailClient

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PACKAGE = "oeds_workflow.config.email"
_DEFAULT_CONFIG_FILE = "default.json"


class EmailManager:
    """Resolve email definitions and send rendered template emails."""

    def __init__(
        self,
        *,
        email_service_url: str,
        email_map: Mapping[str, str] | None = None,
        config_file: str = _DEFAULT_CONFIG_FILE,
    ) -> None:
        if not email_service_url or not str(email_service_url).strip():
            raise ValueError("email_service_url is required")
        if email_map is not None and not isinstance(email_map, Mapping):
            raise ValueError("email_map must be a mapping")
        if not config_file or not str(config_file).strip():
            raise ValueError("config_file is required")

        self.email_map = dict(email_map or {})
        self.email_service_url = str(email_service_url).strip()
        self.config_file = str(config_file).strip()
        self._registry = self._discover_registry_map()

    def _discover_registry_map(self) -> dict[str, dict[str, object]]:
        importlib.import_module(_DEFAULT_CONFIG_PACKAGE)
        logger.info("Loading email config from package '%s' and file '%s'", _DEFAULT_CONFIG_PACKAGE, self.config_file)

        registry_text = files(_DEFAULT_CONFIG_PACKAGE).joinpath(self.config_file).read_text(encoding="utf-8")
        registry = json.loads(registry_text)
        if not isinstance(registry, dict):
            raise ValueError("Email registry must be a JSON object.")

        return registry

    def _load_email_template(self, template_ref: str) -> str:
        if not isinstance(template_ref, str) or not template_ref.strip():
            raise ValueError("template must be a non-empty string.")

        template_name = str(template_ref).strip()
        importlib.import_module(_DEFAULT_CONFIG_PACKAGE)
        return files(_DEFAULT_CONFIG_PACKAGE).joinpath(template_name).read_text(encoding="utf-8")

    def get_email_registry(self, name: str) -> dict[str, object]:
        if not name or not str(name).strip():
            raise ValueError("name is required")

        registry_name = str(name).strip()
        reg = self._registry.get(registry_name)
        if reg is None:
            raise ValueError(f"Email registry '{registry_name}' not found.")

        template_ref = reg.get("template")
        recipient_aliases = reg.get("recipients")

        if not isinstance(template_ref, str) or not template_ref.strip():
            raise ValueError(f"Email registry '{registry_name}' must define a non-empty template path.")
        if not isinstance(recipient_aliases, list):
            raise ValueError(f"Email registry '{registry_name}' must define recipients as a list.")

        recipients: list[str] = []
        skipped_aliases: list[str] = []
        for alias in recipient_aliases:
            recipient_email = self.email_map.get(alias)
            if recipient_email:
                recipients.append(recipient_email)
            else:
                skipped_aliases.append(str(alias))

        logger.debug("[%s] Template reference: %s", registry_name, template_ref)
        if skipped_aliases:
            logger.debug("[%s] Skipped recipient alias(es): %s", registry_name, ", ".join(skipped_aliases))
        logger.debug("[%s] Resolved recipients: %s", registry_name, recipients)
        return {
            "template": template_ref,
            "recipients": recipients,
        }

    def df_to_email_table(self, df: Any) -> str:
        html = df.to_html(index=False, border=1)
        html = html.replace(
            '<table border="1"',
            '<table border="1" style="border-collapse:collapse;border:1px solid black;"'
        ).replace(
            '<td>',
            '<td style="border:1px solid black;padding:8px 12px;">'
        ).replace(
            '<th>',
            '<th style="border:1px solid black;padding:8px 12px;">'
        )
        return html

    def send_email(
        self,
        email_reg: str,
        *,
        variables: dict[str, object] | None = None,
        attachment1_name: str | None = None,
        attachment1_content: object | None = None,
        attachment2_name: str | None = None,
        attachment2_content: object | None = None,
    ) -> None:
        variables = dict(variables or {})
        email_client = EmailClient(self.email_service_url)
        email_definition = self.get_email_registry(email_reg)
        email_template = self._load_email_template(str(email_definition["template"]))
        logger.info("Resolved email registry '%s': %s", email_reg, email_definition)

        for key, value in list(variables.items()):
            if isinstance(value, pd.DataFrame) or isinstance(value, DataFrame):
                variables[key] = self.df_to_email_table(value)
            elif value is None:
                variables[key] = ""

        template_recipients, subject, rendered_html = email_client.render_email_template(
            email_template,
            variables,
        )
        if template_recipients:
            logger.debug("Template metadata recipients are present but overridden by registry recipients")

        attachments = []
        for name, content in [
            (attachment1_name, attachment1_content),
            (attachment2_name, attachment2_content),
        ]:
            if name and content is not None:
                attachments.append(email_client.make_attachment(name, content))

        email_client.send_email(
            recipients=email_definition["recipients"],
            subject=subject,
            body=rendered_html,
            attachments=attachments,
        )