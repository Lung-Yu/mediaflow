# MediaFlow — Risk Assessment Report

**Date**: 2026-06-23  
**Methodology**: STRIDE Threat Modeling (fr33d3m0n/threat-modeling v3.2.0)  
**Scope**: Full system — FastAPI, PostgreSQL, Redis, MinIO, Worker, Watcher, Whisper, Ollama, Frontend  
**Deployment context**: Single Mac mini, Apple Silicon; services currently treated as single-user / LAN-only

---

## Executive Summary

MediaFlow is a well-structured audio pipeline with clean service separation and good internal design. However, it was built for single-user, trusted-LAN use and **has no security controls at any boundary**. Every service port is unauthenticated. Every API endpoint is publicly callable without credentials. Default passwords are in use across all services.

**This system must not be exposed to an untrusted network in its current state.**

The good news: the fixes are straightforward. 8 critical risks can be resolved in approximately 7 hours of engineering time (P0 items below). The architecture itself does not need to change — auth layers and credential rotation are additive.

---

## Risk Summary

| Rating | Count |
|---|---|
| **Critical** | 8 |
| **High** | 8 |
| **Medium** | 5 |
| **Low** | 1 |
| **Total** | 22 |

---

## Top 8 Critical Risks

### VR-001 — MinIO Admin Access via Default Credentials
**Score**: 25/25 | **STRIDE**: Elevation, Information Disclosure

MinIO is running with factory credentials (`mediaflow` / `changeme`) exposed on port 9000 and console on port 9002. An attacker with network access can read and delete all audio files, transcripts, and summaries using standard S3 API calls. No exploitation required — just the default username and password from the docker-compose file.

**Fix**: M1 (rotate credentials) + M10 (bind port to 127.0.0.1). 45 min.

---

### VR-002 — Stage Callback Forgery (No Shared Secret)
**Score**: 20/25 | **STRIDE**: Spoofing, Tampering

The Worker reports pipeline stage results via `POST /internal/stage-callback`. This endpoint is on the same public port (8080) as user-facing APIs and accepts any `job_id` + `status` without authentication. Any caller can mark any job as completed, failed, or jump it to any stage — corrupting the DAG state machine.

**Fix**: M3 (shared secret header). 2 hours.

---

### VR-003 — Redis Stream Injection (No Password)
**Score**: 20/25 | **STRIDE**: Tampering, Elevation

Redis is running without `requirepass`, port 6379 accessible from the host. An attacker can `XADD` arbitrary messages to `mediaflow:jobs`, causing the Worker to download and process attacker-controlled files. The Worker trusts all MQ messages unconditionally.

**Fix**: M2 (Redis `requirepass`). 15 min.

---

### VR-004 — Full Job List Exposed Unauthenticated
**Score**: 15/25 | **STRIDE**: Information Disclosure

`GET /jobs` returns all jobs, filenames, MinIO processing keys, and error messages to any HTTP caller. No authentication required. Combined with VR-001 (MinIO default creds), this provides a complete exfiltration path: enumerate jobs → download all audio.

**Fix**: M4 (API key auth). 4 hours.

---

### VR-005 — Unauthenticated Job Queue Flooding
**Score**: 15/25 | **STRIDE**: Denial of Service

`POST /jobs` and `POST /upload/complete` have no rate limiting and no authentication. An attacker can fill the Redis queue to `max_queue_depth` (default: 20) with no-op or malicious jobs, blocking all legitimate processing.

**Fix**: M4 (auth) + M5 (rate limiting). 6 hours.

---

### VR-006 — Transcript Tampering Without Auth
**Score**: 15/25 | **STRIDE**: Tampering, Elevation

`PATCH /jobs/{id}/correction` and `POST /jobs/{id}/correction/finalize` are unauthenticated. Any caller can replace transcript segments for any job and mark it as verified. This directly undermines the transcript integrity model (FR4).

**Fix**: M4 (API key auth). 4 hours (same as VR-004 fix).

---

### VR-007 — Unauthenticated Bulk Job Deletion
**Score**: 16/25 | **STRIDE**: Tampering, Denial of Service

`DELETE /jobs/{id}` hard-deletes the job row and cascades to all `events` rows. No authentication, no soft delete. An attacker who enumerates job IDs (trivial via VR-004) can destroy the entire job history and audit trail irreversibly.

**Fix**: M4 (auth) + M11 (soft delete). 6 hours.

---

### VR-008 — Grafana Default Admin Credentials
**Score**: 15/25 | **STRIDE**: Information Disclosure, Elevation

Grafana is running on port 3001 with `admin` / `admin`. Provides access to all Prometheus metrics, job throughput, latency, error rates, and alert configuration. Grafana also has webhook alerts configured — a compromised Grafana instance can modify alert destinations.

**Fix**: M1 (rotate `GRAFANA_PASSWORD`). 5 min.

---

## Validated Attack Chains

### Chain 1: Complete Data Wipe (10 minutes, no prior access)
```
1. curl GET /jobs → enumerate all job IDs and minio keys
2. Connect to MinIO :9000 with mediaflow/changeme → list + delete all buckets
3. Connect to Redis :6379 → DEL mediaflow:jobs
4. curl DELETE /jobs/{id} for each job → destroy DB records
Result: All audio, transcripts, summaries — permanently destroyed
```

### Chain 2: Transparent Pipeline Hijack
```
1. Connect to Redis :6379
2. XADD mediaflow:jobs with attacker-controlled processing_path
3. Worker downloads and processes attacker file
4. POST /internal/stage-callback to mark job done
Result: Attacker controls what gets processed and what the output claims
```

### Chain 3: Full Audio Exfiltration
```
1. GET /jobs (no auth) → all minio_processing_key values
2. s3.list_objects("mediaflow-output") with default creds → all file names
3. s3.get_object() for each file → all audio, SRT, summaries downloaded
Result: Complete data breach in under 5 minutes
```

---

## Remediation Roadmap

### P0 — Block Before Any Network Exposure (~7 hours)

| # | Action | VR Addressed | Effort |
|---|---|---|---|
| M1 | Rotate all default credentials (postgres, minio, grafana, redis) | VR-001, VR-008 | 30 min |
| M2 | Add Redis `requirepass` | VR-003 | 15 min |
| M3 | Add `X-Worker-Secret` header check on `/internal/stage-callback` | VR-002 | 2h |
| M4 | Add static API key authentication to all user-facing endpoints | VR-004–007, VR-010 | 4h |

### P1 — Short Term (~10 hours)

| # | Action | VR Addressed | Effort |
|---|---|---|---|
| M5 | Rate limiting on POST /jobs, /upload/init, /upload/complete | VR-005, VR-009 | 2h |
| M6 | Restrict MinIO CORS to frontend origin | VR-011 | 15 min |
| M7 | File magic byte validation before pipeline trigger | VR-014 | 3h |
| M8 | Cap `initial_prompt` to 500 chars | VR-021 | 30 min |
| M9 | Sanitize error responses (no internal paths) | VR-016 | 2h |

### P2 — Medium Term (~9 hours)

| # | Action | VR Addressed | Effort |
|---|---|---|---|
| M10 | Bind Docker service ports to 127.0.0.1 | VR-001, VR-003 (defence) | 30 min |
| M11 | Soft-delete jobs (preserve audit trail) | VR-013 | 2h |
| M12 | Webhook SSRF prevention | VR-015 | 2h |
| M13 | Stage name allowlist in callback handler | VR-017 | 1h |
| M14 | Prompt injection hardening in Ollama stages | VR-012 | 4h |

---

## Safe Zone Statement

**Current state**: Safe only if the Mac mini is on a fully trusted, isolated LAN with no external access and no untrusted users on the same network segment.

**After P0 mitigations**: Safe for LAN use. Critical authentication and credential gaps closed.

**After P0+P1**: Safe for deployment behind a VPN or reverse proxy with TLS termination.

**After P0+P1+P2**: Hardened for multi-user deployment with audit trail and injection resistance.

---

## Appendix: Files Generated

| File | Contents |
|---|---|
| `P1-PROJECT-UNDERSTANDING.md` | Component inventory, entry points, data assets |
| `P2-DFD-ANALYSIS.md` | Data flow diagrams (L0, L1), flow table |
| `P3-TRUST-BOUNDARY.md` | Trust zone definitions, boundary violations |
| `P4-SECURITY-DESIGN-REVIEW.md` | Auth gaps, injection risks, secrets inventory |
| `P5-STRIDE-THREATS.md` | 22 threats across all STRIDE categories |
| `P6-RISK-VALIDATION.md` | Risk scoring, attack chain validation |
| `P7-MITIGATION-PLAN.md` | Detailed mitigation steps with code samples |
| `mediaflow-RISK-INVENTORY.md` | Scored risk register |
| `mediaflow-MITIGATION-MEASURES.md` | Implementation-ready mitigation details |
| `mediaflow-PENETRATION-TEST-PLAN.md` | 12 test cases with curl/redis-cli commands |
| `mediaflow-RISK-ASSESSMENT-REPORT.md` | This document |
