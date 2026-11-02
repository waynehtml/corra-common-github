from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal


@dataclass(frozen=True)
class VerifyResult:
    """Structured ingestion verification result."""

    status: Literal["SUCCESS", "TIMEOUT"]
    expected: List[Dict[str, Any]]
    matched: List[Dict[str, Any]]
    unmatched: List[Dict[str, Any]]

    @property
    def is_success(self) -> bool:
        return self.status == "SUCCESS"
