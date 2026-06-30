#!/usr/bin/env python3
"""Apple Silicon GPU metrics exporter — exposes Prometheus /metrics on :9200.

Requires passwordless sudo for powermetrics:
  echo "$(whoami) ALL=(ALL) NOPASSWD: /usr/bin/powermetrics" | sudo tee /etc/sudoers.d/gpu-exporter
  sudo chmod 440 /etc/sudoers.d/gpu-exporter
"""
import logging
import plistlib
import subprocess
import time

from prometheus_client import Gauge, start_http_server

log = logging.getLogger(__name__)

gpu_util  = Gauge("apple_gpu_utilization_percent", "Apple GPU busy %")
gpu_power = Gauge("apple_gpu_power_watts",         "Apple GPU power in watts")


def _parse_plist(raw: bytes) -> tuple:
    data = plistlib.loads(raw)
    gpu = data.get("gpu", {})
    idle = float(gpu.get("idle_ratio", 1.0))
    util = round((1.0 - idle) * 100, 2)
    energy_mj  = float(gpu.get("gpu_energy", 0) or 0)
    elapsed_ns = float(data.get("elapsed_ns", 1_000_000_000) or 1_000_000_000)
    power = round(energy_mj / (elapsed_ns / 1e9) / 1000, 3)
    return util, power


def _collect() -> None:
    result = subprocess.run(
        ["sudo", "powermetrics", "--samplers", "gpu_power",
         "-n", "1", "-i", "1000", "-f", "plist"],
        capture_output=True, timeout=15,
    )
    if result.returncode != 0:
        log.warning("powermetrics error: %s", result.stderr[:200])
        return

    util, power = _parse_plist(result.stdout)
    gpu_util.set(util)
    gpu_power.set(power)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    start_http_server(9200)
    log.info("gpu_exporter listening on :9200")
    while True:
        try:
            _collect()
        except Exception as exc:
            log.warning("collect error: %s", exc)
        time.sleep(15)
