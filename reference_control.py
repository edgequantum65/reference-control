"""An instrument that refuses to measure until it has passed its reference cases.

Rule: every measuring tool has cases with a known answer (reference cases),
including at least one blank control that must give zero. They are replayed
every time the tool changes: calibrate() records a fingerprint of the tool
(its code and what it reads) and of its checks, and measure() refuses as soon
as that fingerprint differs. A failed reference case is reported and kept,
never hidden. Until they all pass, the instrument refuses to measure.
"""
from __future__ import annotations

import builtins
import copy
import functools
import hashlib
import json
import math
import pickle
import types
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


class NotCalibrated(RuntimeError):
    """Raised when measure() is called without a passing calibration of this exact tool and checks."""


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
    tolerance: float = 0.0


@dataclass
class Report:
    version: str
    results: list[CheckResult] = field(default_factory=list)
    fingerprint: str = ""
    instrument: str = ""
    timestamp: str = ""  # UTC, ISO 8601

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

    def to_json(self) -> str:
        """One line of JSON, as appended to log_path. NaN and infinities are written as strings."""
        def num(x):
            return repr(x) if isinstance(x, float) and not math.isfinite(x) else x
        results = [{**asdict(r), "expected": num(r.expected), "got": num(r.got), "tolerance": num(r.tolerance)}
                   for r in self.results]
        return json.dumps({"timestamp": self.timestamp, "instrument": self.instrument, "version": self.version,
                           "fingerprint": self.fingerprint, "passed": self.passed, "results": results},
                          ensure_ascii=False, default=repr)


# --- Fingerprint: a description of what a function does and reads, without memory addresses.

_PLAIN = (type(None), bool, int, float, complex, str, bytes)
_BUILTIN_CALLABLES = (types.BuiltinFunctionType, types.MethodWrapperType, types.WrapperDescriptorType,
                      types.MethodDescriptorType, types.ClassMethodDescriptorType)
_CLASS_NOISE = {"__dict__", "__weakref__", "__module__", "__doc__", "__qualname__", "__firstlineno__",
                "__static_attributes__", "__dataclass_fields__", "__dataclass_params__", "_abc_impl"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _qualname(obj: Any) -> str:
    return f"{getattr(obj, '__module__', None)}.{getattr(obj, '__qualname__', getattr(obj, '__name__', '?'))}"


def _state(obj: Any) -> str:
    """Any other object: its pickled state, else its repr, else its type."""
    try:
        return "pickle " + hashlib.sha256(pickle.dumps(obj, 4)).hexdigest()
    except Exception:
        pass
    try:
        return "repr " + repr(obj)
    except Exception:
        return "type " + _qualname(type(obj))


def _names(code: types.CodeType) -> set:
    """Names read by a code object and by the code nested in it (generator expressions, lambdas...)."""
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _names(const)
    return names


def _stable(obj: Any, seen: dict) -> str:
    """A description of obj that changes when obj changes. seen: objects already described (cycles)."""
    if isinstance(obj, _PLAIN):
        return repr(obj)
    if id(obj) in seen:
        return "<seen>"
    seen[id(obj)] = obj  # keeps it alive, so its id cannot be reused during this walk
    if isinstance(obj, (list, tuple)):
        return f"{type(obj).__name__}({','.join(_stable(x, seen) for x in obj)})"
    if isinstance(obj, (set, frozenset)):
        return f"{type(obj).__name__}({','.join(sorted(_stable(x, seen) for x in obj))})"
    if isinstance(obj, dict):
        return "dict(" + ",".join(f"{_stable(k, seen)}:{_stable(v, seen)}" for k, v in obj.items()) + ")"
    if isinstance(obj, types.CodeType):
        c = obj
        consts = ",".join(_stable(x, seen) for x in c.co_consts)  # nested code objects included
        return "code " + _sha(f"{c.co_code.hex()}|{consts}|{c.co_names}|{c.co_varnames}|{c.co_freevars}|"
                              f"{c.co_flags}|{c.co_argcount}|{c.co_posonlyargcount}|{c.co_kwonlyargcount}|"
                              f"{getattr(c, 'co_exceptiontable', b'').hex()}")
    if isinstance(obj, types.ModuleType):
        return f"module {obj.__name__}"
    if isinstance(obj, type):
        if obj.__module__ == "builtins":
            return f"class {obj.__qualname__}"
        body = {k: v for k, v in vars(obj).items() if k not in _CLASS_NOISE}  # methods, class attributes
        return f"class {_qualname(obj)}{_stable(obj.__bases__, seen)}{_stable(body, seen)}"
    if isinstance(obj, (staticmethod, classmethod)):
        return f"{type(obj).__name__}({_stable(obj.__func__, seen)})"
    if isinstance(obj, property):
        return f"property({_stable((obj.fget, obj.fset, obj.fdel), seen)})"
    if callable(obj):
        return _callable(obj, seen)
    return _state(obj)


def _callable(fn: Any, seen: dict) -> str:
    if isinstance(fn, functools.partial):
        return f"partial({_stable(fn.func, seen)},{_stable(fn.args, seen)},{_stable(fn.keywords, seen)})"
    if isinstance(fn, types.MethodType):
        return f"method({_stable(fn.__func__, seen)},{_stable(fn.__self__, seen)})"
    if isinstance(fn, types.FunctionType):
        return _function(fn, seen)
    if hasattr(fn, "__wrapped__"):  # functools.lru_cache and other C wrappers
        return f"wrapped {_qualname(type(fn))}({_stable(fn.__wrapped__, seen)})"
    if isinstance(fn, _BUILTIN_CALLABLES):  # C code: only its name is visible, plus what it is bound to
        owner = getattr(fn, "__self__", None)
        bound = "" if owner is None or isinstance(owner, types.ModuleType) else _stable(owner, seen)
        return f"builtin {_qualname(fn)}{bound}"
    state = _stable(vars(fn), seen) if hasattr(fn, "__dict__") else _state(fn)
    return f"instance {_stable(type(fn), seen)}{state}"  # a callable object: its class (__call__) and state


def _function(fn: types.FunctionType, seen: dict) -> str:
    """Code, defaults, closure cells, and the current value of every global or builtin it reads."""
    code = fn.__code__
    parts = [_stable(code, seen), _stable(fn.__defaults__, seen), _stable(fn.__kwdefaults__, seen)]
    for name, cell in zip(code.co_freevars, fn.__closure__ or ()):
        try:
            value = cell.cell_contents
        except ValueError:  # empty cell
            value = "<empty cell>"
        parts.append(f"{name}={_stable(value, seen)}")
    names = _names(code)  # unlike inspect.getclosurevars, this includes names read by nested code
    builtin_ns = fn.__globals__.get("__builtins__", builtins)
    builtin_ns = vars(builtin_ns) if isinstance(builtin_ns, types.ModuleType) else builtin_ns
    for name in sorted(names):
        for ns in (fn.__globals__, builtin_ns):
            if name in ns:
                parts.append(f"{name}={_module(ns[name], names, seen)}")
                break
    return "function " + _sha("|".join(parts))


def _module(value: Any, names: set, seen: dict) -> str:
    """For a module, also describe the attributes the code may read on it (config.THRESHOLD)."""
    if not isinstance(value, types.ModuleType):
        return _stable(value, seen)
    if ("module", id(value)) in seen:
        return f"module {value.__name__}"
    seen[("module", id(value))] = value
    attrs = vars(value)
    return f"module {value.__name__}(" + ",".join(
        f"{n}={_module(attrs[n], names, seen)}" for n in sorted(names) if n in attrs) + ")"


def _inputs(inputs: Any) -> str:
    try:
        return "pickle " + hashlib.sha256(pickle.dumps(inputs, 4)).hexdigest()
    except Exception:
        return _stable(inputs, {})


class Instrument:
    def __init__(self, name: str, fn: Callable[[Any], float], log_path: Any = None):
        self.name = name
        self._fn = fn
        self.log_path = log_path  # if set, every report is appended to it as one JSON line
        self.checks: list[Check] = []
        self.history: list[Report] = []  # every report is kept, failures included
        self._calibrated: str | None = None  # fingerprint of the last passing calibration

    @property
    def fn(self) -> Callable[[Any], float]:
        return self._fn  # read-only; replacing _fn anyway is caught by the fingerprint

    @property
    def version(self) -> str:
        """Short hash of the function as it is now: code, defaults, closures, globals it reads."""
        return _sha(_stable(self._fn, {}))[:12]

    def _fingerprint(self, version: str) -> str:
        checks = [(c.name, c.expected, c.tolerance, c.is_blank, _inputs(c.inputs)) for c in self.checks]
        return _sha(version + _stable(checks, {}))[:16]

    def fingerprint(self) -> str:
        """Hash of the version and of every check: name, expected, tolerance, kind, inputs."""
        return self._fingerprint(self.version)

    def reference(self, name: str, inputs: Any, expected: float, tolerance: float = 0.0) -> "Instrument":
        """inputs are copied: the stored case does not follow later changes to the caller's object."""
        self.checks.append(Check(name, copy.deepcopy(inputs), expected, tolerance, is_blank=False))
        return self

    def blank(self, name: str, inputs: Any, tolerance: float = 0.0) -> "Instrument":
        """A blank control: expected is always 0. A float instrument may state a small tolerance."""
        self.checks.append(Check(name, copy.deepcopy(inputs), 0.0, tolerance, is_blank=True))
        return self

    def calibrate(self) -> Report:
        if not any(c.is_blank for c in self.checks):
            raise ValueError("at least one blank control (expected 0) is required")
        self._calibrated = None
        version = self.version
        report = Report(version, fingerprint=self._fingerprint(version), instrument=self.name,
                        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        for c in self.checks:
            try:
                got = float(self._fn(copy.deepcopy(c.inputs)))  # a copy: fn cannot corrupt the reference case
                ok = abs(got - c.expected) <= c.tolerance  # False for NaN
                report.results.append(CheckResult(c.name, c.expected, got, ok, c.is_blank, "", c.tolerance))
            except Exception as exc:  # a crash is a failed check, not a hidden one
                report.results.append(CheckResult(c.name, c.expected, None, False, c.is_blank, repr(exc),
                                                  c.tolerance))
        self.history.append(report)
        if self.log_path is not None:
            with open(self.log_path, "a", encoding="utf-8") as log:  # append-only, never rewritten
                log.write(report.to_json() + "\n")
        self._calibrated = report.fingerprint if report.passed else None
        return report

    def measure(self, inputs: Any) -> float:
        version = self.version
        if self._calibrated is None:
            raise NotCalibrated(f"{self.name} v{version}: run calibrate() and pass every check first")
        if self._fingerprint(version) != self._calibrated:
            raise NotCalibrated(f"{self.name} v{version}: code, globals or checks changed since the last "
                                "passing calibration; run calibrate() again")
        return float(self._fn(inputs))
