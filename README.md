# reference-control

**An instrument that refuses to measure until it has passed its reference cases.**

A rule used in the Edge Quantum 65 lab (Lannemezan, France) for every measuring tool:

1. Every tool has **reference cases**, inputs whose answer is known in advance.
2. At least one is a **blank control**, which must give exactly zero.
3. They are replayed every time the tool's code changes (the version is a hash of the code).
4. A failed check is **kept** in the history, never hidden. A crash counts as a failed check.
5. Until every check passes, `measure()` **refuses** to run.

Dependency-free Python, about 100 lines.

## Example: the bug that looks right

```
--- count_peaks
version 74159f455935 — PASS
  [ok  ] ref   two clear peaks: expected 2, got 2.0
  [ok  ] blank flat signal: expected 0.0, got 0.0
  [ok  ] blank low-level noise: expected 0.0, got 0.0
measure(TWO_PEAKS) = 2.0
--- count_peaks_broken
version 3e0d3e2d3e4c — FAIL
  [ok  ] ref   two clear peaks: expected 2, got 2.0
  [ok  ] blank flat signal: expected 0.0, got 0.0
  [FAIL] blank low-level noise: expected 0.0, got 54.0
refused: peak counter v3e0d3e2d3e4c: run calibrate() and pass every check first
```

The broken counter forgets its threshold. It still finds the two real peaks, and the flat-signal blank passes too. Only the **right** blank control, low-level noise with no real peak, exposes it: 54 phantom peaks. One blank is required, but choosing what it must reject is the real work.

## Use

```python
from reference_control import Instrument

inst = (Instrument("peak counter", count_peaks)
        .reference("two clear peaks", TWO_PEAKS, expected=2)
        .blank("low-level noise", NOISE))
print(inst.calibrate())      # report, kept in inst.history
value = inst.measure(signal) # raises NotCalibrated if any check failed
```

```bash
python -m unittest test_reference_control
python example_peaks.py
```

---

**FR** — Un instrument qui refuse de mesurer tant qu'il n'a pas réussi ses étalons, dont au moins un témoin qui doit donner zéro. Les échecs sont gardés, jamais cachés. Dans l'exemple, le compteur cassé trouve les vrais pics et passe le témoin « signal plat » : seul le bon témoin (un bruit faible, sans aucun vrai pic) le démasque.

License: MIT.
