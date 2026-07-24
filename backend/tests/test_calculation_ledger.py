from app.deep_research.agents.calculation_ledger import build_calculation_ledger


def _metric(
    name, value, unit, *, entity="ACME", scope="company",
    period="FY2026", period_type="annual", basis="actual",
    currency="USD", source="https://example.com/filing",
):
    return {
        "metric_name": name,
        "value": value,
        "unit": unit,
        "entity": entity,
        "scope": scope,
        "period": period,
        "period_type": period_type,
        "basis": basis,
        "currency": currency,
        "source_id": source,
    }


def _calculation(inputs, required_alignment):
    return {
        "calculation_type": "derived",
        "description": "범용 파생 계산",
        "formula": "입력 A × 입력 B",
        "formula_expression": "input_0 * input_1",
        "inputs": inputs,
        "assumptions": [],
        "required_alignment": required_alignment,
        "output": _metric("결과", "120", "USD million", basis="derived", source=None),
    }


def test_valid_calculation_is_run_scoped_and_eligible():
    raw = _calculation(
        [_metric("매출", "100", "USD million"), _metric("성장계수", "1.2", "ratio")],
        ["entity", "scope", "period", "period_type"],
    )

    first = build_calculation_ledger("run-a", [raw])[0]
    second = build_calculation_ledger("run-a", [raw])[0]

    assert first.calculation_id == second.calculation_id
    assert first.research_run_id == "run-a"
    assert first.validation_status == "valid"
    assert first.executive_summary_eligible is True


def test_scope_mismatch_is_industry_agnostic():
    cases = [
        ("server DRAM", "total DRAM"),
        ("cloud subscribers", "all subscribers"),
        ("international routes", "all routes"),
        ("upstream production", "total company"),
    ]
    for narrow, broad in cases:
        raw = _calculation(
            [
                _metric("가격 변화", "10", "%", scope=narrow),
                _metric("매출", "100", "USD million", scope=broad),
            ],
            ["entity", "scope", "period"],
        )
        result = build_calculation_ledger("run-a", [raw])[0]
        assert result.validation_status == "invalid"
        assert "scope_mismatch" in result.validation_errors
        assert result.executive_summary_eligible is False


def test_quarter_and_annual_period_type_mismatch():
    raw = _calculation(
        [
            _metric("분기 매출", "25", "USD billion", period="Q2 2026", period_type="quarter"),
            _metric("연간 비용", "40", "USD billion", period="FY2026", period_type="annual"),
        ],
        ["period_type"],
    )
    result = build_calculation_ledger("run-a", [raw])[0]
    assert "period_type_mismatch" in result.validation_errors
    assert result.validation_status == "invalid"


def test_actual_and_guidance_basis_mismatch():
    raw = _calculation(
        [
            _metric("실제 매출", "100", "USD million", basis="actual"),
            _metric("가이던스 마진", "20", "%", basis="guidance"),
        ],
        ["basis"],
    )
    result = build_calculation_ledger("run-a", [raw])[0]
    assert "basis_mismatch" in result.validation_errors


def test_missing_source_requires_review():
    item = _metric("사용자 입력값", "10", "%", source=None)
    raw = _calculation([item], [])
    result = build_calculation_ledger("run-a", [raw])[0]
    assert result.validation_status == "needs_review"
    assert "missing_input_source:0" in result.validation_errors
    assert result.executive_summary_eligible is False


def test_forecast_never_auto_promoted_even_when_valid():
    raw = _calculation([_metric("성장률 전망", "10", "%", basis="forecast")], [])
    raw["calculation_type"] = "forecast"
    result = build_calculation_ledger("run-a", [raw])[0]
    assert result.validation_status == "valid"
    assert result.executive_summary_eligible is False


def test_recomputes_industry_independent_arithmetic():
    cases = [
        ("bank", _metric("loans", "800", "USD billion"), _metric("yield", "5", "%"),
         _metric("interest", "40", "USD billion", basis="derived", source=None)),
        ("saas", _metric("customers", "2", "million"), _metric("ARPU", "500", "USD"),
         _metric("ARR", "1", "USD billion", basis="derived", source=None)),
        ("airline", _metric("passengers", "25", "million"), _metric("revenue/passenger", "400", "USD"),
         _metric("revenue", "10", "USD billion", basis="derived", source=None)),
        ("energy", _metric("volume", "100", "million"), _metric("margin", "20", "USD"),
         _metric("cash margin", "2", "USD billion", basis="derived", source=None)),
    ]
    for _, left, right, output in cases:
        raw = _calculation([left, right], [])
        raw["output"] = output
        result = build_calculation_ledger("run-generic", [raw])[0]
        assert result.validation_status == "valid"
        assert result.recomputed_value is not None
        assert result.recomputation_delta == "0"


def test_wrong_result_is_blocked_from_summary():
    raw = _calculation(
        [_metric("revenue", "100", "USD million"), _metric("growth", "20", "%")],
        [],
    )
    raw["output"] = _metric(
        "incremental revenue", "30", "USD million", basis="derived", source=None,
    )
    result = build_calculation_ledger("run-wrong", [raw])[0]
    assert result.validation_status == "invalid"
    assert "calculation_mismatch" in result.validation_errors
    assert result.executive_summary_eligible is False


def test_unsafe_or_unparseable_formula_never_executes():
    raw = _calculation([_metric("revenue", "100", "USD million")], [])
    raw["formula_expression"] = "__import__('os').system('echo unsafe')"
    result = build_calculation_ledger("run-safe", [raw])[0]
    assert "calculation_not_recomputable" in result.validation_errors
    assert result.executive_summary_eligible is False


def test_missing_machine_formula_requires_review():
    raw = _calculation([_metric("revenue", "100", "USD million")], [])
    raw["formula_expression"] = ""
    result = build_calculation_ledger("run-missing", [raw])[0]
    assert result.validation_status == "needs_review"
    assert "missing_formula_expression" in result.validation_errors
