"""Stage 9 - the decision engine.

This is the only place an outcome is decided, and it contains no LLM call, no
network access and no randomness. Given the same findings it returns the same
decision, every time.

The rule is simply: the outcome is the highest severity present.

    FAIL   > REJECT > REVIEW > INFO
    FAILED   REJECTED NEEDS_REVIEW APPROVED

Findings are never short-circuited upstream - every stage that can run, runs -
so a reviewer sees everything that was wrong, not merely the first thing.
"""

from __future__ import annotations

from ..domain import Decision
from .codes import OUTCOME_FOR_SEVERITY, Finding, Severity, sort_findings


def decide(findings: list[Finding]) -> Decision:
    """Aggregate findings into one outcome with ordered reason codes."""
    ordered = sort_findings(findings)

    if not ordered:
        # No stage reported anything at all. That is not an approval; it means
        # the pipeline did not actually run, and saying "approved" here would
        # be the single most dangerous bug this system could have.
        return Decision(
            outcome="FAILED",
            findings=[Finding("LLM_UNAVAILABLE", "decision",
                              {"attempts": 0, "detail": "no checks were executed"})],
        )

    severity = max(f.severity for f in ordered)
    outcome = OUTCOME_FOR_SEVERITY[severity]

    if severity == Severity.INFO:
        # Surface an explicit "everything passed" line so an approval still
        # has a stated reason rather than an empty list.
        if not any(f.code == "ALL_CHECKS_PASSED" for f in ordered):
            blocking = [f for f in ordered if f.severity > Severity.INFO]
            if not blocking:
                ordered.append(Finding("ALL_CHECKS_PASSED", "decision"))
                ordered = sort_findings(ordered)

    return Decision(outcome=outcome, findings=ordered)


def blocking_findings(findings: list[Finding]) -> list[Finding]:
    """The findings that actually drove the outcome, in order."""
    if not findings:
        return []
    severity = max(f.severity for f in findings)
    if severity == Severity.INFO:
        return []
    return [f for f in sort_findings(findings) if f.severity == severity]
