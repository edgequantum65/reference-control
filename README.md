# reference-control

[![tests](https://github.com/edgequantum65/reference-control/actions/workflows/tests.yml/badge.svg)](https://github.com/edgequantum65/reference-control/actions/workflows/tests.yml)

**An instrument that refuses to measure until it has passed its reference cases.**

A rule used in the Edge Quantum 65 lab (Lannemezan, France) for every measuring tool:

1. Every tool has **reference cases**, inputs whose answer is known in advance.
2. At least one is a **blank control**, which must give zero (exactly, unless you state a tolerance for a float instrument).
3. They are replayed every time the tool changes. `calibrate()` records a **fingerprint** of the tool and of its checks; `measure()` recomputes it every time and refuses if anything the fingerprint covers has changed since the last passing calibration (see below).
4. A failed check is **kept** in the history, never hidden, and can be appended to a log file. A crash or a NaN counts as a failed check.
5. Until every check passes, `measure()` **refuses** to run.

Dependency-free Python 3.9+, one file of about 350 lines.

## Example: the bug that looks right

Output of `python example_peaks.py` (Python 3.13):

```
--- count_peaks
version 687bfbed053b — PASS
  [ok  ] ref   two clear peaks: expected 2.0, got 2.0
  [ok  ] blank flat signal: expected 0.0, got 0.0
  [ok  ] blank low-level noise: expected 0.0, got 0.0
measure(TWO_PEAKS) = 2.0
--- count_peaks_broken
version 8e03e0955962 — FAIL
  [ok  ] ref   two clear peaks: expected 2.0, got 2.0
  [ok  ] blank flat signal: expected 0.0, got 0.0
  [FAIL] blank low-level noise: expected 0.0, got 54.0
refused: peak counter v8e03e0955962: run calibrate() and pass every check first
--- count_peaks, THRESHOLD set to -10.0 after a passing calibration
refused: peak counter v8cb4a2fe3680: code, globals or checks changed since the last passing calibration; run calibrate() again
```

The broken counter forgets its threshold. It still finds the two real peaks, and the flat-signal blank passes too. Only the **right** blank control, low-level noise with no real peak, exposes it: 54 phantom peaks. One blank is required, but choosing what it must reject is the real work.

The last case: the correct counter passed, then the global `THRESHOLD` it reads was changed. Its source code did not change, but its fingerprint did, so it must be calibrated again before it can measure.

## Install

```bash
pip install git+https://github.com/edgequantum65/reference-control
```

Or copy `reference_control.py`: it has no dependencies.

## Use

```python
from reference_control import Instrument

inst = (Instrument("peak counter", count_peaks, log_path="calibrations.jsonl")
        .reference("two clear peaks", TWO_PEAKS, expected=2)
        .blank("low-level noise", NOISE))  # .blank(name, inputs, tolerance=1e-9) for a float instrument
print(inst.calibrate())      # report, kept in inst.history and appended to the log
value = inst.measure(signal) # raises NotCalibrated if any check failed or anything changed since
```

With `log_path`, every `calibrate()` appends one JSON line to that file, failures included: UTC timestamp, instrument name, version, fingerprint, passed, error, and each check with its expected value, result, tolerance and error. The file is only ever appended to. NaN and infinities are written as strings (`"nan"`).

`inst.fn` is read-only. Reference inputs are copied when registered and again before each run, so neither the caller nor the function can alter a stored reference case. Expected values and tolerances are stored as floats.

```bash
python -m unittest test_reference_control
python example_peaks.py
python check_readme.py   # the output above is the real output (run it with Python 3.13)
```

## What the fingerprint covers, and what it does not

The fingerprint is a **best effort against accidental changes**: an edited function, a changed setting, a re-run notebook cell, a check added after calibration. It is not a defence against deliberate cheating: anyone who edits the instrument's private attributes (`inst._calibrated`) can get around it.

`inst.version` (12 hex characters, shown in reports) is a hash of the function as it is now. `inst.fingerprint()` adds every check: name, expected value, tolerance, kind and inputs. A calibration is valid only for the fingerprint it was made with. `calibrate()` also recomputes the fingerprint after running the checks: if it moved, the calibration fails, because the checks did not all run on the same tool.

**Your code** (Python functions and classes outside the standard library and site-packages) is followed:

- the bytecode, constants, default arguments, attributes and closure variables of the function, including nested code (generator expressions, comprehensions, lambdas);
- the current value of each global name it reads, and of each attribute it reads on one of your modules (`config.THRESHOLD`), following the functions it calls;
- the classes it uses, directly or through an object: their methods and class attributes, and the object's own attributes;
- `functools.partial`, bound methods, callable objects, decorators (`functools.wraps`, class-based, `functools.lru_cache`).

**Data** it reads (numbers, strings, lists, dicts, numpy arrays...) is described by its pickle, or by its `repr` if it cannot be pickled.

**Library code** (standard library, builtins, installed packages) is identified by its name and the package's `__version__` only. Its internal state is not followed: the shared `random` generator, the caches of `re`, the loggers of `logging` can change without a refusal.

Not covered:

- the internals of library code, and code installed in site-packages that you edit in place without changing its version;
- a module stored in an object or a container rather than read as a global (only its name is seen);
- state reached only through `getattr` with a computed name, `eval`, an import done inside the function, or the objects passed to `measure()`;
- data that can neither be pickled nor shows its state in its `repr`;
- the outside world: files, environment variables, time.

Consequences to know:

- the bytecode differs between Python versions, so the version hash does too (the example above is from Python 3.13). Changing Python means calibrating again;
- a function that keeps state in what it reads (a cache in a global dict, a counter, an object attribute filled on first use such as `functools.cached_property`) changes its own fingerprint. If that happens during `calibrate()`, the calibration fails and says so; calibrating again once the state is stable works, until the state changes again. `functools.lru_cache` is fine: its cache is not part of the fingerprint;
- a set rebuilt with the same elements in a different order can pickle differently, and be refused: a false refusal, never a missed change;
- data that falls back to a `repr` containing a memory address gives a version that is not the same from one run to the next;
- data nested thousands of levels deep makes `calibrate()` raise `RecursionError`;
- `measure()` recomputes the fingerprint each time. Measured on one machine (Python 3.13): about 0.05 ms per call in the example; 35 to 70 ms when the function reads a global list, dict or numpy array of a million elements; about 0.1 s with two reference inputs of a million elements each.

---

**FR** — Un instrument qui refuse de mesurer tant qu'il n'a pas réussi ses étalons, dont au moins un témoin qui doit donner zéro (une tolérance peut être fixée pour un instrument en nombres flottants). Les échecs sont gardés, jamais cachés, et peuvent être ajoutés à un journal (une ligne JSON par étalonnage, jamais réécrite). Une empreinte de votre code, des valeurs qu'il lit et des étalons est vérifiée avant chaque mesure : si quelque chose a changé depuis le dernier étalonnage réussi, l'instrument refuse. C'est une protection contre les changements accidentels, pas contre la triche délibérée ; les bibliothèques ne sont identifiées que par leur nom et leur version, et ce que l'empreinte ne voit pas est listé ci-dessus. Dans l'exemple, le compteur cassé trouve les vrais pics et passe le témoin « signal plat » : seul le bon témoin (un bruit faible, sans aucun vrai pic) le démasque.

License: MIT.
