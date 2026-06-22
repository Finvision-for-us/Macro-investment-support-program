from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from pydantic import BaseModel, Field
except ImportError:
    class _FieldInfo:
        def __init__(self, default: Any = None, default_factory: Any = None) -> None:
            self.default = default
            self.default_factory = default_factory

    def Field(default: Any = None, default_factory: Any = None) -> _FieldInfo:
        return _FieldInfo(default=default, default_factory=default_factory)

    class BaseModel:
        def __init__(self, **kwargs: Any) -> None:
            annotations: dict[str, Any] = {}
            for cls in reversed(self.__class__.mro()):
                annotations.update(getattr(cls, "__annotations__", {}))

            for name in annotations:
                if name in kwargs:
                    value = kwargs.pop(name)
                elif hasattr(self.__class__, name):
                    default = getattr(self.__class__, name)
                    if isinstance(default, _FieldInfo):
                        value = default.default_factory() if default.default_factory else deepcopy(default.default)
                    else:
                        value = deepcopy(default)
                else:
                    raise TypeError(f"Missing required field: {name}")
                setattr(self, name, value)

            for name, value in kwargs.items():
                setattr(self, name, value)

        def model_dump(self) -> dict[str, Any]:
            return {
                name: _dump_value(getattr(self, name))
                for name in getattr(self.__class__, "__annotations__", {})
            }

        def dict(self) -> dict[str, Any]:
            return self.model_dump()


def _dump_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _dump_value(value.model_dump())
    if isinstance(value, list):
        return [_dump_value(item) for item in value]
    if isinstance(value, tuple):
        return [_dump_value(item) for item in value]
    if isinstance(value, dict):
        return {str(_dump_value(key)): _dump_value(item) for key, item in value.items()}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


class CitationItem(BaseModel):
    title: str | None = None
    url: str | None = None
    source_type: str | None = None
    language: str | None = None
    snippet: str | None = None


class TraceEventItem(BaseModel):
    name: str
    input: str | dict[str, Any] | None = None
    output_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str


class GeminiRunRecord(BaseModel):
    run_id: str
    query: str
    instruction_path: str | None = None
    prompt_path: str | None = None
    mode: str = "grounded"
    model: str
    agent: str | None = None
    interaction_id: str | None = None
    poll_count: int | None = None
    polling_interval_seconds: float | None = None
    timeout_seconds: int | None = None
    started_at: str
    ended_at: str | None = None
    status: str
    request_metadata: dict[str, Any] = Field(default_factory=dict)
    events: list[TraceEventItem] = Field(default_factory=list)
    citations: list[CitationItem] = Field(default_factory=list)
    final_answer: str = ""
    raw_response_path: str | None = None
    summary_path: str | None = None
    html_log_viewer_path: str | None = None
    notes: list[str] = Field(default_factory=list)


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return _dump_value(model.model_dump())
    return _dump_value(model.dict())
