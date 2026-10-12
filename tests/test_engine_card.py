import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from server import render_lokal_motor

CPU = {"id": "cpu", "name": "CPU", "backend": "CPU", "available": True}
GPU = {"id": "amd", "name": "RX 9070 XT", "backend": "ROCm", "available": True}


def render(**overrides):
    hardware = {"available": [CPU, GPU], "resources": None, "active": None,
                "worker_status": "verifierad_idle", "gpu_resources": None}
    hardware.update(overrides)
    return render_lokal_motor(hardware)


def gpu_row(html):
    status = re.search(r"GPU VRAM</span><span data-gpu-status>([^<]*)</span>", html).group(1)
    state = re.search(r"<div class='meter-state' data-gpu-state>([^<]*)</div>", html)
    return status, state.group(1) if state else None


class EngineCardTests(unittest.TestCase):
    def test_label_is_always_gpu_vram(self):
        for html in (render(), render(available=[CPU]),
                     render(gpu_resources={"vram_used_gb": 1.0, "vram_total_gb": 24.0})):
            self.assertIn("GPU VRAM</span>", html)

    def test_measured_vram_shows_figure_on_the_row_and_state_below(self):
        measured = {"vram_used_gb": 7.4, "vram_total_gb": 24.0}
        self.assertEqual(gpu_row(render(gpu_resources=measured, active="amd", worker_status="running")),
                         ("7.4 / 24.0 GB", "Arbetar"))
        self.assertEqual(gpu_row(render(gpu_resources=measured)), ("7.4 / 24.0 GB", "Ledig"))
        paused = dict(GPU, available=False)
        self.assertEqual(gpu_row(render(gpu_resources=measured, available=[CPU, paused])),
                         ("7.4 / 24.0 GB", "Pausad"))

    def test_without_measurement_the_row_shows_only_the_state(self):
        self.assertEqual(gpu_row(render()), ("Ledig", None))
        self.assertEqual(gpu_row(render(active="amd", worker_status="running")), ("Arbetar", None))
        self.assertEqual(gpu_row(render(available=[CPU])), ("Ingen hittad", None))


if __name__ == "__main__":
    unittest.main()
