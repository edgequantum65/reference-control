"""An instrument that refuses to measure until it has passed its reference cases.

Rule: every measuring tool has cases with a known answer (reference cases),
including at least one blank control that must give zero. They are replayed
every time the tool changes: calibrate() records a fingerprint of the tool
(its code and what it reads) and of its checks, and measure() refuses as soon
as that fingerprint differs. A failed reference case is reported and kept,
never hidden. Until they all pass, the instrument refuses to measure.

The fingerprint is a best effort against accidental changes, not a defence
against deliberate cheating. README.md lists what it covers and what it does not.
"""
from __future__ import annotations

import builtins
import copy
import dis
import enum
import functools
import hashlib
import io
import json
import math
import os
import pickle
import sys
import sysconfig
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
    error: str = ""  # a failure of the calibration itself, not of one check

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results) and not self.error

    def __str__(self) -> str:
        lines = [f"version {self.version} — {'PASS' if self.passed else 'FAIL'}"]
        for r in self.results:
            kind = "blank" if r.is_blank else "ref  "
            mark = "ok  " if r.passed else "FAIL"
            lines.append(f"  [{mark}] {kind} {r.name}: expected {r.expected}, got {r.got} {r.error}".rstrip())
        if self.error:
            lines.append(f"  [FAIL] {self.error}")
        return "\n".join(lines)

    def to_json(self) -> str:
        """One line of JSON, as appended to log_path. NaN and infinities are written as strings."""
        def num(x):
            return repr(x) if isinstance(x, float) and not math.isfinite(x) else x
        results = [{**asdict(r), "expected": num(r.expected), "got": num(r.got), "tolerance": num(r.tolerance)}
                   for r in self.results]
        return json.dumps({"timestamp": self.timestamp, "instrument": self.instrument, "version": self.version,
                           "fingerprint": self.fingerprint, "passed": self.passed, "error": self.error,
                           "results": results}, ensure_ascii=False, default=repr)


# --- Fingerprint. User code (Python functions and classes outside the standard library and
# site-packages) is described by its bytecode and by what it reads. Library code is described
# by its name and version only. Data is described by its pickle, or else by its repr.

_PLAIN = (type(None), bool, int, float, complex, str, bytes)
_GLOBAL_OPS = {"LOAD_GLOBAL", "LOAD_NAME", "LOAD_FROM_DICT_OR_GLOBALS"}
_ATTR_OPS = {"LOAD_ATTR", "LOAD_METHOD", "LOAD_SUPER_ATTR"}
_CLASS_NOISE = {"__dict__", "__weakref__", "__module__", "__doc__", "__qualname__", "__firstlineno__",
                "__static_attributes__", "__dataclass_fields__", "__dataclass_params__", "_abc_impl"}
_LIB_DIRS = tuple({os.path.join(os.path.realpath(p), "") for p in
                   (sysconfig.get_paths().get(k) for k in ("stdlib", "platstdlib", "purelib", "platlib")) if p})
_library_cache: dict = {}
_code_cache: dict = {}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _library(module_name: Any) -> bool:
    """True for the standard library, builtins and installed packages (site-packages)."""
    top = str(module_name).partition(".")[0]
    if top not in _library_cache:
        module = sys.modules.get(top)
        if module is None:
            return False  # not an imported module (code made with exec, say): user code
        path = getattr(module, "__file__", None)
        if path:
            path = os.path.realpath(path)
            lib = path.startswith(_LIB_DIRS) or any(f"{os.sep}{d}{os.sep}" in path
                                                    for d in ("site-packages", "dist-packages"))
        else:
            lib = top in sys.builtin_module_names or top in getattr(sys, "stdlib_module_names", ())
        _library_cache[top] = lib
    return _library_cache[top]


def _library_name(module_name: str, name: str = "") -> str:
    version = getattr(sys.modules.get(module_name.partition(".")[0]), "__version__", "")
    return f"library {module_name}{'.' + name if name else ''} {version}"


def _const(c: Any) -> str:
    if isinstance(c, tuple):
        return "(" + ",".join(_const(x) for x in c) + ")"
    if isinstance(c, frozenset):
        return "{" + ",".join(sorted(_const(x) for x in c)) + "}"
    return repr(c)


def _code(code: types.CodeType) -> tuple:
    """Digest, global names and attribute names of a code object and of the code nested in it."""
    hit = _code_cache.get(id(code))
    if hit and hit[0] is code:
        return hit[1:]
    global_names, attr_names, consts = set(), set(), []
    for ins in dis.get_instructions(code):
        if ins.opname in _GLOBAL_OPS:
            global_names.add(ins.argval)
        elif ins.opname in _ATTR_OPS:
            attr_names.add(ins.argval)
    for c in code.co_consts:
        if isinstance(c, types.CodeType):  # generator expressions, comprehensions, lambdas, inner functions
            digest, g, a = _code(c)
            consts.append(digest)
            global_names |= g
            attr_names |= a
        else:
            consts.append(_const(c))
    digest = _sha(f"{code.co_code.hex()}|{','.join(consts)}|{code.co_names}|{code.co_varnames}|"
                  f"{code.co_freevars}|{code.co_flags}|{code.co_argcount}|{code.co_posonlyargcount}|"
                  f"{code.co_kwonlyargcount}|{getattr(code, 'co_exceptiontable', b'').hex()}")
    _code_cache[id(code)] = (code, digest, frozenset(global_names), frozenset(attr_names))
    return digest, frozenset(global_names), frozenset(attr_names)


class _HoldsUserCode(Exception):
    pass


class _Pickler(pickle.Pickler):
    def reducer_override(self, obj):  # pickle stores functions and classes by name: walk them instead
        if isinstance(obj, (types.FunctionType, type)):
            user = not _library(obj.__module__)
        elif isinstance(obj, (types.MethodType, types.ModuleType, types.CodeType)):
            user = True
        else:
            user = not _library(type(obj).__module__) or "__wrapped__" in getattr(obj, "__dict__", ())
        if user:
            raise _HoldsUserCode
        return NotImplemented


def _describe(obj: Any, seen: dict) -> str:
    """A short description of obj that changes when obj changes. seen: descriptions made so far."""
    if isinstance(obj, _PLAIN):
        return repr(obj)
    if id(obj) in seen:
        return seen[id(obj)][1]
    seen[id(obj)] = (obj, "<cycle>")  # keeps obj alive, so its id is not reused during this walk
    text = _walk(obj, seen)
    seen[id(obj)] = (obj, text)
    return text


def _walk(obj: Any, seen: dict) -> str:
    if isinstance(obj, types.FunctionType):
        return _function(obj, seen)
    if isinstance(obj, type):
        if _library(obj.__module__):
            return _library_name(obj.__module__, obj.__qualname__)
        skip = _CLASS_NOISE | ({"__new__"} if isinstance(obj, enum.EnumMeta) else set())  # set lazily by enum
        body = {k: v for k, v in vars(obj).items() if k not in skip}  # methods and class attributes
        return f"class {obj.__qualname__} " + _sha(_describe(obj.__bases__, seen) + _describe(body, seen))
    if isinstance(obj, types.ModuleType):
        return _module(obj, frozenset(), seen)
    if isinstance(obj, types.MethodType):
        return f"method({_describe(obj.__func__, seen)},{_describe(obj.__self__, seen)})"
    if isinstance(obj, (staticmethod, classmethod)):
        return f"{type(obj).__name__}({_describe(obj.__func__, seen)})"
    if isinstance(obj, property):
        return f"property({_describe((obj.fget, obj.fset, obj.fdel), seen)})"
    if isinstance(obj, functools.cached_property):
        return f"cached_property({_describe(obj.func, seen)})"
    try:
        buffer = io.BytesIO()
        _Pickler(buffer, 4).dump(obj)
        return "pickle " + hashlib.sha256(buffer.getvalue()).hexdigest()
    except Exception:
        pass  # it holds user code, or it cannot be pickled: describe it piece by piece
    if isinstance(obj, (list, tuple)):
        return f"{type(obj).__name__} " + _sha(",".join(_describe(x, seen) for x in obj))
    if isinstance(obj, (set, frozenset)):
        return f"{type(obj).__name__} " + _sha(",".join(sorted(_describe(x, seen) for x in obj)))
    if isinstance(obj, dict):
        return "dict " + _sha(",".join(f"{_describe(k, seen)}:{_describe(v, seen)}" for k, v in obj.items()))
    if isinstance(obj, functools.partial):
        return f"partial({_describe(obj.func, seen)},{_describe(obj.args, seen)},{_describe(obj.keywords, seen)})"
    if not _library(type(obj).__module__):  # an object of a user class: its class and its state
        state = dict(getattr(obj, "__dict__", {}))
        for klass in type(obj).__mro__:
            slots = getattr(klass, "__slots__", ())
            for name in [slots] if isinstance(slots, str) else slots:
                if name not in ("__dict__", "__weakref__") and hasattr(obj, name):
                    state[name] = getattr(obj, name)
        return f"object {_describe(type(obj), seen)} {_describe(state, seen)}"
    if "__wrapped__" in getattr(obj, "__dict__", ()):  # functools.lru_cache and other library wrappers
        return f"wrapper {type(obj).__qualname__}({_describe(obj.__wrapped__, seen)})"
    try:
        return "repr " + repr(obj)
    except Exception:
        return _library_name(type(obj).__module__, type(obj).__qualname__)


def _function(fn: types.FunctionType, seen: dict) -> str:
    """Code, defaults, attributes, closure cells, and the current value of each global it reads."""
    if _library(fn.__module__):
        return _library_name(fn.__module__, fn.__qualname__)
    digest, global_names, attr_names = _code(fn.__code__)
    parts = [digest, _describe(fn.__defaults__, seen), _describe(fn.__kwdefaults__, seen),
             _describe(vars(fn), seen)]
    for name, cell in zip(fn.__code__.co_freevars, fn.__closure__ or ()):
        try:
            value = cell.cell_contents
        except ValueError:  # empty cell
            value = "<empty cell>"
        parts.append(f"{name}={_describe(value, seen)}")
    builtin_ns = fn.__globals__.get("__builtins__", builtins)
    builtin_ns = vars(builtin_ns) if isinstance(builtin_ns, types.ModuleType) else builtin_ns
    for name in sorted(global_names):
        for ns in (fn.__globals__, builtin_ns):
            if name in ns:
                value = ns[name]
                is_module = isinstance(value, types.ModuleType)
                parts.append(f"{name}={_module(value, attr_names, seen) if is_module else _describe(value, seen)}")
                break
    return "function " + _sha("|".join(parts))


def _module(module: types.ModuleType, attr_names: frozenset, seen: dict) -> str:
    """A user module: the attributes the code reads on it (config.THRESHOLD), each described once."""
    if _library(module.__name__):
        return _library_name(module.__name__)
    parts = []
    for name in sorted(attr_names):
        if name in vars(module) and (id(module), name) not in seen:
            seen[(id(module), name)] = (module, "")
            value = vars(module)[name]
            is_module = isinstance(value, types.ModuleType)
            parts.append(f"{name}={_module(value, attr_names, seen) if is_module else _describe(value, seen)}")
    return f"module {module.__name__}(" + ",".join(parts) + ")"


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
        return _sha(_describe(self._fn, {}))[:12]

    def _fingerprint(self, version: str) -> str:
        checks = [(c.name, c.expected, c.tolerance, c.is_blank, c.inputs) for c in self.checks]
        return _sha(version + _describe(checks, {}))[:16]

    def fingerprint(self) -> str:
        """Hash of the version and of every check: name, expected, tolerance, kind, inputs."""
        return self._fingerprint(self.version)

    def reference(self, name: str, inputs: Any, expected: float, tolerance: float = 0.0) -> "Instrument":
        """inputs are copied: the stored case does not follow later changes to the caller's object."""
        self.checks.append(Check(name, copy.deepcopy(inputs), float(expected), float(tolerance), is_blank=False))
        return self

    def blank(self, name: str, inputs: Any, tolerance: float = 0.0) -> "Instrument":
        """A blank control: expected is always 0. A float instrument may state a small tolerance."""
        self.checks.append(Check(name, copy.deepcopy(inputs), 0.0, float(tolerance), is_blank=True))
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
                ok = bool(abs(got - c.expected) <= c.tolerance)  # False for NaN
                report.results.append(CheckResult(c.name, c.expected, got, ok, c.is_blank, "", c.tolerance))
            except Exception as exc:  # a crash is a failed check, not a hidden one
                report.results.append(CheckResult(c.name, c.expected, None, False, c.is_blank, repr(exc),
                                                  c.tolerance))
        if self._fingerprint(self.version) != report.fingerprint:
            report.error = ("the function changed what it reads while being calibrated (a cache, a counter, "
                            "a random generator): the checks did not all run on the same tool")
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
