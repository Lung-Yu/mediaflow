#!/usr/bin/env python3
"""Apple Silicon GPU metrics exporter — exposes Prometheus /metrics on :9200.

Requires passwordless sudo for powermetrics:
  echo "$(whoami) ALL=(ALL) NOPASSWD: /usr/bin/powermetrics" | sudo tee /etc/sudoers.d/gpu-exporter
"""
import json
import logging
import subprocess
import time

from prometheus_client import Gauge, start_http_server

log = logging.getLogger(__name__)

gpu_util  = Gauge("apple_gpu_utilization_percent", "Apple GPU busy %")
ane_util  = Gauge("apple_ane_utilization_percent", "Apple Neural Engine busy %")
gpu_power = Gauge("apple_gpu_power_watts",         "Apple GPU power in watts")


def _parse_first_json(text: str) -> dict:
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                return json.loads(text[start : i + 1])
    raise ValueError("no complete JSON object found")


def _collect() -> None:
    result = subprocess.run(
        ["sudo", "powermetrics", "--samplers", "gpu_power", "-n", "1",
         "--json", "-i", "1000"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        log.warning("powermetrics error: %s", result.stderr[:200])
        return

    data = _parse_first_json(result.stdout)
    gpu  = data.get("gpu", {})

    gpu_util.set(round((1.0 - gpu.get("idle_ratio", 1.0)) * 100, 2))

    # ANE key varies by macOS version — try known locations
    ane_val = (
        data.get("ane_power")
        or data.get("processor", {}).get("ane_energy", 0)
    )
    ane_util.set(round(float(ane_val or 0), 2))

    # GPU power — also varies
    pw = (
        data.get("processor", {}).get("gpu_energy")
        or gpu.get("power", 0)
    )
    gpu_power.set(round(float(pw or 0), 3))


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
