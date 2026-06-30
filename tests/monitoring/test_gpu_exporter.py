import sys, os, plistlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../monitoring'))

from gpu_exporter import _parse_plist


def _plist(gpu_idle: float, gpu_energy: float = 0.0, elapsed_ns: int = 1_000_000_000) -> bytes:
    return plistlib.dumps({
        "gpu": {"idle_ratio": gpu_idle, "gpu_energy": gpu_energy},
        "elapsed_ns": elapsed_ns,
    })


def test_util_from_idle_ratio():
    util, _ = _parse_plist(_plist(gpu_idle=0.9))
    assert util == 10.0


def test_power_calculation():
    # 1000 mJ over 1s = 1.0 W
    _, power = _parse_plist(_plist(gpu_idle=1.0, gpu_energy=1000.0, elapsed_ns=1_000_000_000))
    assert power == 1.0


def test_full_utilization():
    util, _ = _parse_plist(_plist(gpu_idle=0.0))
    assert util == 100.0
