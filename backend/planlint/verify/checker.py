"""Deterministic compliance checker — pure Python, the ONLY component that
decides compliance. No LLM performs arithmetic or renders judgment here."""

from __future__ import annotations

from planlint.config import settings
from planlint.models import (
    CheckResult,
    Constraint,
    Operator,
    PhysicalAsset,
    VerdictType,
)

_TO_INCHES: dict[str, float] = {
    "in": 1.0,
    "inch": 1.0,
    "inches": 1.0,
    "ft": 12.0,
    "feet": 12.0,
    "mm": 1.0 / 25.4,
    "cm": 1.0 / 2.54,
    "m": 1.0 / 0.0254,
}


def to_inches(value: float, unit: str) -> float | None:
    """Convert a value in `unit` to inches; None for unknown units."""
    factor = _TO_INCHES.get(unit.strip().lower())
    if factor is None:
        return None
    return value * factor


def _review(reason: str, measured: float | None = None, required: str | None = None) -> CheckResult:
    return CheckResult(
        verdict=VerdictType.NEEDS_REVIEW, measured=measured, required=required, reason=reason
    )


def check(asset: PhysicalAsset, constraint: Constraint) -> CheckResult | None:
    """Compare one asset against one constraint.

    Returns None when the constraint does not apply to this asset type
    (no edge is written at all).
    """
    if constraint.applies_to != asset.type:
        return None

    if constraint.operator in (Operator.QUALITATIVE, Operator.BOOLEAN):
        kind = constraint.operator.value
        return _review(
            f"Constraint is {kind} and cannot be auto-verdicted: {constraint.summary or 'see clause text'}"
        )

    if constraint.extraction_confidence < settings.extraction_confidence_floor:
        return _review(
            f"Constraint extraction confidence {constraint.extraction_confidence:.2f} is below "
            f"the {settings.extraction_confidence_floor:.2f} threshold"
        )

    if constraint.parameter is None or constraint.value is None:
        return _review("Constraint is missing a parameter or value")

    if constraint.unit is None:
        return _review("Constraint has no unit")

    required_in = to_inches(constraint.value, constraint.unit)
    if required_in is None:
        return _review(f"Unknown unit '{constraint.unit}'")

    measured = asset.measurements.get(constraint.parameter)
    if measured is None:
        return _review(
            f"Asset has no '{constraint.parameter.value}' measurement",
        )

    if constraint.operator == Operator.MIN:
        required_str = f">= {required_in:g} in"
        if measured >= required_in:
            return CheckResult(
                verdict=VerdictType.COMPLIES_WITH,
                measured=measured,
                required=required_str,
                reason=f"{constraint.parameter.value} is {measured:g} in; code requires {required_str}",
            )
        return CheckResult(
            verdict=VerdictType.VIOLATES,
            measured=measured,
            required=required_str,
            reason=(
                f"{constraint.parameter.value} is {measured:g} inches, "
                f"code requires {required_in:g} inches minimum"
            ),
        )

    if constraint.operator == Operator.MAX:
        required_str = f"<= {required_in:g} in"
        if measured <= required_in:
            return CheckResult(
                verdict=VerdictType.COMPLIES_WITH,
                measured=measured,
                required=required_str,
                reason=f"{constraint.parameter.value} is {measured:g} in; code requires {required_str}",
            )
        return CheckResult(
            verdict=VerdictType.VIOLATES,
            measured=measured,
            required=required_str,
            reason=(
                f"{constraint.parameter.value} is {measured:g} inches, "
                f"code allows {required_in:g} inches maximum"
            ),
        )

    if constraint.operator == Operator.RANGE:
        if constraint.value_high is None:
            return _review("Range constraint is missing its upper bound")
        high_in = to_inches(constraint.value_high, constraint.unit)
        if high_in is None:
            return _review(f"Unknown unit '{constraint.unit}'")
        required_str = f"{required_in:g}..{high_in:g} in"
        if required_in <= measured <= high_in:
            return CheckResult(
                verdict=VerdictType.COMPLIES_WITH,
                measured=measured,
                required=required_str,
                reason=f"{constraint.parameter.value} is {measured:g} in, within {required_str}",
            )
        return CheckResult(
            verdict=VerdictType.VIOLATES,
            measured=measured,
            required=required_str,
            reason=(
                f"{constraint.parameter.value} is {measured:g} inches, "
                f"code requires between {required_in:g} and {high_in:g} inches"
            ),
        )

    return _review(f"Unsupported operator '{constraint.operator.value}'")
