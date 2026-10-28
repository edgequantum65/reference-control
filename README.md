# reference-control

[![tests](https://github.com/edgequantum65/reference-control/actions/workflows/tests.yml/badge.svg)](https://github.com/edgequantum65/reference-control/actions/workflows/tests.yml)

**An instrument that refuses to measure until it has passed its reference cases.**

A rule used in the Edge Quantum 65 lab (Lannemezan, France) for every measuring tool:

1. Every tool has **reference cases**, inputs whose answer is known in advance.
2. At least one is a **blank control**, which must give zero (exactly, unless you state a tolerance for a float instrument).
3. They are replayed every time the tool changes. `calibrate()` records a **fingerprint** of the tool and of its checks; `measure()` recomputes it every time and refuses if anything changed since the last passing calibration.
4. A failed check is **kept** in the history, never hidden, and can be appended to a log file. A crash or a NaN counts as a failed check.
5. Until every check passes, `measure()` **refuses** to run.

Dependency-free Python 3.9+, one file of about 270 lines.

## Example: the bug that looks right

Output of `python example_peaks.py` (Python 3.13):

```
--- count_peaks
version 3b522a178224 — PASS
  [ok  ] ref   two clear peaks: expected 2, got 2.0
  [ok  ] blank flat signal: expected 0.0, got 0.0
  [ok  ] blank low-level noise: expected 0.0, got 0.0
measure(TWO_PEAKS) = 2.0
--- count_peaks_broken
version 85235dcec0e4 — FAIL
  [ok  ] ref   two clear peaks: expected 2, got 2.0
  [ok  ] blank flat signal: expected 0.0, got 0.0
  [FAIL] blank low-level noise: expected 0.0, got 54.0
refused: peak counter v85235dcec0e4: run calibrate() and pass every check first
--- count_peaks, THRESHOLD set to -10.0 after a passing calibration
refused: peak counter v34e62fbb9d6c: code, globals or checks changed since the last passing calibration; run calibrate() again
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

With `log_path`, every `calibrate()` appends one JSON line to that file, failures included: UTC timestamp, instrument name, version, fingerprint, passed, and each check with its expected value, result, tolerance and error. The file is only ever appended to. NaN and infinities are written as strings (`"nan"`).

`inst.fn` is read-only. Reference inputs are copied when registered and again before each run, so neither the caller nor the function can alter a stored reference case.

```bash
python -m unittest test_reference_control
python example_peaks.py
python check_readme.py   # the output above is the real output (run it with Python 3.13)
```

## What the fingerprint covers, and what it does not

`inst.version` (12 hex characters, shown in reports) is a hash of the function as it is now. `inst.fingerprint()` adds every check: name, expected value, tolerance, kind and inputs (pickled, or described if they cannot be pickled). A calibration is valid only for the fingerprint it was made with.

Covered:

- the function's bytecode, constants and names, including nested code (generator expressions, comprehensions, lambdas), its default arguments and its closure variables;
- the current value of each global or builtin name it reads, following the plain Python functions it calls, the attributes it reads on modules (`config.THRESHOLD`), and the classes it uses (their methods and class attributes);
- `functools.partial` (function, arguments, keywords), bound methods and callable objects (their class and their attributes), `functools.lru_cache` and other wrappers (the wrapped function), builtins (by name);
- other objects it reads: their pickled state, or else their `repr`.

Not covered:

- code inside C extensions and builtins: only their name is seen, not their internal state;
- state the description cannot see: an object that cannot be pickled and whose `repr` does not show its state, or state reached only through `getattr` with a computed name, `eval`, an import done inside the function, or the objects passed to `measure()`;
- the outside world: files, environment variables, time;
- deliberate cheating through private attributes (`inst._calibrated`): this guards against accidents and shortcuts, not against someone who edits the instrument's internals.

Consequences to know:

- the bytecode differs between Python versions, so the version hash does too (the example above is from Python 3.13). Changing Python means calibrating again;
- a function that changes the globals it reads (a counter, a cache in a global dict) changes its own fingerprint and is refused after its first measurement. Use `functools.lru_cache` (its cache is not part of the fingerprint) or keep that state out of its globals;
- the same holds for the shared `random` generator, whose state changes at every draw: create a seeded generator inside the function (`random.Random(seed)`) instead;
- an object described by a `repr` that contains a memory address is safe within one run, but gives a version that is not the same from one run to the next;
- `measure()` recomputes the fingerprint each time; the cost grows with the size of the reference inputs and of the globals the function reads (about 0.09 ms per call in the example, on one machine).

---

**FR** — Un instrument qui refuse de mesurer tant qu'il n'a pas réussi ses étalons, dont au moins un témoin qui doit donner zéro. Les échecs sont gardés, jamais cachés, et peuvent être ajoutés à un journal (une ligne JSON par étalonnage, jamais réécrite). Une empreinte du code, des variables globales qu'il lit et des étalons est vérifiée avant chaque mesure : si quelque chose a changé depuis le dernier étalonnage réussi, l'instrument refuse. Ce que l'empreinte ne voit pas (code C, état caché, fichiers, horloge) est listé ci-dessus. Dans l'exemple, le compteur cassé trouve les vrais pics et passe le témoin « signal plat » : seul le bon témoin (un bruit faible, sans aucun vrai pic) le démasque.

License: MIT.
