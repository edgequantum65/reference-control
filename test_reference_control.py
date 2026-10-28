import functools
import json
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

import example_peaks
from example_peaks import FLAT, NOISE, TWO_PEAKS, build, count_peaks, count_peaks_broken
from reference_control import Instrument, NotCalibrated


def _helper(x):
    return x


def sum_via_helper(xs):
    return sum(_helper(x) for x in xs)


def peaks_via_module(signal):
    return example_peaks.count_peaks(signal)  # reads another module's function, which reads THRESHOLD


def scaled_sum(xs, k):
    return k * sum(xs)


class ReferenceControlTests(unittest.TestCase):
    def test_refuses_before_calibration(self):
        with self.assertRaises(NotCalibrated):
            build(count_peaks).measure(TWO_PEAKS)

    def test_correct_instrument_passes_and_measures(self):
        inst = build(count_peaks)
        self.assertTrue(inst.calibrate().passed)
        self.assertEqual(inst.measure(TWO_PEAKS), 2)

    def test_blank_control_catches_broken_instrument(self):
        inst = build(count_peaks_broken)
        report = inst.calibrate()
        self.assertFalse(report.passed)
        failed = [r.name for r in report.results if not r.passed]
        self.assertEqual(failed, ["low-level noise"])  # the real peaks still look right
        with self.assertRaises(NotCalibrated):
            inst.measure(TWO_PEAKS)

    def test_failed_reports_are_kept(self):
        inst = build(count_peaks_broken)
        inst.calibrate()
        inst.calibrate()
        self.assertEqual(len(inst.history), 2)
        self.assertTrue(all(not r.passed for r in inst.history))

    def test_blank_control_is_mandatory(self):
        inst = Instrument("no blank", count_peaks).reference("two peaks", TWO_PEAKS, 2)
        with self.assertRaises(ValueError):
            inst.calibrate()

    def test_crash_is_a_failed_check(self):
        inst = Instrument("crashes", lambda x: 1 / 0).blank("anything", NOISE)
        report = inst.calibrate()
        self.assertFalse(report.passed)
        self.assertIn("ZeroDivisionError", report.results[0].error)

    def test_nan_is_a_failed_check(self):
        inst = (Instrument("nan", lambda x: float("nan"))
                .reference("anything", TWO_PEAKS, 2, tolerance=math.inf).blank("flat", FLAT, tolerance=math.inf))
        report = inst.calibrate()
        self.assertEqual([r.passed for r in report.results], [False, False])
        with self.assertRaises(NotCalibrated):
            inst.measure(TWO_PEAKS)

    def test_blank_tolerance_for_float_instruments(self):
        tiny = lambda xs: 1e-12 + 0 * sum(xs)  # noqa: E731
        self.assertFalse(Instrument("strict", tiny).blank("flat", FLAT).calibrate().passed)
        inst = Instrument("tolerant", tiny).blank("flat", FLAT, tolerance=1e-9)
        self.assertTrue(inst.calibrate().passed)
        self.assertEqual(inst.checks[0].expected, 0.0)

    def test_many_measures_stay_allowed(self):  # the bytecode specialises with use, the fingerprint must not move
        inst = build(count_peaks)
        inst.calibrate()
        for _ in range(200):
            self.assertEqual(inst.measure(TWO_PEAKS), 2)

    def test_version_is_stable_and_distinguishes_code(self):
        self.assertEqual(build(count_peaks).version, build(count_peaks).version)
        self.assertNotEqual(build(count_peaks).version, build(count_peaks_broken).version)
        self.assertRegex(build(count_peaks).version, r"^[0-9a-f]{12}$")

    # --- the five bypasses of the earlier version, each must now be blocked

    def test_bypass1_fn_cannot_be_swapped_after_calibration(self):
        inst = build(count_peaks)
        inst.calibrate()
        with self.assertRaises(AttributeError):
            inst.fn = count_peaks_broken  # read-only
        inst._fn = count_peaks_broken  # forced anyway: the fingerprint catches it
        with self.assertRaises(NotCalibrated):
            inst.measure(NOISE)

    def test_bypass1_code_replaced_in_place(self):
        inst = build(count_peaks)
        inst.calibrate()
        original = count_peaks.__code__
        try:
            count_peaks.__code__ = count_peaks_broken.__code__
            with self.assertRaises(NotCalibrated):
                inst.measure(NOISE)
        finally:
            count_peaks.__code__ = original
        self.assertEqual(inst.measure(TWO_PEAKS), 2)  # same code and globals again: allowed again

    def test_bypass2_global_dependency_changed_after_calibration(self):
        inst = build(count_peaks)
        inst.calibrate()
        before = inst.version
        with mock.patch.object(example_peaks, "THRESHOLD", -10.0):
            self.assertNotEqual(inst.version, before)
            with self.assertRaises(NotCalibrated):
                inst.measure(NOISE)
        self.assertEqual(inst.measure(TWO_PEAKS), 2)

    def test_bypass2_dependency_through_a_module_attribute(self):
        inst = Instrument("via module", peaks_via_module).reference("two", TWO_PEAKS, 2).blank("noise", NOISE)
        self.assertTrue(inst.calibrate().passed)
        with mock.patch.object(example_peaks, "THRESHOLD", -10.0):
            with self.assertRaises(NotCalibrated):
                inst.measure(NOISE)

    def test_bypass2_called_helper_function_replaced(self):
        inst = Instrument("helper", sum_via_helper).reference("one two", [1, 2], 3).blank("zeros", [0, 0])
        self.assertTrue(inst.calibrate().passed)
        with mock.patch.object(sys.modules[__name__], "_helper", lambda x: x + 1):
            with self.assertRaises(NotCalibrated):
                inst.measure([5])
        self.assertEqual(inst.measure([5]), 5)

    def test_bypass3_checks_changed_after_calibration(self):
        inst = build(count_peaks)
        inst.calibrate()
        inst.blank("one more blank", [0.0, 0.5, 0.0])
        with self.assertRaises(NotCalibrated):
            inst.measure(TWO_PEAKS)
        self.assertTrue(inst.calibrate().passed)
        inst.checks[1].tolerance = 100.0  # editing a stored check
        with self.assertRaises(NotCalibrated):
            inst.measure(TWO_PEAKS)
        inst.calibrate()
        inst.checks[2].inputs[0] = 5.0  # editing a reference input
        with self.assertRaises(NotCalibrated):
            inst.measure(TWO_PEAKS)

    def test_bypass4_any_callable_can_be_an_instrument(self):
        ns = {}
        exec("def typed_in(xs):\n    return sum(xs)\n", ns)  # no source file, like a REPL or stdin
        cases = {
            "partial": functools.partial(scaled_sum, k=1),
            "builtin": math.fsum,
            "no source": ns["typed_in"],
            "bound method": [].count,
        }
        for name, fn in cases.items():
            with self.subTest(name):
                inst = Instrument(name, fn)
                if name == "bound method":
                    inst.reference("absent", 1, 0).blank("absent too", 2)
                else:
                    inst.reference("ten tenths", [0.1] * 10, 1.0, tolerance=1e-9).blank("zeros", [0.0] * 3)
                self.assertTrue(inst.calibrate().passed, name)
                self.assertRegex(inst.version, r"^[0-9a-f]{12}$")

    def test_bypass4_partial_arguments_are_fingerprinted(self):
        inst = Instrument("partial", functools.partial(scaled_sum, k=1)).reference("1+2", [1, 2], 3).blank("0", [0])
        self.assertTrue(inst.calibrate().passed)
        inst.fn.keywords["k"] = 2
        with self.assertRaises(NotCalibrated):
            inst.measure([1, 2])

    def test_bypass4_callable_object_and_bound_method_state(self):
        class Counter:
            def __init__(self, threshold):
                self.threshold = threshold

            def __call__(self, signal):
                return sum(1 for x in signal if x > self.threshold)

            def count(self, signal):
                return self(signal)

        for fn_of in (lambda c: c, lambda c: c.count):
            counter = Counter(1.0)
            inst = Instrument("object", fn_of(counter)).reference("two", TWO_PEAKS, 2).blank("noise", NOISE)
            self.assertTrue(inst.calibrate().passed)
            self.assertEqual(inst.measure(TWO_PEAKS), 2)
            counter.threshold = -10.0
            with self.assertRaises(NotCalibrated):
                inst.measure(TWO_PEAKS)

    def test_bypass5_fn_cannot_corrupt_the_reference_cases(self):
        def drain(xs):  # counts values above 1, emptying its input as it goes
            n = 0
            while xs:
                n += xs.pop() > 1.0
            return n

        noise = list(NOISE)
        inst = Instrument("drain", drain).reference("two", [3.0, 0.5, 2.0], 2).blank("noise", noise)
        self.assertTrue(inst.calibrate().passed)
        self.assertTrue(inst.calibrate().passed)
        self.assertEqual(noise, NOISE)
        self.assertEqual(len(inst.checks[1].inputs), 200)
        self.assertEqual(inst.measure([5.0]), 1)

    # --- persistent history

    def test_log_path_appends_one_json_line_per_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "calibrations.jsonl")
            broken = build(count_peaks_broken)
            broken.log_path = path
            broken.calibrate()
            with open(path, encoding="utf-8") as f:
                first = f.read()
            crash = Instrument("crashes", lambda x: 1 / 0, log_path=path).blank("anything", NOISE)
            crash.calibrate()
            good = Instrument("peak counter", count_peaks, log_path=path)
            good.reference("two clear peaks", TWO_PEAKS, expected=2).blank("low-level noise", NOISE)
            good.calibrate()
            with open(path, encoding="utf-8") as f:
                content = f.read()
        self.assertTrue(content.startswith(first))  # appended, never rewritten
        lines = [json.loads(line) for line in content.splitlines()]
        self.assertEqual([r["passed"] for r in lines], [False, False, True])
        self.assertEqual(lines[0]["version"], broken.history[0].version)
        self.assertEqual(lines[0]["results"][2]["got"], 54.0)
        self.assertIn("ZeroDivisionError", lines[1]["results"][0]["error"])
        self.assertEqual(lines[2]["fingerprint"], good.fingerprint())
        for line in lines:
            self.assertTrue(line["timestamp"].endswith("+00:00"))
            self.assertEqual(set(line), {"timestamp", "instrument", "version", "fingerprint", "passed", "results"})

    def test_nan_is_logged_as_a_string(self):
        report = Instrument("nan", lambda x: float("nan")).blank("flat", FLAT).calibrate()
        self.assertEqual(json.loads(report.to_json())["results"][0]["got"], "nan")


if __name__ == "__main__":
    unittest.main()
