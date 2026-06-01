from __future__ import annotations

from copy import deepcopy
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
        return value.model_dump()
    if isinstance(value, list):
        return [_dump_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump_value(item) for key, item in value.items()}
    return value


class SourceItem(BaseModel):
    title: str | None = None
    url: str | None = None
    source_type: str | None = None
    language: str | None = None
    reliability_score: float | None = None


class ToolCallItem(BaseModel):
    tool_name: str
    input: str | dict[str, Any] | None = None
    output_summary: str | None = None


class EvidenceScoreItem(BaseModel):
    url: str | None = None
    score: float | None = None
    reason: str | None = None


class ResearchTrace(BaseModel):
    engine_name: str
    query: str = ""
    research_plan: list[str] = Field(default_factory=list)
    generated_queries: list[str] = Field(default_factory=list)
    official_source_queries: list[str] = Field(default_factory=list)
    searched_sources: list[str] = Field(default_factory=list)
    sources_found: list[SourceItem] = Field(default_factory=list)
    citations: list[SourceItem] = Field(default_factory=list)
    tool_calls: list[ToolCallItem] = Field(default_factory=list)
    detected_jurisdictions: list[str] = Field(default_factory=list)
    evidence_scores: list[EvidenceScoreItem] = Field(default_factory=list)
    cross_source_consistency: list[str] = Field(default_factory=list)
    unverified_gaps: list[str] = Field(default_factory=list)
    final_answer: str = ""
    notes: list[str] = Field(default_factory=list)


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
