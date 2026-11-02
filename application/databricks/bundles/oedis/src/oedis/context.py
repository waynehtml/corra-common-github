from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from oedis.runtime import Runtime

@dataclass
class Context:
    runtime: Runtime
    config_resolved: Dict[str, Any]
    config_template: Dict[str, Any]
    pipeline: str | None = field(default=None, init=False)

    @property
    def inbox(self) -> Optional[str]:
        return self.runtime.inbox_dir

    @property
    def inbox_dir(self) -> Optional[str]:
        return self.runtime.inbox_dir

    @property
    def inbox_vol(self) -> Optional[str]:
        return self.runtime.inbox_vol

    # current task metadata (auto-populated by engine)
    task_id: Optional[str] = None
    task_handler: Optional[str] = None
    task_params: Dict[str, Any] = field(default_factory=dict)

    # Engine keeps each task's TaskResult keyed by task_id.
    task_results: Dict[str, Any] = field(default_factory=dict)
    pipeline_failed: bool = False
    pipeline_error: dict[str, Any] | None = None


    def set_task_result(self, key: str, value: Any) -> None:
        self.task_results[key] = value

    def get_task_result(self, key: str, default: Any = None) -> Any:
        return self.task_results.get(key, default)
