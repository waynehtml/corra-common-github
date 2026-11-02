# oedis/runtime.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional, Any, Dict

@dataclass(frozen=True)
class Runtime:
    # environment, loc is local
    env: Optional[str] = "loc"
    # rundate
    rundate: Optional[date] = None
    # dubug mode
    debug: Optional[bool] = None
    # flexible extra parameters
    job_params: Dict[str, Any] = field(default_factory=dict)

    # default tz
    timezone: Optional[str] = None

    # local task inbox directory (home_dir/job_run_id)
    inbox_dir: Optional[str] = None
    # volume task inbox directory (home_vol/job_run_id)
    inbox_vol: Optional[str] = None
    # Config directory
    config_dir: Optional[str] = None
    # Nexus URL
    nexus_url: Optional[str] = None

    # job metadata
    job_name: Optional[str] = None
    job_user: Optional[str] = None
    job_id: Optional[str] = None
    job_run_id: Optional[str] = None
    job_trigger_type: Optional[str] = None
    workspace_url: Optional[str] = None

    # Generated
    started_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def inbox(self) -> Optional[str]:
        """Backward-compatible alias for inbox_dir used by task handlers."""
        return self.inbox_dir

    def get_param(self, key: str, default: Any = None) -> Any:
        """Fetch a value from job_params with an optional default."""
        return self.job_params.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Useful for logging/telemetry."""
        return {
            "env": self.env,
            "rundate": self.rundate.isoformat() if self.rundate else None,
            "debug": self.debug,
            "job_params": self.job_params,
            "timezone": self.timezone,
            "inbox_dir": self.inbox_dir,
            "inbox_vol": self.inbox_vol,
            "config_dir": self.config_dir,
            "nexus_url": self.nexus_url,
            "inbox": self.inbox,
            "job_name": self.job_name,
            "job_user": self.job_user,
            "job_id": self.job_id,
            "job_run_id": self.job_run_id,
            "job_trigger_type": self.job_trigger_type,
            "workspace_url": self.workspace_url,
            "started_at_utc": self.started_at_utc.isoformat(),
        }
