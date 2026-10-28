"""An instrument that refuses to measure until it has passed its reference cases.

Rule: every measuring tool has cases with a known answer (reference cases),
including at least one blank control that must give zero. They are replayed
every time the tool's code changes. A failed reference case is reported and
kept, never hidden. Until they all pass, the instrument refuses to measure.
"""
from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


class NotCalibrated(RuntimeError):
    """Raised when measure() is called before a passing calibration of this exact code."""


@dataclass
class Check:
    name: str
    inputs: Any
    expected: float
    tolerance: float
    is_blank: bool


@dataclass
class CheckResult:
    name: str
    expected: float
    got: float | None
    passed: bool
    is_blank: bool
    error: str = ""


@dataclass
class Report:
    version: str
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)

    def __str__(self) -> str:
        lines = [f"version {self.version} — {'PASS' if self.passed else 'FAIL'}"]
        for r in self.results:
            kind = "blank" if r.is_blank else "ref  "
            mark = "ok  " if r.passed else "FAIL"
            lines.append(f"  [{mark}] {kind} {r.name}: expected {r.expected}, got {r.got} {r.error}".rstrip())
        return "\n".join(lines)


class Instrument:
    def __init__(self, name: str, fn: Callable[[Any], float]):
        self.name = name
        self.fn = fn
        self.version = hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:12]
        self.checks: list[Check] = []
        self.history: list[Report] = []  # every report is kept, failures included
        self._calibrated_version: str | None = None

    def reference(self, name: str, inputs: Any, expected: float, tolerance: float = 0.0) -> "Instrument":
        self.checks.append(Check(name, inputs, expected, tolerance, is_blank=False))
        return self

    def blank(self, name: str, inputs: Any) -> "Instrument":
        self.checks.append(Check(name, inputs, 0.0, 0.0, is_blank=True))
        return self

    def calibrate(self) -> Report:
        if not any(c.is_blank for c in self.checks):
            raise ValueError("at least one blank control (expected 0) is required")
        report = Report(self.version)
        for c in self.checks:
            try:
                got = float(self.fn(c.inputs))
                ok = abs(got - c.expected) <= c.tolerance
                report.results.append(CheckResult(c.name, c.expected, got, ok, c.is_blank))
            except Exception as exc:  # a crash is a failed check, not a hidden one
                report.results.append(CheckResult(c.name, c.expected, None, False, c.is_blank, repr(exc)))
        self.history.append(report)
        self._calibrated_version = self.version if report.passed else None
        return report

    def measure(self, inputs: Any) -> float:
        if self._calibrated_version != self.version:
            raise NotCalibrated(f"{self.name} v{self.version}: run calibrate() and pass every check first")
        return float(self.fn(inputs))
