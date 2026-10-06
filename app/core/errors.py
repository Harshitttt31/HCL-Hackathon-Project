"""Domain exceptions. The API maps these to structured responses; none of them may crash a request."""
from __future__ import annotations


class AssistantError(Exception):
    """Base class."""


class ValidationFailed(AssistantError):
    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or [message]


class ToolError(AssistantError):
    def __init__(self, tool: str, message: str, code: str = "tool_error"):
        super().__init__(f"{tool}: {message}")
        self.tool = tool
        self.code = code


class RecordNotFound(AssistantError):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


class ParseError(AssistantError):
    pass


class OCRUnavailable(AssistantError):
    pass


class EmbeddingError(AssistantError):
    pass


class VectorStoreError(AssistantError):
    pass


class LLMUnavailable(AssistantError):
    pass
