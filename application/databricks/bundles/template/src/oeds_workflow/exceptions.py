"""Workflow exception hierarchy for the template bundle.

The intent is to give the workflow a single base exception that pipeline code
can catch broadly, while still allowing each pipeline task to raise a more
specific failure type when needed.
"""


class WorkflowError(Exception):
    """Base exception for all template workflow failures."""


class PipelineTaskError(WorkflowError):
    """Base exception for failures tied to a specific pipeline task."""


class MainTaskError(PipelineTaskError):
    """Raised when the main orchestration task fails."""


class IngestTaskError(PipelineTaskError):
    """Raised when the ingest task fails."""


class LandToRawTaskError(PipelineTaskError):
    """Raised when the land-to-raw task fails."""


class RawToStdTaskError(PipelineTaskError):
    """Raised when the raw-to-standardized task fails."""


class StdToCuratedTaskError(PipelineTaskError):
    """Raised when the standardized-to-curated task fails."""


class OnCompleteTaskError(PipelineTaskError):
    """Raised when completion handling fails."""


class OnFailureTaskError(PipelineTaskError):
    """Raised when failure handling fails."""
