"""산업 독립 Bull/Base/Bear 시나리오 계약과 결정론 검증."""
from __future__ import annotations

from typing import Any

from app.deep_research.models import MetricValue, ScenarioAnalysis, ScenarioCase


_REQUIRED_CASES = {"bull", "base", "bear"}


def _metric(raw: dict[str, Any]) -> MetricValue:
    return MetricValue(
        metric_name=str(raw.get("metric_name") or "").strip(),
        value=str(raw.get("value") or "").strip(),
        unit=str(raw.get("unit") or "").strip(),
        entity=str(raw.get("entity") or "").strip(),
        scope=str(raw.get("scope") or "").strip(),
        period=str(raw.get("period") or "").strip(),
        period_type=str(raw.get("period_type") or "").strip(),
        as_of=(str(raw.get("as_of")).strip() if raw.get("as_of") else None),
        basis=str(raw.get("basis") or "").strip(),
        currency=(str(raw.get("currency")).strip() if raw.get("currency") else None),
        source_id=(str(raw.get("source_id")).strip() if raw.get("source_id") else None),
    )


def _probability(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return -1.0
    return parsed / 100.0 if parsed > 1.0 else parsed


def _output_signature(metric: MetricValue) -> tuple[str, ...]:
    return (
        metric.metric_name.strip().lower(),
        metric.unit.strip().lower(),
        metric.entity.strip().lower(),
        metric.scope.strip().lower(),
        metric.period.strip().lower(),
        metric.period_type.strip().lower(),
        (metric.currency or "").strip().lower(),
    )


def build_scenario_analysis(
    research_run_id: str,
    raw_cases: list[dict[str, Any]],
) -> ScenarioAnalysis | None:
    if not raw_cases:
        return None

    cases: list[ScenarioCase] = []
    errors: list[str] = []
    seen_names: set[str] = set()

    for index, raw in enumerate(raw_cases):
        name = str(raw.get("name") or "").strip().lower()
        probability = _probability(raw.get("probability"))
        assumptions = [str(v).strip() for v in raw.get("assumptions", []) if str(v).strip()]
        triggers = [
            str(v).strip() for v in raw.get("invalidation_triggers", []) if str(v).strip()
        ]
        evidence = [
            str(v).strip() for v in raw.get("evidence_source_ids", []) if str(v).strip()
        ]
        outputs = [_metric(v) for v in raw.get("outputs", []) if isinstance(v, dict)]

        if name not in _REQUIRED_CASES:
            errors.append(f"invalid_case_name:{index}")
        if name in seen_names:
            errors.append(f"duplicate_case:{name}")
        seen_names.add(name)
        if probability < 0 or probability > 1:
            errors.append(f"invalid_probability:{name or index}")
        if not assumptions:
            errors.append(f"missing_assumptions:{name or index}")
        if not triggers:
            errors.append(f"missing_invalidation_trigger:{name or index}")
        if not evidence:
            errors.append(f"missing_evidence:{name or index}")
        if not outputs:
            errors.append(f"missing_outputs:{name or index}")
        for output_index, output in enumerate(outputs):
            if not output.metric_name or not output.value or not output.unit:
                errors.append(f"missing_output_metadata:{name or index}:{output_index}")

        cases.append(ScenarioCase(
            name=name,
            probability=max(0.0, probability),
            assumptions=assumptions,
            outputs=outputs,
            invalidation_triggers=triggers,
            evidence_source_ids=evidence,
        ))

    missing = _REQUIRED_CASES - seen_names
    errors.extend(f"missing_case:{name}" for name in sorted(missing))

    probability_sum = sum(case.probability for case in cases)
    if abs(probability_sum - 1.0) > 0.01:
        errors.append("probability_sum_mismatch")

    # 세 시나리오는 같은 결과 지표·단위·대상·범위·기간으로 비교되어야 한다.
    signatures = [{_output_signature(m) for m in case.outputs} for case in cases]
    if signatures and any(signature != signatures[0] for signature in signatures[1:]):
        errors.append("scenario_output_mismatch")

    errors = list(dict.fromkeys(errors))
    status = "valid" if not errors else "invalid"
    return ScenarioAnalysis(
        research_run_id=research_run_id,
        cases=cases,
        validation_status=status,
        validation_errors=errors,
        executive_summary_eligible=status == "valid",
    )
