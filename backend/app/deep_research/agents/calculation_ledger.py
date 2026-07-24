"""산업 독립적인 금융 계산 의미 검증기."""
from __future__ import annotations

import ast
import hashlib
import re
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from app.deep_research.models import CalculationRecord, MetricValue


_ALLOWED_TYPES = {
    "derived",
    "mechanical_sensitivity",
    "forecast",
    "scenario",
}
_DIMENSIONS = {
    "entity",
    "scope",
    "period",
    "period_type",
    "as_of",
    "basis",
    "currency",
}
_SCALE_WORDS = {
    "thousand": Decimal("1e3"),
    "million": Decimal("1e6"),
    "billion": Decimal("1e9"),
    "trillion": Decimal("1e12"),
    "천": Decimal("1e3"),
    "백만": Decimal("1e6"),
    "십억": Decimal("1e9"),
    "조": Decimal("1e12"),
}
_ALLOWED_BINARY = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}


def _parse_decimal(value: str) -> Decimal:
    text = value.strip().replace(",", "").replace("$", "").replace("₩", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").strip()
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*(%|[KkMmBbTt])?", text)
    if not match:
        raise InvalidOperation
    number = Decimal(match.group(1))
    suffix = (match.group(2) or "").lower()
    if suffix == "%":
        number /= Decimal("100")
    elif suffix:
        number *= {"k": Decimal("1e3"), "m": Decimal("1e6"),
                   "b": Decimal("1e9"), "t": Decimal("1e12")}[suffix]
    return -number if negative else number


def _unit_scale(unit: str) -> Decimal:
    normalized = unit.lower()
    for word, scale in _SCALE_WORDS.items():
        if word in normalized:
            return scale
    return Decimal("1")


def _metric_number(metric: MetricValue) -> Decimal:
    number = _parse_decimal(metric.value)
    # 값 자체의 K/M/B/T 접미사와 단위 배율을 이중 적용하지 않는다.
    if not re.search(r"[KkMmBbTt]\s*$", metric.value.strip()):
        number *= _unit_scale(metric.unit)
    if "%" in metric.unit and "%" not in metric.value:
        number /= Decimal("100")
    return number


def _evaluate_expression(expression: str, values: list[Decimal]) -> Decimal:
    tree = ast.parse(expression, mode="eval")

    def visit(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name) and re.fullmatch(r"input_\d+", node.id):
            index = int(node.id.split("_")[1])
            if index >= len(values):
                raise ValueError("unknown_input")
            return values[index]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY:
            return _ALLOWED_BINARY[type(node.op)](visit(node.left), visit(node.right))
        raise ValueError("unsafe_formula")

    with localcontext() as context:
        context.prec = 28
        return visit(tree)


def _recompute(
    calculation_type: str,
    expression: str,
    inputs: list[MetricValue],
    output: MetricValue | None,
) -> tuple[Decimal | None, Decimal | None, list[str]]:
    if calculation_type not in {"derived", "mechanical_sensitivity"}:
        return None, None, []
    if not expression:
        return None, None, ["missing_formula_expression"]
    if output is None:
        return None, None, []
    try:
        values = [_metric_number(item) for item in inputs]
        recomputed = _evaluate_expression(expression, values)
        reported = _metric_number(output)
    except (InvalidOperation, ValueError, ZeroDivisionError, SyntaxError, IndexError):
        return None, None, ["calculation_not_recomputable"]

    delta = recomputed - reported
    tolerance = max(abs(reported) * Decimal("0.001"), Decimal("0.000001"))
    errors = ["calculation_mismatch"] if abs(delta) > tolerance else []
    return recomputed, delta, errors


def _calc_id(run_id: str, index: int, description: str, formula: str) -> str:
    raw = f"{run_id}:{index}:{description}:{formula}"
    return "calc_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


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


def _validate(
    calculation_type: str,
    formula: str,
    inputs: list[MetricValue],
    output: MetricValue | None,
    required_alignment: list[str],
) -> list[str]:
    errors: list[str] = []
    if calculation_type not in _ALLOWED_TYPES:
        errors.append("invalid_calculation_type")
    if not formula:
        errors.append("missing_formula")
    if not inputs:
        errors.append("missing_inputs")
    if output is None:
        errors.append("missing_output")

    for idx, item in enumerate(inputs):
        if not item.metric_name or not item.value or not item.unit:
            errors.append(f"missing_input_metadata:{idx}")
        if not item.source_id:
            errors.append(f"missing_input_source:{idx}")

    unknown = [d for d in required_alignment if d not in _DIMENSIONS]
    errors.extend(f"unknown_alignment_dimension:{d}" for d in unknown)

    # 계산별로 반드시 같아야 한다고 선언된 의미 차원만 비교한다.
    # 산업명·지표명을 하드코딩하지 않는다.
    for dimension in required_alignment:
        if dimension not in _DIMENSIONS:
            continue
        values = []
        missing = False
        for item in inputs:
            value = getattr(item, dimension)
            normalized = str(value or "").strip().lower()
            if not normalized:
                missing = True
            else:
                values.append(normalized)
        if missing:
            errors.append(f"missing_alignment:{dimension}")
        if len(set(values)) > 1:
            errors.append(f"{dimension}_mismatch")

    return list(dict.fromkeys(errors))


def build_calculation_ledger(
    research_run_id: str,
    calculations: list[dict[str, Any]],
) -> list[CalculationRecord]:
    ledger: list[CalculationRecord] = []
    for index, raw in enumerate(calculations):
        description = str(raw.get("description") or "").strip()
        formula = str(raw.get("formula") or "").strip()
        formula_expression = str(raw.get("formula_expression") or "").strip()
        calculation_type = str(raw.get("calculation_type") or "").strip().lower()
        inputs = [_metric(item) for item in (raw.get("inputs") or []) if isinstance(item, dict)]
        output_raw = raw.get("output")
        output = _metric(output_raw) if isinstance(output_raw, dict) else None
        required_alignment = list(dict.fromkeys(
            str(d).strip() for d in (raw.get("required_alignment") or []) if str(d).strip()
        ))
        errors = _validate(
            calculation_type, formula, inputs, output, required_alignment,
        )
        recomputed, delta, recompute_errors = _recompute(
            calculation_type, formula_expression, inputs, output,
        )
        errors.extend(recompute_errors)
        errors = list(dict.fromkeys(errors))
        status = "valid" if not errors else (
            "invalid" if any("mismatch" in e or e.startswith("invalid_") for e in errors)
            else "needs_review"
        )
        ledger.append(CalculationRecord(
            calculation_id=_calc_id(research_run_id, index, description, formula),
            research_run_id=research_run_id,
            calculation_type=calculation_type or "derived",
            description=description,
            formula=formula,
            inputs=inputs,
            assumptions=[str(a) for a in (raw.get("assumptions") or [])],
            required_alignment=required_alignment,
            output=output,
            validation_status=status,
            validation_errors=errors,
            recomputed_value=(str(recomputed.normalize()) if recomputed is not None else None),
            recomputation_delta=(
                "0" if delta == 0 else str(delta.normalize())
                if delta is not None else None
            ),
            executive_summary_eligible=(
                status == "valid"
                and calculation_type in {"derived", "mechanical_sensitivity"}
            ),
        ))
    return ledger
