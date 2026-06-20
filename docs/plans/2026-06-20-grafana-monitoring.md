# Grafana Monitoring & Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a full Grafana + Prometheus + OTel observability stack for mediaflow — system resources, Apple Silicon GPU, pipeline business metrics, external service health, and dual-channel alerting.

**Architecture:** OpenTelemetry SDK in `pipeline/` and `api/` pushes metrics via OTLP gRPC to an `otel-collector` container, which exposes a Prometheus endpoint. Prometheus also scrapes `node_exporter`, `gpu_exporter`, `cadvisor`, `redis_exporter`, `postgres_exporter`, `blackbox_exporter`, and MinIO's built-in endpoint. Grafana queries Prometheus and fires alerts to Webhook + Email.

**Tech Stack:**
- `opentelemetry-sdk>=1.25.0`, `opentelemetry-exporter-otlp-proto-grpc>=1.25.0`
- `opentelemetry-instrumentation-fastapi>=0.46b0`
- `prometheus_client>=0.21` (gpu_exporter only)
- Docker images: `otel/opentelemetry-collector-contrib`, `prom/prometheus`, `grafana/grafana`, `gcr.io/cadvisor/cadvisor`, `oliver006/redis_exporter`, `prometheuscommunity/postgres-exporter`, `prom/blackbox-exporter`

## Global Constraints

- Mac mini Apple Silicon (arm64) — all Docker images must have arm64 variants (all listed above do)
- `host.docker.internal` is the Docker Desktop hostname for the Mac host — used to reach node_exporter, gpu_exporter, Whisper, Ollama, Diarize from within containers
- Grafana on port 3001 (3000 is taken by `web`)
- Prometheus retention: 15 days
- No push to remote unless user confirms
- OTel metric names use dots (`mediaflow.jobs.submitted`); Prometheus stores with underscores (`mediaflow_jobs_submitted_total`)
- Commit after each task

---

## File Map

```
monitoring/                           ← new directory
  otel-collector-config.yml          ← new
  prometheus.yml                     ← new
  blackbox.yml                       ← new
  gpu_exporter.py                    ← new
  grafana/
    provisioning/
      datasources/prometheus.yml     ← new
      dashboards/dashboards.yml      ← new
      alerting/contactpoints.yml     ← new
      alerting/rules.yml             ← new
    dashboards/
      overview.json                  ← new
      pipeline.json                  ← new

docker-compose.yml                   ← modified (7 new services + 2 new volumes + MinIO env)
pipeline/telemetry.py                ← new
pipeline/runner.py                   ← modified (stage timing + last_stage_ts)
pipeline/watcher.py                  ← modified (job counters + active_jobs gauge)
api/main.py                          ← modified (FastAPI OTel instrumentation)
requirements.txt                     ← modified (3 new packages)
docs/monitoring.excalidraw           ← new
```

---

## Task 1: Monitoring Infrastructure — Config Files + docker-compose

**Files:**
- Create: `monitoring/otel-collector-config.yml`
- Create: `monitoring/prometheus.yml`
- Create: `monitoring/blackbox.yml`
- Create: `monitoring/grafana/provisioning/datasources/prometheus.yml`
- Create: `monitoring/grafana/provisioning/dashboards/dashboards.yml`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: All monitoring containers reachable; Prometheus scrape targets visible at `http://localhost:9090/targets`

- [ ] **Step 1: Create `monitoring/otel-collector-config.yml`**

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 10s

exporters:
  prometheus:
    endpoint: "0.0.0.0:8889"

service:
  pipelines:
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheus]
```

- [ ] **Step 2: Create `monitoring/prometheus.yml`**

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: otel-collector
    static_configs:
      - targets: ['otel-collector:8889']

  - job_name: node
    static_configs:
      - targets: ['host.docker.internal:9100']

  - job_name: gpu
    static_configs:
      - targets: ['host.docker.internal:9200']

  - job_name: cadvisor
    static_configs:
      - targets: ['cadvisor:8080']

  - job_name: redis
    static_configs:
      - targets: ['redis_exporter:9121']

  - job_name: postgres
    static_configs:
      - targets: ['postgres_exporter:9187']

  - job_name: blackbox
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets:
          - http://host.docker.internal:9001/health
          - http://host.docker.internal:11434/api/tags
          - http://host.docker.internal:9003/health
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox_exporter:9115

  - job_name: minio
    metrics_path: /minio/v2/metrics/cluster
    static_configs:
      - targets: ['minio:9000']
```

- [ ] **Step 3: Create `monitoring/blackbox.yml`**

```yaml
modules:
  http_2xx:
    prober: http
    timeout: 5s
    http:
      valid_http_versions: ["HTTP/1.1", "HTTP/2.0"]
      valid_status_codes: []
      method: GET
      preferred_ip_protocol: ip4
```

- [ ] **Step 4: Create Grafana provisioning files**

`monitoring/grafana/provisioning/datasources/prometheus.yml`:
```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    uid: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

`monitoring/grafana/provisioning/dashboards/dashboards.yml`:
```yaml
apiVersion: 1
providers:
  - name: MediaFlow
    orgId: 1
    folder: MediaFlow
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
```

- [ ] **Step 5: Add 7 services + 2 volumes + MinIO env to `docker-compose.yml`**

Add to the `minio` service's `environment` block:
```yaml
      MINIO_PROMETHEUS_AUTH_TYPE: public
```

Append after the `web` service (before the `volumes:` section):
```yaml
  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    restart: unless-stopped
    volumes:
      - ./monitoring/otel-collector-config.yml:/etc/otelcol-contrib/config.yaml
    ports:
      - "4317:4317"
      - "4318:4318"
      - "8889:8889"

  prometheus:
    image: prom/prometheus:latest
    restart: unless-stopped
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus
    ports:
      - "9090:9090"
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=15d
      - --web.enable-lifecycle

  grafana:
    image: grafana/grafana:latest
    restart: unless-stopped
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD:-admin}
      - GF_SERVER_HTTP_PORT=3001
      - GF_SMTP_ENABLED=${GRAFANA_SMTP_ENABLED:-false}
      - GF_SMTP_HOST=${GRAFANA_SMTP_HOST:-}
      - GF_SMTP_USER=${GRAFANA_SMTP_USER:-}
      - GF_SMTP_PASSWORD=${GRAFANA_SMTP_PASSWORD:-}
      - GF_SMTP_FROM_ADDRESS=${GRAFANA_SMTP_FROM:-alerts@mediaflow.local}
      - GF_UNIFIED_ALERTING_ENABLED=true
    volumes:
      - ./monitoring/grafana/provisioning:/etc/grafana/provisioning
      - ./monitoring/grafana/dashboards:/var/lib/grafana/dashboards
      - grafana-data:/var/lib/grafana
    ports:
      - "3001:3001"
    depends_on:
      - prometheus

  cadvisor:
    image: gcr.io/cadvisor/cadvisor:latest
    restart: unless-stopped
    privileged: true
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    ports:
      - "8081:8080"
    command:
      - --docker_only=true
      - --store_container_labels=false

  redis_exporter:
    image: oliver006/redis_exporter:latest
    restart: unless-stopped
    environment:
      REDIS_ADDR: redis://redis:6379
    ports:
      - "9121:9121"
    depends_on:
      - redis

  postgres_exporter:
    image: prometheuscommunity/postgres-exporter:latest
    restart: unless-stopped
    environment:
      DATA_SOURCE_NAME: postgresql://mediaflow:${POSTGRES_PASSWORD:-changeme}@postgres:5432/mediaflow?sslmode=disable
    ports:
      - "9187:9187"
    depends_on:
      postgres:
        condition: service_healthy

  blackbox_exporter:
    image: prom/blackbox-exporter:latest
    restart: unless-stopped
    volumes:
      - ./monitoring/blackbox.yml:/etc/blackbox_exporter/config.yml
    ports:
      - "9115:9115"
```

Append to the `volumes:` section:
```yaml
  prometheus-data:
  grafana-data:
```

- [ ] **Step 6: Start services and verify**

```bash
docker compose up -d otel-collector prometheus grafana cadvisor redis_exporter postgres_exporter blackbox_exporter
```

Expected: all 7 containers start without error. Then open `http://localhost:9090/targets` — you should see all scrape jobs listed. Most will show `DOWN` at this point (node_exporter and gpu_exporter not yet installed; pipeline not yet instrumented) — that is expected.

Verify Grafana loads: `curl -s http://localhost:3001/api/health` → `{"database":"ok"}`

Verify Prometheus API: `curl -s http://localhost:9090/-/healthy` → `Prometheus Server is Healthy.`

- [ ] **Step 7: Commit**

```bash
git add monitoring/ docker-compose.yml
git commit -m "feat(monitoring): add Prometheus + Grafana + OTel Collector + exporters to docker-compose"
```

---

## Task 2: Host Exporters — node_exporter + gpu_exporter.py

**Files:**
- Create: `monitoring/gpu_exporter.py`

**Interfaces:**
- Produces: `http://localhost:9100/metrics` (system), `http://localhost:9200/metrics` (GPU)

- [ ] **Step 1: Install node_exporter**

```bash
brew install node_exporter
brew services start node_exporter
```

Verify: `curl -s http://localhost:9100/metrics | grep node_cpu`
Expected output contains lines like `node_cpu_seconds_total{...}`.

- [ ] **Step 2: Write `monitoring/gpu_exporter.py`**

```python
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
```

- [ ] **Step 3: Configure passwordless sudo for powermetrics**

```bash
echo "$(whoami) ALL=(ALL) NOPASSWD: /usr/bin/powermetrics" | sudo tee /etc/sudoers.d/gpu-exporter
sudo chmod 0440 /etc/sudoers.d/gpu-exporter
```

Verify: `sudo powermetrics --samplers gpu_power -n 1 --json -i 500` — should output JSON without password prompt.

- [ ] **Step 4: Write test for `_parse_first_json`**

Create `tests/monitoring/test_gpu_exporter.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../monitoring'))

from gpu_exporter import _parse_first_json


def test_parses_single_object():
    text = '{"gpu": {"idle_ratio": 0.9}, "processor": {"gpu_energy": 2.5}}'
    data = _parse_first_json(text)
    assert data["gpu"]["idle_ratio"] == 0.9
    assert data["processor"]["gpu_energy"] == 2.5


def test_parses_first_of_multiple_objects():
    text = '{"gpu": {"idle_ratio": 0.8}}\n{"gpu": {"idle_ratio": 0.7}}'
    data = _parse_first_json(text)
    assert data["gpu"]["idle_ratio"] == 0.8


def test_raises_on_no_json():
    import pytest
    with pytest.raises(ValueError):
        _parse_first_json("no json here")
```

- [ ] **Step 5: Run tests**

```bash
source venv/bin/activate
pytest tests/monitoring/test_gpu_exporter.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Start gpu_exporter and verify**

```bash
source venv/bin/activate
python monitoring/gpu_exporter.py &
sleep 5
curl -s http://localhost:9200/metrics | grep apple_gpu
```

Expected: lines containing `apple_gpu_utilization_percent`, `apple_ane_utilization_percent`, `apple_gpu_power_watts`.

Check Prometheus targets: `http://localhost:9090/targets` — `node` and `gpu` jobs should now show UP.

- [ ] **Step 7: Commit**

```bash
git add monitoring/gpu_exporter.py tests/monitoring/test_gpu_exporter.py
git commit -m "feat(monitoring): add Apple Silicon GPU metrics exporter + node_exporter setup"
```

---

## Task 3: Pipeline OTel Instrumentation

**Files:**
- Create: `pipeline/telemetry.py`
- Modify: `pipeline/runner.py` (lines 19–26 imports + lines 185–192 execute loop)
- Modify: `pipeline/watcher.py` (lines 1–30 imports + `_run_pipeline` + `_init_telemetry`)
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: `otel-collector` running on `localhost:4317`
- Produces: metrics visible in Prometheus under `mediaflow_*` prefix

- [ ] **Step 1: Add OTel packages to `requirements.txt`**

Append to `requirements.txt` under the `# Pipeline (host-native)` section:

```
opentelemetry-sdk>=1.25.0
opentelemetry-exporter-otlp-proto-grpc>=1.25.0
prometheus-client>=0.21
```

Install:
```bash
source venv/bin/activate
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc prometheus-client
```

- [ ] **Step 2: Create `pipeline/telemetry.py`**

```python
"""OpenTelemetry initialisation for the pipeline (host-native process).

Call init() once at startup before any meters are acquired.
"""
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader


def init(endpoint: str = "localhost:4317") -> metrics.Meter:
    """Set the global OTel MeterProvider and return the pipeline meter.

    Idempotent — returns existing meter if provider already configured.
    """
    if isinstance(metrics.get_meter_provider(), MeterProvider):
        return metrics.get_meter("mediaflow.pipeline")

    exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=15_000)
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    return metrics.get_meter("mediaflow.pipeline")
```

- [ ] **Step 3: Write test for `pipeline/telemetry.py`**

Create `tests/pipeline/test_telemetry.py`:

```python
from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider


def test_init_sets_meter_provider():
    from pipeline.telemetry import init
    # Use a no-op endpoint — connection refused is expected, init must not raise
    meter = init(endpoint="localhost:19999")
    assert isinstance(otel_metrics.get_meter_provider(), MeterProvider)
    assert meter is not None


def test_init_idempotent():
    from pipeline.telemetry import init
    m1 = init(endpoint="localhost:19999")
    m2 = init(endpoint="localhost:19999")
    assert otel_metrics.get_meter_provider() is otel_metrics.get_meter_provider()
```

- [ ] **Step 4: Run tests**

```bash
source venv/bin/activate
pytest tests/pipeline/test_telemetry.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Add stage timing to `pipeline/runner.py`**

Add to the imports block (after existing imports, around line 22):

```python
import time
from opentelemetry import metrics as _otel_metrics
```

Replace the inner loop body in `execute()` (the block starting at `pub.publish("stage.started"...)`) — currently at lines 187–192:

```python
        pub.publish("stage.started", ctx["stem"], stage=sid)
        t0 = time.monotonic()
        ctx, extra = STAGE_RUNNERS[sid](ctx, cfg)
        elapsed = time.monotonic() - t0

        # Record stage duration and last-event timestamp (no-op if OTel not init'd)
        meter = _otel_metrics.get_meter("mediaflow.pipeline")
        meter.create_histogram(
            "mediaflow.stage.duration", unit="s",
            description="Pipeline stage processing time",
        ).record(elapsed, {"stage": sid})
        meter.create_gauge(
            "mediaflow.pipeline.last_stage_ts", unit="s",
            description="Unix timestamp of last stage completion event",
        ).set(time.time())

        pub.publish("stage.completed", ctx["stem"], stage=sid, **extra)
```

> Note: `meter.create_histogram()` called repeatedly returns the same instrument from the SDK cache — it is not expensive.

- [ ] **Step 6: Add job-lifecycle metrics to `pipeline/watcher.py`**

Add after existing imports (around line 20):

```python
from pipeline import telemetry as _tel
from opentelemetry import metrics as _otel_metrics
```

Add this function after `_executor` definition (around line 29):

```python
def _init_telemetry(cfg: dict) -> None:
    endpoint = cfg.get("otel", {}).get("endpoint", "localhost:4317")
    _tel.init(endpoint)


def _meter() -> "_otel_metrics.Meter":
    return _otel_metrics.get_meter("mediaflow.pipeline")
```

In `_run_pipeline()`, add metric updates at the three lifecycle points:

After `stem = path.stem` (around line 33), add:
```python
    _meter().create_up_down_counter(
        "mediaflow.pipeline.active_jobs", unit="jobs"
    ).add(1)
    _meter().create_counter(
        "mediaflow.jobs.submitted", unit="jobs"
    ).add(1, {"recording_type": cfg.get("pipeline", {}).get("recording_type", "auto")})
```

After `path.rename(archive_dir / path.name)` (the success path, around line 64), add:
```python
        _meter().create_counter(
            "mediaflow.jobs.completed", unit="jobs"
        ).add(1, {"recording_type": cfg.get("pipeline", {}).get("recording_type", "auto")})
        _meter().create_up_down_counter(
            "mediaflow.pipeline.active_jobs", unit="jobs"
        ).add(-1)
```

In each `except` block that calls `pub.publish("task.failed", ...)` (there are two — around lines 68 and 88), add before or after the publish call:
```python
        _meter().create_counter(
            "mediaflow.jobs.failed", unit="jobs"
        ).add(1, {"stage": ctx.get("_last_stage", "unknown"), "error_type": type(exc).__name__})
        _meter().create_up_down_counter(
            "mediaflow.pipeline.active_jobs", unit="jobs"
        ).add(-1)
```

In the `main()` function (or wherever `cfg` is loaded before the watcher starts, around line 195), add:
```python
    _init_telemetry(cfg)
```

Also add an observable gauge for queue depth in `main()` after `_init_telemetry(cfg)`:
```python
    _ws = Path(cfg["pipeline"]["workspace_dir"])
    def _queue_depth_callback(options):
        from opentelemetry.metrics import Observation
        depth = len([f for f in (_ws / "1_input").iterdir()
                     if f.is_file() and not f.name.startswith('.')])
        yield Observation(depth)

    _otel_metrics.get_meter("mediaflow.pipeline").create_observable_gauge(
        "mediaflow.queue.depth",
        callbacks=[_queue_depth_callback],
        unit="files",
        description="Files waiting in 1_input/",
    )
```

- [ ] **Step 7: Verify metrics flow end-to-end**

With `otel-collector` running, start the pipeline watcher:
```bash
source venv/bin/activate
bash scripts/start-pipeline.sh
```

Copy a test file:
```bash
cp tests/fixtures/test-speech.m4a workspace/1_input/
```

After the job completes, query Prometheus:
```bash
curl -s 'http://localhost:9090/api/v1/query?query=mediaflow_jobs_submitted_total' | python3 -m json.tool
```

Expected: JSON with `"value"` showing count ≥ 1.

```bash
curl -s 'http://localhost:9090/api/v1/query?query=mediaflow_stage_duration_seconds_count' | python3 -m json.tool
```

Expected: count ≥ 1 per stage that ran.

- [ ] **Step 8: Commit**

```bash
git add pipeline/telemetry.py pipeline/runner.py pipeline/watcher.py requirements.txt tests/pipeline/test_telemetry.py
git commit -m "feat(pipeline): OTel instrumentation — job counters, stage duration histogram, queue depth"
```

---

## Task 4: API OTel Instrumentation

**Files:**
- Modify: `api/main.py` (add OTel init + FastAPI instrumentor)
- Modify: `requirements.txt` (add `opentelemetry-instrumentation-fastapi`)

**Interfaces:**
- Produces: `http.server.request.duration_seconds` histogram in Prometheus (via otel-collector)

- [ ] **Step 1: Add FastAPI instrumentation package to `requirements.txt`**

Append under the `# API` section:
```
opentelemetry-instrumentation-fastapi>=0.46b0
opentelemetry-exporter-otlp-proto-grpc>=1.25.0
opentelemetry-sdk>=1.25.0
```

Rebuild the API image after this step (see Step 5).

- [ ] **Step 2: Add OTel init to `api/main.py`**

Add imports after the existing imports block (around line 12):

```python
import os
from opentelemetry import metrics as _otel_metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
```

Add this function before the `lifespan` context manager:

```python
def _init_otel() -> None:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "otel-collector:4317")
    exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=15_000)
    provider = MeterProvider(metric_readers=[reader])
    _otel_metrics.set_meter_provider(provider)
```

Call `_init_otel()` at module level (before `app = FastAPI(...)`):

```python
_init_otel()
```

After the `app = FastAPI(...)` line, add:

```python
FastAPIInstrumentor.instrument_app(app)
```

- [ ] **Step 3: Add `OTEL_EXPORTER_OTLP_ENDPOINT` to `api` service in `docker-compose.yml`**

In the `api` service `environment` block, add:
```yaml
      - OTEL_EXPORTER_OTLP_ENDPOINT=otel-collector:4317
```

- [ ] **Step 4: Write smoke test for OTel init**

Create `tests/api/test_otel.py`:

```python
"""Verify OTel init does not raise when collector is unreachable."""
import os
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:19999")

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry import metrics as otel_metrics


def test_init_otel_does_not_raise():
    from api.main import _init_otel
    _init_otel()
    assert isinstance(otel_metrics.get_meter_provider(), MeterProvider)
```

Run:
```bash
source venv/bin/activate
pytest tests/api/test_otel.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Rebuild and restart the API container**

```bash
docker compose build api && docker compose up -d api
```

- [ ] **Step 6: Verify HTTP metrics flow to Prometheus**

```bash
# Make a few API requests
curl -s http://localhost:8080/health
curl -s http://localhost:8080/jobs

# Query Prometheus for HTTP duration metric
curl -s 'http://localhost:9090/api/v1/query?query=http_server_request_duration_seconds_count' | python3 -m json.tool
```

Expected: result array with count ≥ 2.

- [ ] **Step 7: Commit**

```bash
git add api/main.py requirements.txt tests/api/test_otel.py docker-compose.yml
git commit -m "feat(api): OTel FastAPI instrumentation — HTTP request duration + active requests"
```

---

## Task 5: Grafana Dashboards

**Files:**
- Create: `monitoring/grafana/dashboards/overview.json`
- Create: `monitoring/grafana/dashboards/pipeline.json`

**Interfaces:**
- Consumes: Prometheus datasource uid `"prometheus"` (set in Task 1 provisioning)
- Produces: 2 custom dashboards + 5 community dashboards loaded in Grafana under "MediaFlow" folder

- [ ] **Step 1: Import community dashboards via Grafana UI**

Open `http://localhost:3001` (default credentials: admin / admin).

Navigate to **Dashboards → Import** and import each of the following by dashboard ID:

| ID | Dashboard | Covers |
|----|-----------|--------|
| 1860 | Node Exporter Full | CPU, memory, disk, network |
| 763 | Redis Exporter | Redis memory, commands, streams |
| 9628 | PostgreSQL | Connections, query latency, table sizes |
| 14282 | cAdvisor | Per-container CPU and memory |
| 7587 | Blackbox Exporter | Service probe success, latency |

For each: paste the ID, click **Load**, select **Prometheus** as the datasource, click **Import**.

- [ ] **Step 2: Create `monitoring/grafana/dashboards/overview.json`**

```json
{
  "title": "MediaFlow — Overview",
  "uid": "mediaflow-overview",
  "tags": ["mediaflow"],
  "timezone": "browser",
  "refresh": "30s",
  "schemaVersion": 39,
  "panels": [
    {
      "id": 1, "type": "stat", "title": "Whisper",
      "gridPos": {"h": 3, "w": 4, "x": 0, "y": 0},
      "targets": [{"datasource": {"uid": "prometheus"}, "expr": "probe_success{instance=\"http://host.docker.internal:9001/health\"}"}],
      "fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}], "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": 0}, {"color": "green", "value": 1}]}}}
    },
    {
      "id": 2, "type": "stat", "title": "Ollama",
      "gridPos": {"h": 3, "w": 4, "x": 4, "y": 0},
      "targets": [{"datasource": {"uid": "prometheus"}, "expr": "probe_success{instance=\"http://host.docker.internal:11434/api/tags\"}"}],
      "fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}], "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": 0}, {"color": "green", "value": 1}]}}}
    },
    {
      "id": 3, "type": "stat", "title": "Diarize",
      "gridPos": {"h": 3, "w": 4, "x": 8, "y": 0},
      "targets": [{"datasource": {"uid": "prometheus"}, "expr": "probe_success{instance=\"http://host.docker.internal:9003/health\"}"}],
      "fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}], "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": 0}, {"color": "green", "value": 1}]}}}
    },
    {
      "id": 4, "type": "stat", "title": "Active Jobs",
      "gridPos": {"h": 3, "w": 4, "x": 12, "y": 0},
      "targets": [{"datasource": {"uid": "prometheus"}, "expr": "mediaflow_pipeline_active_jobs"}],
      "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": 0}, {"color": "yellow", "value": 2}]}}}
    },
    {
      "id": 5, "type": "stat", "title": "Queue Depth",
      "gridPos": {"h": 3, "w": 4, "x": 16, "y": 0},
      "targets": [{"datasource": {"uid": "prometheus"}, "expr": "mediaflow_queue_depth"}],
      "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": 0}, {"color": "yellow", "value": 5}]}}}
    },
    {
      "id": 6, "type": "stat", "title": "Completed Today",
      "gridPos": {"h": 3, "w": 4, "x": 20, "y": 0},
      "targets": [{"datasource": {"uid": "prometheus"}, "expr": "increase(mediaflow_jobs_completed_total[24h])"}],
      "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": 0}]}}}
    },
    {
      "id": 7, "type": "timeseries", "title": "Stage Duration P50 / P95 (last 3h)",
      "gridPos": {"h": 8, "w": 14, "x": 0, "y": 3},
      "targets": [
        {"datasource": {"uid": "prometheus"}, "expr": "histogram_quantile(0.5, rate(mediaflow_stage_duration_seconds_bucket[5m]))", "legendFormat": "P50 {{stage}}"},
        {"datasource": {"uid": "prometheus"}, "expr": "histogram_quantile(0.95, rate(mediaflow_stage_duration_seconds_bucket[5m]))", "legendFormat": "P95 {{stage}}"}
      ]
    },
    {
      "id": 8, "type": "timeseries", "title": "Job Throughput (per hour)",
      "gridPos": {"h": 8, "w": 10, "x": 14, "y": 3},
      "targets": [
        {"datasource": {"uid": "prometheus"}, "expr": "increase(mediaflow_jobs_completed_total[1h])", "legendFormat": "Completed"},
        {"datasource": {"uid": "prometheus"}, "expr": "increase(mediaflow_jobs_failed_total[1h])", "legendFormat": "Failed"}
      ]
    }
  ]
}
```

- [ ] **Step 3: Create `monitoring/grafana/dashboards/pipeline.json`**

```json
{
  "title": "MediaFlow — Pipeline Performance",
  "uid": "mediaflow-pipeline",
  "tags": ["mediaflow"],
  "timezone": "browser",
  "refresh": "1m",
  "schemaVersion": 39,
  "panels": [
    {
      "id": 1, "type": "barchart", "title": "Jobs / Hour — 7 Days",
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
      "targets": [
        {"datasource": {"uid": "prometheus"}, "expr": "increase(mediaflow_jobs_completed_total[1h])", "legendFormat": "Completed {{recording_type}}"},
        {"datasource": {"uid": "prometheus"}, "expr": "increase(mediaflow_jobs_failed_total[1h])", "legendFormat": "Failed {{stage}}"}
      ]
    },
    {
      "id": 2, "type": "timeseries", "title": "Stage Duration — P50 / P95 / P99",
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
      "targets": [
        {"datasource": {"uid": "prometheus"}, "expr": "histogram_quantile(0.50, rate(mediaflow_stage_duration_seconds_bucket[10m]))", "legendFormat": "P50 {{stage}}"},
        {"datasource": {"uid": "prometheus"}, "expr": "histogram_quantile(0.95, rate(mediaflow_stage_duration_seconds_bucket[10m]))", "legendFormat": "P95 {{stage}}"},
        {"datasource": {"uid": "prometheus"}, "expr": "histogram_quantile(0.99, rate(mediaflow_stage_duration_seconds_bucket[10m]))", "legendFormat": "P99 {{stage}}"}
      ]
    },
    {
      "id": 3, "type": "piechart", "title": "Failures by Stage (24h)",
      "gridPos": {"h": 8, "w": 8, "x": 0, "y": 8},
      "targets": [{"datasource": {"uid": "prometheus"}, "expr": "increase(mediaflow_jobs_failed_total[24h])", "legendFormat": "{{stage}}"}]
    },
    {
      "id": 4, "type": "piechart", "title": "Recording Type Distribution (24h)",
      "gridPos": {"h": 8, "w": 8, "x": 8, "y": 8},
      "targets": [{"datasource": {"uid": "prometheus"}, "expr": "increase(mediaflow_jobs_submitted_total[24h])", "legendFormat": "{{recording_type}}"}]
    },
    {
      "id": 5, "type": "stat", "title": "Overall Success Rate (24h)",
      "gridPos": {"h": 8, "w": 8, "x": 16, "y": 8},
      "targets": [{
        "datasource": {"uid": "prometheus"},
        "expr": "increase(mediaflow_jobs_completed_total[24h]) / (increase(mediaflow_jobs_completed_total[24h]) + increase(mediaflow_jobs_failed_total[24h])) * 100"
      }],
      "fieldConfig": {"defaults": {"unit": "percent", "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": 0}, {"color": "yellow", "value": 75}, {"color": "green", "value": 90}]}}}
    }
  ]
}
```

- [ ] **Step 4: Reload Grafana provisioning**

```bash
curl -s -X POST http://admin:admin@localhost:3001/api/admin/provisioning/dashboards/reload
```

Expected: `{"message":"Dashboards config reloaded"}`

- [ ] **Step 5: Verify dashboards load**

Open `http://localhost:3001/dashboards` — confirm "MediaFlow" folder exists with:
- MediaFlow — Overview
- MediaFlow — Pipeline Performance

Click into each dashboard and verify panels render (may show "No data" if no pipeline runs yet — that is expected).

- [ ] **Step 6: Commit**

```bash
git add monitoring/grafana/dashboards/
git commit -m "feat(monitoring): add Overview and Pipeline Performance Grafana dashboards"
```

---

## Task 6: Alert Rules + Notification Channels

**Files:**
- Create: `monitoring/grafana/provisioning/alerting/contactpoints.yml`
- Create: `monitoring/grafana/provisioning/alerting/rules.yml`

**Interfaces:**
- Consumes: `WEBHOOK_URL` environment variable (existing), Grafana SMTP env vars (Task 1)
- Produces: Grafana fires alerts to Webhook and Email on the conditions defined in the spec

- [ ] **Step 1: Create `monitoring/grafana/provisioning/alerting/contactpoints.yml`**

```yaml
apiVersion: 1
contactPoints:
  - orgId: 1
    name: MediaFlow Webhook
    receivers:
      - uid: mediaflow-webhook
        type: webhook
        settings:
          url: "${WEBHOOK_URL}"
          httpMethod: POST
          maxAlerts: 10

  - orgId: 1
    name: MediaFlow Email
    receivers:
      - uid: mediaflow-email
        type: email
        settings:
          addresses: "${GRAFANA_ALERT_EMAIL:-}"
          singleEmail: false
```

> Add `GRAFANA_ALERT_EMAIL` to your environment (`.env` or shell) with the target address.

- [ ] **Step 2: Create `monitoring/grafana/provisioning/alerting/rules.yml`**

```yaml
apiVersion: 1
groups:
  - orgId: 1
    name: mediaflow-critical
    folder: MediaFlow
    interval: 1m
    rules:
      - uid: whisper-down
        title: "[Critical] Whisper service down"
        condition: B
        data:
          - refId: A
            relativeTimeRange: {from: 300, to: 0}
            datasourceUid: prometheus
            model:
              expr: "probe_success{instance=\"http://host.docker.internal:9001/health\"}"
              instant: true
              refId: A
          - refId: B
            datasourceUid: __expr__
            model:
              refId: B
              type: threshold
              conditions:
                - evaluator: {params: [1], type: lt}
                  operator: {type: and}
                  query: {params: [A]}
        for: 2m
        labels: {severity: critical}
        annotations:
          summary: "Whisper HTTP service is not responding on :9001"
        noDataState: Alerting
        execErrState: Alerting

      - uid: ollama-down
        title: "[Critical] Ollama service down"
        condition: B
        data:
          - refId: A
            relativeTimeRange: {from: 300, to: 0}
            datasourceUid: prometheus
            model:
              expr: "probe_success{instance=\"http://host.docker.internal:11434/api/tags\"}"
              instant: true
              refId: A
          - refId: B
            datasourceUid: __expr__
            model:
              refId: B
              type: threshold
              conditions:
                - evaluator: {params: [1], type: lt}
                  operator: {type: and}
                  query: {params: [A]}
        for: 2m
        labels: {severity: critical}
        annotations:
          summary: "Ollama service is not responding on :11434"
        noDataState: Alerting
        execErrState: Alerting

      - uid: pipeline-stuck
        title: "[Critical] Pipeline job stuck > 30 min"
        condition: B
        data:
          - refId: A
            relativeTimeRange: {from: 300, to: 0}
            datasourceUid: prometheus
            model:
              expr: "(mediaflow_pipeline_active_jobs > 0) and (time() - mediaflow_pipeline_last_stage_ts > 1800)"
              instant: true
              refId: A
          - refId: B
            datasourceUid: __expr__
            model:
              refId: B
              type: threshold
              conditions:
                - evaluator: {params: [0], type: gt}
                  operator: {type: and}
                  query: {params: [A]}
        for: 5m
        labels: {severity: critical}
        annotations:
          summary: "A pipeline job has not progressed for 30+ minutes"

  - orgId: 1
    name: mediaflow-warning
    folder: MediaFlow
    interval: 5m
    rules:
      - uid: disk-low
        title: "[Warning] Disk space < 20%"
        condition: B
        data:
          - refId: A
            relativeTimeRange: {from: 300, to: 0}
            datasourceUid: prometheus
            model:
              expr: "node_filesystem_avail_bytes{mountpoint=\"/\"} / node_filesystem_size_bytes{mountpoint=\"/\"} * 100"
              instant: true
              refId: A
          - refId: B
            datasourceUid: __expr__
            model:
              refId: B
              type: threshold
              conditions:
                - evaluator: {params: [20], type: lt}
                  operator: {type: and}
                  query: {params: [A]}
        for: 5m
        labels: {severity: warning}
        annotations:
          summary: "Disk free space is below 20% — workspace volume may fill up"

      - uid: job-failure-rate
        title: "[Warning] Job failure rate > 25%"
        condition: B
        data:
          - refId: A
            relativeTimeRange: {from: 3600, to: 0}
            datasourceUid: prometheus
            model:
              expr: "increase(mediaflow_jobs_failed_total[1h]) / (increase(mediaflow_jobs_failed_total[1h]) + increase(mediaflow_jobs_completed_total[1h])) * 100"
              instant: true
              refId: A
          - refId: B
            datasourceUid: __expr__
            model:
              refId: B
              type: threshold
              conditions:
                - evaluator: {params: [25], type: gt}
                  operator: {type: and}
                  query: {params: [A]}
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "More than 25% of pipeline jobs have failed in the last hour"
```

- [ ] **Step 3: Create notification policy**

Create `monitoring/grafana/provisioning/alerting/policy.yml`:

```yaml
apiVersion: 1
policies:
  - orgId: 1
    receiver: MediaFlow Webhook
    routes:
      - receiver: MediaFlow Webhook
        matchers:
          - severity =~ "critical|warning"
      - receiver: MediaFlow Email
        matchers:
          - severity = critical
```

- [ ] **Step 4: Reload Grafana alerting provisioning**

```bash
curl -s -X POST http://admin:admin@localhost:3001/api/admin/provisioning/alertmanager/reload
curl -s -X POST http://admin:admin@localhost:3001/api/admin/provisioning/alerts/reload
```

- [ ] **Step 5: Verify alert rules appear in Grafana**

Open `http://localhost:3001/alerting/list` — you should see 5 alert rules under "MediaFlow" folder: Whisper Down, Ollama Down, Pipeline Stuck, Disk Low, Job Failure Rate.

All rules will show "Normal" state if services are running. To test firing: stop the Whisper service and wait 2 minutes; the Whisper Down alert should transition to "Firing".

- [ ] **Step 6: Commit**

```bash
git add monitoring/grafana/provisioning/alerting/
git commit -m "feat(monitoring): add Grafana alert rules + webhook/email contact points"
```

---

## Task 7: docs/monitoring.excalidraw

**Files:**
- Create: `docs/monitoring.excalidraw`

**Interfaces:**
- Produces: Architecture diagram of the full monitoring stack saved to docs/monitoring.excalidraw

- [ ] **Step 1: Open Excalidraw MCP tool and create the diagram**

Use the `mcp__claude_ai_Excalidraw__create_view` tool to draw the monitoring architecture. The diagram must include these components and connections:

**Host layer (left column, top-to-bottom):**
- `pipeline/watcher.py + stages.py` box with label "OTel SDK → OTLP push"
- `gpu_exporter.py :9200` box with label "powermetrics → /metrics"
- `node_exporter :9100` box with label "system → /metrics"
- `Whisper :9001`, `Ollama :11434`, `Diarize :9003` boxes labelled "probed"

**Docker layer (middle column):**
- `otel-collector :4317/:8889` — receives from pipeline/api, exposes /metrics
- `prometheus :9090` — scrapes all targets, 15d retention
- `grafana :3001` — dashboards + alerts
- `cadvisor :8081`, `redis_exporter :9121`, `postgres_exporter :9187`, `blackbox_exporter :9115` — each pointing to prometheus
- `api :8080` box with "OTel FastAPI Instrumentor"

**Alert egress (right column):**
- `WEBHOOK_URL (ntfy/Slack)` and `Email (SMTP)` boxes
- Arrow from grafana to both

**Connections:**
- pipeline → OTLP → otel-collector
- api → OTLP → otel-collector
- otel-collector → /metrics → prometheus
- node_exporter, gpu_exporter → scrape → prometheus
- cadvisor, redis_exporter, postgres_exporter, blackbox_exporter → scrape → prometheus
- minio (/minio/v2/metrics) → scrape → prometheus
- blackbox → HTTP probe → Whisper, Ollama, Diarize
- prometheus → query → grafana
- grafana → alert → webhook + email

- [ ] **Step 2: Export and save**

Use `mcp__claude_ai_Excalidraw__export_to_excalidraw` to export the diagram to `docs/monitoring.excalidraw`.

- [ ] **Step 3: Verify file**

```bash
python3 -c "import json; d=json.load(open('docs/monitoring.excalidraw')); print(f'Elements: {len(d[\"elements\"])}')"
```

Expected: Elements > 20.

- [ ] **Step 4: Commit**

```bash
git add docs/monitoring.excalidraw
git commit -m "docs: add monitoring architecture diagram (docs/monitoring.excalidraw)"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|-----------------|------------|
| Operational visibility (alerts) | Task 6 |
| Performance tuning (GPU/CPU) | Task 2 (gpu_exporter, node_exporter) + Task 5 (Infrastructure dashboard via community import 1860) |
| Business metrics (throughput, stage P95, success rate) | Task 3 (OTel SDK) + Task 5 (Pipeline dashboard) |
| Grafana Alerting → Webhook + Email | Task 6 |
| 15-day Prometheus retention | Task 1 (--storage.tsdb.retention.time=15d) |
| docs/monitoring.excalidraw (new file) | Task 7 |
| All 6 metric categories | Tasks 2, 3, 4 + community dashboards (Task 5) |
| 5 alert rules | Task 6 |
| MinIO metrics | Task 1 (prometheus.yml minio scrape job + MINIO_PROMETHEUS_AUTH_TYPE=public) |

**No gaps found.**
