import pytest

from app.deep_research.agents.scenario_validator import build_scenario_analysis


def _output(metric, value, unit, scope, period="FY2027", currency="USD"):
    return {
        "metric_name": metric,
        "value": value,
        "unit": unit,
        "entity": "ACME",
        "scope": scope,
        "period": period,
        "period_type": "annual",
        "basis": "scenario",
        "currency": currency,
    }


def _cases(metric="EPS", unit="USD/share", scope="company"):
    return [
        {
            "name": "bull", "probability": 0.25,
            "assumptions": ["수요 상향"], "outputs": [_output(metric, "12", unit, scope)],
            "invalidation_triggers": ["수요 성장률 5% 미만"],
            "evidence_source_ids": ["https://example.com/bull"],
        },
        {
            "name": "base", "probability": 0.5,
            "assumptions": ["현재 추세 유지"], "outputs": [_output(metric, "9", unit, scope)],
            "invalidation_triggers": ["가이던스 철회"],
            "evidence_source_ids": ["https://example.com/base"],
        },
        {
            "name": "bear", "probability": 0.25,
            "assumptions": ["수요 둔화"], "outputs": [_output(metric, "5", unit, scope)],
            "invalidation_triggers": ["수요 성장률 20% 초과"],
            "evidence_source_ids": ["https://example.com/bear"],
        },
    ]


@pytest.mark.parametrize("metric,unit,scope", [
    ("EPS", "USD/share", "company"),
    ("순이자이익", "USD million", "bank"),
    ("ARR", "USD million", "subscription business"),
    ("영업이익", "USD million", "airline"),
    ("잉여현금흐름", "USD million", "energy company"),
])
def test_same_validator_accepts_multiple_industries(metric, unit, scope):
    result = build_scenario_analysis("run-a", _cases(metric, unit, scope))
    assert result.validation_status == "valid"
    assert result.executive_summary_eligible is True


def test_probability_sum_mismatch_blocks_use():
    cases = _cases()
    cases[0]["probability"] = 0.5
    result = build_scenario_analysis("run-a", cases)
    assert "probability_sum_mismatch" in result.validation_errors
    assert result.executive_summary_eligible is False


def test_output_scope_mismatch_blocks_comparison():
    cases = _cases()
    cases[0]["outputs"][0]["scope"] = "product segment"
    result = build_scenario_analysis("run-a", cases)
    assert "scenario_output_mismatch" in result.validation_errors


def test_missing_invalidation_and_evidence_are_rejected():
    cases = _cases()
    cases[2]["invalidation_triggers"] = []
    cases[2]["evidence_source_ids"] = []
    result = build_scenario_analysis("run-a", cases)
    assert "missing_invalidation_trigger:bear" in result.validation_errors
    assert "missing_evidence:bear" in result.validation_errors


def test_percent_probabilities_are_normalized():
    cases = _cases()
    cases[0]["probability"] = 25
    cases[1]["probability"] = 50
    cases[2]["probability"] = 25
    result = build_scenario_analysis("run-a", cases)
    assert result.validation_status == "valid"
    assert sum(c.probability for c in result.cases) == 1.0


def test_empty_scenarios_are_not_fabricated():
    assert build_scenario_analysis("run-a", []) is None
