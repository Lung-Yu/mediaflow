# P6 — Risk Validation

Risk scoring: Likelihood × Impact on a 1–5 scale. Score ≥ 12 = Critical, ≥ 8 = High, ≥ 4 = Medium, < 4 = Low.

---

## Validated Risk Register

| VR-ID | STRIDE-ID | Title | Likelihood | Impact | Score | Rating | Validated |
|---|---|---|---|---|---|---|---|
| VR-001 | E6, I6 | MinIO admin access via default credentials | 5 | 5 | 25 | **Critical** | ✓ |
| VR-002 | S1, T2 | Unauthenticated stage-callback forging | 5 | 4 | 20 | **Critical** | ✓ |
| VR-003 | T1, E1 | Redis stream injection (no auth) | 4 | 5 | 20 | **Critical** | ✓ |
| VR-004 | I1 | Full job list / audio paths exposed to anyone | 5 | 3 | 15 | **Critical** | ✓ |
| VR-005 | D1 | Unauthenticated job queue flooding | 5 | 3 | 15 | **Critical** | ✓ |
| VR-006 | T3, E5 | Unauthenticated transcript correction / finalization | 5 | 3 | 15 | **Critical** | ✓ |
| VR-007 | D2 | Unauthenticated bulk job deletion (destroys audit) | 4 | 4 | 16 | **Critical** | ✓ |
| VR-008 | I5 | Grafana with default `admin` credentials | 5 | 3 | 15 | **Critical** | ✓ |
| VR-009 | D3 | Storage exhaustion via uncapped uploads | 4 | 3 | 12 | **High** | ✓ |
| VR-010 | E4 | Unauthenticated job rerun | 5 | 2 | 10 | **High** | ✓ |
| VR-011 | I3, I2 | MinIO presigned URL abuse (CORS `*`) | 3 | 3 | 9 | **High** | ✓ |
| VR-012 | E3 | LLM prompt injection via crafted transcript | 3 | 3 | 9 | **High** | ✓ |
| VR-013 | R3 | Job deletion destroys audit trail | 4 | 2 | 8 | **High** | ✓ |
| VR-014 | D4 | Decompression bomb / FFmpeg resource exhaustion | 2 | 4 | 8 | **High** | ✓ |
| VR-015 | E2 | SSRF via webhook URL | 2 | 4 | 8 | **High** | ✓ |
| VR-016 | I4 | Internal path leakage in error messages | 4 | 2 | 8 | **High** | ✓ |
| VR-017 | T4 | Segments.json poisoning → FFmpeg CLI | 2 | 3 | 6 | **Medium** | ✓ |
| VR-018 | D5 | Whisper/GPU resource starvation via queue | 3 | 2 | 6 | **Medium** | ✓ |
| VR-019 | S3 | Job ID enumeration | 3 | 2 | 6 | **Medium** | ✓ |
| VR-020 | R1, R2 | No user attribution / audit accountability | 5 | 1 | 5 | **Medium** | ✓ |
| VR-021 | I7 | `initial_prompt` stored and returned in job records | 3 | 1 | 3 | **Low** | ✓ |
| VR-022 | T5 | Caller-controlled DAG flow selection | 5 | 1 | 5 | **Medium** | ✓ |

---

## Attack Chain Validation

### Chain 1: Storage Wipe (VR-001 → VR-007 → VR-003)
```
1. Connect to MinIO port 9002 with default mediaflow/changeme
2. Delete all buckets (input, processing, output, clips)
3. Connect to Redis port 6379 (no auth), DEL mediaflow:jobs stream
4. All audio, transcripts, summaries — permanently lost
```
**Verified feasible**: Docker Compose exposes all service ports to host; MinIO console at :9002 requires no additional exploitation.

### Chain 2: Pipeline Hijack (VR-003 → VR-002)
```
1. XADD to mediaflow:jobs with processing_path pointing to attacker's bucket object
2. Worker downloads the attacker's file and runs it through FFmpeg + Whisper
3. Optionally, POST /internal/stage-callback to mark job done without waiting for worker
```
**Verified feasible**: Redis port 6379 accessible from host (docker-compose ports binding). Worker trusts all MQ messages unconditionally.

### Chain 3: Full Job Data Exfiltration (VR-004 → VR-001)
```
1. GET http://host:8080/jobs → enumerate all job_ids + minio_processing_key values
2. Use MinIO default credentials to download all audio from processing/ bucket
3. Download all SRT + summaries from output/ bucket
```
**Verified feasible**: No auth at either step.

### Chain 4: Transcript Corruption (VR-006 → VR-013)
```
1. GET /jobs → find job_id
2. PATCH /jobs/{id}/correction with corrupted segments
3. POST /jobs/{id}/correction/finalize → marked as verified
4. DELETE /jobs/{id} → removes audit trail of original events
```
**Verified feasible**: All endpoints unauthenticated.
