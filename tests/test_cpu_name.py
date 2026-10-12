import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, '/ai')
import worker


def name_from(model_name):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'cpuinfo'
        path.write_text(f'processor\t: 0\nmodel name\t: {model_name}\ncpu MHz\t: 3900.0\n', encoding='utf-8')
        return worker.local_cpu_name(str(path))


class LocalCpuNameTests(unittest.TestCase):
    def test_intel_trademark_marks_are_removed(self):
        self.assertEqual(name_from('Intel(R) Core(TM) Ultra 7 265K'), 'Intel Core Ultra 7 265K')

    def test_amd_core_count_suffix_is_removed(self):
        self.assertEqual(name_from('AMD Ryzen 7 9800X3D 8-Core Processor'), 'AMD Ryzen 7 9800X3D')

    def test_xeon_clock_speed_and_cpu_word_are_removed(self):
        self.assertEqual(name_from('Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz'), 'Intel Xeon E5-2680 v4')

    def test_missing_file_or_model_line_never_invents_a_name(self):
        self.assertEqual(worker.local_cpu_name('/nonexistent/cpuinfo'), 'CPU')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cpuinfo'
            path.write_text('processor\t: 0\n', encoding='utf-8')
            self.assertEqual(worker.local_cpu_name(str(path)), 'CPU')

    def test_local_cpu_job_names_the_real_machine_not_a_fixed_cpu(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertNotIn('Ryzen 7 9800X3D', source)


if __name__ == '__main__':
    unittest.main()
