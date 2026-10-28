import unittest

from example_peaks import NOISE, TWO_PEAKS, build, count_peaks, count_peaks_broken
from reference_control import Instrument, NotCalibrated


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


if __name__ == "__main__":
    unittest.main()
