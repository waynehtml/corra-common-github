"""DIS exception hierarchy used for consistent error handling."""


class DisError(Exception):
    """Base exception for DIS pipeline errors."""


class RuntimeParamError(DisError):
    """Raised when runtime job parameters are missing or invalid."""


class ConfigResolutionError(DisError):
    """Raised when ingestion config template cannot be resolved/parsed."""


class SchemaValidationError(DisError):
    """Raised when task config fails schema validation."""


class PipelineExecutionError(DisError):
    """Raised when pipeline execution fails at the notebook boundary."""


class TaskException(DisError):
    """Raised when a task handler fails during engine execution."""
