from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskResult:
    task_id: str
    handler: str
    status: str = "success"
    error: Optional[str] = None
    outputs: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    extras: Optional[Dict[str, Any]] = None
