"""Example: a peak counter, a correct version and a subtly broken one.

The broken version forgets the threshold. On real signals it looks fine
(it finds the big peaks), but the blank control, low-level noise with no
real peak, catches it, and the instrument refuses to measure.
"""
import math

from reference_control import Instrument, NotCalibrated

THRESHOLD = 1.0


def count_peaks(signal):
    return sum(
        1
        for i in range(1, len(signal) - 1)
        if signal[i] > THRESHOLD and signal[i] > signal[i - 1] and signal[i] >= signal[i + 1]
    )


def count_peaks_broken(signal):
    return sum(
        1
        for i in range(1, len(signal) - 1)
        if signal[i] > signal[i - 1] and signal[i] >= signal[i + 1]  # threshold forgotten
    )


TWO_PEAKS = [0, 0.1, 3.0, 0.2, 0, 0.1, 2.5, 0.1, 0]
FLAT = [0.0] * 50
NOISE = [0.05 * math.sin(i * 1.7) for i in range(200)]  # deterministic, amplitude 0.05


def build(fn):
    return (
        Instrument("peak counter", fn)
        .reference("two clear peaks", TWO_PEAKS, expected=2)
        .blank("flat signal", FLAT)
        .blank("low-level noise", NOISE)
    )


if __name__ == "__main__":
    for fn in (count_peaks, count_peaks_broken):
        inst = build(fn)
        print(f"--- {fn.__name__}")
        print(inst.calibrate())
        try:
            print("measure(TWO_PEAKS) =", inst.measure(TWO_PEAKS))
        except NotCalibrated as exc:
            print("refused:", exc)
