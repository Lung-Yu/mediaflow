# Grafana Monitoring & Observability Design

**Date:** 2026-06-20  
**Scope:** Full-stack observability for mediaflow — system resources, pipeline business metrics, external service health, alerting  
**Diagram:** `docs/monitoring.excalidraw` (new file, separate from `docs/architecture.excalidraw`)

---

## Goals

- **Operational visibility** — know when services crash or pipeline stalls before the user notices
- **Performance tuning** — observe actual GPU/CPU consumption per stage (Whisper, Ollama)
- **Business metrics** — throughput trends, per-stage processing time P50/P95, success rate
- **Alerting** — Grafana Alerting → Webhook (ntfy/Slack) + Email; 15-day retention

---

## Architecture

### Data Flow

```
[Host — Apple Silicon]
  pipeline/watcher.py + stages.py
    └── opentelemetry-sdk  →  OTLP gRPC push  →  otel-collector:4317

  node_exporter   :9100   ← CPU / mem / disk / net (scrape)
  gpu_exporter.py :9200   ← powermetrics GPU + ANE (scrape, custom Python)

  Whisper  :9001  ─┐
  Ollama   :11434 ─┤← blackbox_exporter HTTP probe (liveness + latency)
  Diarize  :9003  ─┘

[Docker Compose — new services]
  otel-collector   :4317 OTLP gRPC in
                   :8889 Prometheus exporter out
  prometheus       :9090  scrape all targets → 15d TSDB
  grafana          :3001  dashboards + alerting
  cadvisor         :8081  Docker container resources
  redis_exporter   :9121  Redis Streams metrics
  postgres_exporter:9187  PostgreSQL metrics
  blackbox_exporter:9115  HTTP probes

[Existing services — instrumentation added]
  api (Docker)
    └── opentelemetry-fastapi-instrumentator
        → OTLP push → otel-collector:4317

  minio (Docker)
    └── built-in /minio/v2/metrics/cluster (Prometheus scrape directly)

[Alert egress]
  Grafana Alerting → WEBHOOK_URL (ntfy / Slack)
                   → Email (SMTP config in grafana)
```

### Why OTel over prometheus_client

The pipeline watcher runs on the host (outside Docker). OpenTelemetry OTLP push avoids the need for a Pushgateway — watcher pushes metrics directly to the Collector, which then exposes them to Prometheus. This also means pipeline metrics survive scrape-timing gaps during watcher restarts.

### Scrape Targets (prometheus.yml)

| Target | Address | Interval |
|--------|---------|----------|
| otel-collector (pipeline + API business metrics) | `otel-collector:8889` | 15s |
| node_exporter (host system) | `host.docker.internal:9100` | 15s |
| gpu_exporter (Apple Silicon) | `host.docker.internal:9200` | 15s |
| cadvisor (containers) | `cadvisor:8081` | 15s |
| redis_exporter | `redis_exporter:9121` | 15s |
| postgres_exporter | `postgres_exporter:9187` | 15s |
| blackbox_exporter | `blackbox_exporter:9115` | 30s |
| minio (built-in) | `minio:9000/minio/v2/metrics/cluster` | 30s |

---

## Metrics Catalog

### ① System Resources (node_exporter)

| Metric | Purpose |
|--------|---------|
| `node_cpu_seconds_total` | CPU utilization all cores |
| `node_memory_MemAvailable_bytes` | Available RAM |
| `node_filesystem_avail_bytes{mountpoint="/"}` | Disk free (workspace volume) |
| `node_disk_io_time_seconds_total` | Disk I/O pressure |

### ② Apple Silicon GPU (gpu_exporter.py — custom)

Calls `sudo powermetrics --samplers gpu_power -n 1 --json` and exposes via `prometheus_client` HTTP server on `:9200`. Requires a launchd entry or sudo passwordless rule for the mediaflow user.

| Metric | Purpose |
|--------|---------|
| `apple_gpu_utilization_percent` | GPU busy % — spikes during Whisper/Ollama inference |
| `apple_ane_utilization_percent` | Neural Engine utilization |
| `apple_gpu_power_watts` | GPU power draw — thermal throttle early warning |

### ③ Container Resources (cAdvisor)

| Metric | Purpose |
|--------|---------|
| `container_cpu_usage_seconds_total{name}` | Per-container CPU |
| `container_memory_usage_bytes{name}` | Per-container memory |
| `container_restarts_total{name}` | Crash detection |

### ④ Infrastructure Services

| Metric | Source | Purpose |
|--------|--------|---------|
| `redis_stream_length{stream="mediaflow:events"}` | redis_exporter | Queue backlog |
| `redis_memory_used_bytes` | redis_exporter | Redis memory health |
| `pg_stat_activity_count{state="active"}` | postgres_exporter | Active connections |
| `pg_database_size_bytes` | postgres_exporter | DB growth trend |
| `minio_bucket_usage_total_bytes{bucket}` | MinIO built-in | Per-bucket storage |
| `minio_s3_requests_errors_total` | MinIO built-in | Error rate |

### ⑤ External Service Health (blackbox_exporter)

Probes configured in `blackbox.yml` as `http_2xx` modules:

| Target | Probe URL |
|--------|-----------|
| Whisper | `http://host.docker.internal:9001/health` |
| Ollama | `http://host.docker.internal:11434/api/tags` |
| Diarize | `http://host.docker.internal:9003/health` |

Key metrics: `probe_success{instance}`, `probe_duration_seconds{instance}`

### ⑥ Pipeline Business Metrics (OTEL SDK)

**Instrumentation points:**
- `pipeline/watcher.py` — job lifecycle (submitted, active count, queue depth)
- `pipeline/stages.py` — per-stage duration and failure counters

| Metric | Type | Labels | Instrumentation point |
|--------|------|--------|-----------------------|
| `mediaflow.jobs.submitted` | Counter | `recording_type` | watcher.py on job start |
| `mediaflow.jobs.completed` | Counter | `recording_type` | watcher.py on job done |
| `mediaflow.jobs.failed` | Counter | `stage`, `error_type` | stages.py on exception |
| `mediaflow.stage.duration` | Histogram | `stage` | stages.py per-stage timing |
| `mediaflow.queue.depth` | Gauge | — | watcher.py, len(1_input/) |
| `mediaflow.pipeline.active_jobs` | Gauge | — | watcher.py ThreadPoolExecutor |
| `mediaflow.pipeline.last_stage_ts` | Gauge | — | stages.py, Unix epoch on each stage event |

> **Naming note:** OTel SDK uses dot notation (`mediaflow.jobs.submitted`). Prometheus stores these as underscores (`mediaflow_jobs_submitted_total`). Use underscore form in PromQL / Grafana queries.

**API metrics (opentelemetry-fastapi-instrumentator, automatic):**

| Metric | Type |
|--------|------|
| `http.server.request.duration` | Histogram (by route, method, status) |
| `http.server.active_requests` | Gauge |

---

## Dashboards (Grafana folder: MediaFlow)

### Dashboard 1 — Overview
Entry point for on-call. Shows at-a-glance health across all services.

- **Row 1:** Service status lamps (Whisper / Ollama / Diarize / API / Redis / PostgreSQL) — green/red based on `probe_success` and container liveness
- **Row 2:** Stat panels — Active Jobs, Queue Depth, Today's Completed, Today's Failed
- **Row 3:** Jobs status pie (success/failed/processing, last 24h) + Stage duration trend (P50/P95 time series, last 24h)
- **Row 4:** Recent failed jobs table (stem, stage, error_msg, timestamp)

### Dashboard 2 — Pipeline Performance
Business metric deep-dive.

- Throughput: jobs/hour bar chart (7-day window)
- Stage Duration Heatmap: preprocess / transcribe / summarize / diarize
- Failure breakdown: by stage (bar) + by error_type (pie)
- Recording type distribution (course / meeting / general)

### Dashboard 3 — Infrastructure
System and container resources.

- CPU %, Memory %, Disk available GB (time series)
- GPU Utilization %, ANE %, GPU Power W (time series)
- Per-container CPU + Memory (multi-line)
- Container restart events (bar + annotations)

### Dashboard 4 — Services
External service reliability.

- Uptime % per service (last 24h stat panels)
- Probe response latency P50/P95 (time series, 3 lines)
- Probe failure history (annotations on shared timeline)

### Dashboard 5 — Storage
Capacity trends.

- MinIO bucket sizes over time (stacked area: input / processing / output / clips)
- PostgreSQL DB size + active connections (dual-axis)
- Redis memory + stream backlog length (dual-axis)

---

## Alert Rules

| Severity | Condition | Channel |
|----------|-----------|---------|
| Critical | `probe_success{instance=~"whisper|ollama|diarize"} == 0` for 2 min | Webhook + Email |
| Critical | `mediaflow_pipeline_active_jobs > 0` AND `time() - mediaflow_pipeline_last_stage_ts > 1800` | Webhook + Email |
| Warning | `node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.20` | Webhook |
| Warning | Job failure rate > 25% in last 1h (`jobs.failed / jobs.submitted`) | Webhook |
| Warning | `mediaflow.stage.duration` P95 > 2× 7-day baseline | Webhook |
| Info | `mediaflow.queue.depth > 5` | Grafana annotation only |

---

## New Files & Changes

### New files
```
monitoring/
  gpu_exporter.py           — Apple Silicon powermetrics → Prometheus /metrics :9200
  otel-collector-config.yml — OTEL Collector pipeline config
  prometheus.yml            — scrape configs + retention (15d)
  blackbox.yml              — HTTP probe module definitions
  grafana/
    provisioning/
      datasources/prometheus.yml
      dashboards/dashboards.yml
    dashboards/
      overview.json
      pipeline.json
      infrastructure.json
      services.json
      storage.json

docs/monitoring.excalidraw  — monitoring architecture diagram (new)
```

### Modified files
```
docker-compose.yml          — add: otel-collector, prometheus, grafana,
                              cadvisor, redis_exporter, postgres_exporter,
                              blackbox_exporter
pipeline/watcher.py         — OTEL SDK init + job lifecycle metrics
pipeline/stages.py          — OTEL stage duration histogram + failure counters
api/main.py                 — opentelemetry-fastapi-instrumentator setup
requirements.txt            — opentelemetry-sdk, opentelemetry-exporter-otlp,
                              opentelemetry-instrumentation-fastapi
```

### Host setup (one-time)
```bash
brew install node_exporter   # starts automatically as launchd service
# configure sudo passwordless for powermetrics (gpu_exporter.py)
```

---

## Out of Scope

- Distributed tracing (Jaeger / Tempo) — addable later by extending OTEL Collector config; no re-instrumentation needed
- Log aggregation (Loki) — separate concern; current logs via `docker compose logs`
- Multi-host / remote Prometheus — single Mac mini scope; Thanos/VictoriaMetrics if scale-out needed later
