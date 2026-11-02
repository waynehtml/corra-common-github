from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict

from jinja2 import Environment, StrictUndefined


DEFAULT_TEMPLATE_MARKERS: Dict[str, Dict[str, str]] = {
    "variable": {"start": "{{", "end": "}}"},
    "block": {"start": "{%", "end": "%}"},
    "comment": {"start": "{#", "end": "#}"},
}

TASK_TEMPLATE_MARKERS: Dict[str, Dict[str, str]] = {
    "variable": {"start": "<<", "end": ">>"},
    "block": {"start": "<%", "end": "%>"},
    "comment": {"start": "<#", "end": "#>"},
}


class TemplateResolver:
    def __init__(self, template_markers: Dict[str, Dict[str, str]], render_context: Dict[str, Any]):
        """Create a Jinja template resolver with custom markers and render context."""
        if not isinstance(template_markers, dict):
            raise TypeError("template_markers must be a dict")
        if not isinstance(render_context, dict):
            raise TypeError("render_context must be a dict")

        self._template_markers = {
            "variable": template_markers.get("variable") or DEFAULT_TEMPLATE_MARKERS["variable"],
            "block": template_markers.get("block") or DEFAULT_TEMPLATE_MARKERS["block"],
            "comment": template_markers.get("comment") or DEFAULT_TEMPLATE_MARKERS["comment"],
        }
        self._render_context = render_context

        self._env = Environment(
            undefined=StrictUndefined,
            variable_start_string=self._template_markers["variable"]["start"],
            variable_end_string=self._template_markers["variable"]["end"],
            block_start_string=self._template_markers["block"]["start"],
            block_end_string=self._template_markers["block"]["end"],
            comment_start_string=self._template_markers["comment"]["start"],
            comment_end_string=self._template_markers["comment"]["end"],
            autoescape=False,
        )

    @staticmethod
    def _json_default(value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)
        raise TypeError(f"Not JSON serializable: {type(value)}")

    def render(self, template: Any) -> Any:
        """Render a string template or JSON-like structure with the configured context."""
        if isinstance(template, str):
            return self._env.from_string(template).render(**self._render_context)

        template_json = json.dumps(template, ensure_ascii=False, default=self._json_default)
        rendered_json = self._env.from_string(template_json).render(**self._render_context)
        return json.loads(rendered_json)


def resolve_config(template: Any, *, render_context: Dict[str, Any]) -> Any:
    """Resolve templates with default Jinja markers."""
    return TemplateResolver(
        template_markers=DEFAULT_TEMPLATE_MARKERS,
        render_context=render_context,
    ).render(template)


def resolve_task(template: Any, *, render_context: Dict[str, Any]) -> Any:
    """Resolve templates with task-level markers (`<< >>`, `<% %>`, `<# #>`)."""
    return TemplateResolver(
        template_markers=TASK_TEMPLATE_MARKERS,
        render_context=render_context,
    ).render(template)
