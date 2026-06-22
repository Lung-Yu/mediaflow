# MediaFlow — Risk Inventory

**Date**: 2026-06-23 | **Methodology**: STRIDE | **Scope**: Full system

---

| VR-ID | Title | Category | Rating | Score | STRIDE | Mitigation |
|---|---|---|---|---|---|---|
| VR-001 | MinIO admin via default credentials | Auth | **Critical** | 25 | E, I | M1, M13 |
| VR-002 | Stage-callback forging (no shared secret) | Auth | **Critical** | 20 | S, T | M3 |
| VR-003 | Redis stream injection (no auth) | Auth | **Critical** | 20 | T, E | M2, M13 |
| VR-004 | Full job list exposed unauthenticated | Auth | **Critical** | 15 | I | M4 |
| VR-005 | Job queue flooding (no rate limit) | Auth/DoS | **Critical** | 15 | D | M4, M5 |
| VR-006 | Transcript tampering / finalization w/o auth | Auth | **Critical** | 15 | T, E | M4 |
| VR-007 | Unauthenticated bulk job deletion | Auth | **Critical** | 16 | T, D | M4, M11 |
| VR-008 | Grafana default admin credentials | Auth | **Critical** | 15 | I, E | M1 |
| VR-009 | Storage exhaustion via uncapped uploads | DoS | **High** | 12 | D | M5, M10 |
| VR-010 | Unauthenticated job rerun | Auth | **High** | 10 | E | M4 |
| VR-011 | MinIO presigned URL CORS wildcard | Config | **High** | 9 | I | M6 |
| VR-012 | LLM prompt injection via crafted audio | Injection | **High** | 9 | E | M14 |
| VR-013 | Job deletion destroys audit trail | Audit | **High** | 8 | R | M11 |
| VR-014 | Decompression bomb / FFmpeg exhaustion | DoS | **High** | 8 | D | M7 |
| VR-015 | SSRF via webhook URL | SSRF | **High** | 8 | E | M12 |
| VR-016 | Internal paths leaked in error messages | Info | **High** | 8 | I | M9 |
| VR-017 | Segments.json poisoning → FFmpeg args | Injection | **Medium** | 6 | T | M15 |
| VR-018 | GPU starvation via max queue depth | DoS | **Medium** | 6 | D | M5 |
| VR-019 | Job ID enumeration | Info | **Medium** | 6 | I | M4 |
| VR-020 | No user attribution | Audit | **Medium** | 5 | R | M4 |
| VR-021 | `initial_prompt` stored in job records | Info | **Low** | 3 | I | M8 |
| VR-022 | Caller-controlled DAG flow selection | Config | **Medium** | 5 | T | M4 |

---

## Summary

| Rating | Count |
|---|---|
| Critical | 8 |
| High | 8 |
| Medium | 5 |
| Low | 1 |
| **Total** | **22** |

**Most critical attack surface**: The complete absence of authentication on the FastAPI layer means every threat in this inventory is exploitable with a single unauthenticated HTTP request or Redis connection.
