# P2 — Data Flow Diagram Analysis

---

## DFD Level 0 (Context)

```
[Browser]──upload──►[MediaFlow API :8080]──process──►[Pipeline Worker]
                            │                               │
                     [MinIO :9000]◄──────────────────────►[Whisper :9001]
                     [PostgreSQL :5432]                    [Ollama :11434]
                     [Redis :6379]
```

---

## DFD Level 1 (Process Decomposition)

```
                ┌──────────────────────────────────────────────────────┐
                │  TRUST BOUNDARY: Docker Network (mediaflow_default)   │
                │                                                        │
[Browser] ──────┼──► [1.1 Upload API]                                  │
     │          │         │ presigned PUT                               │
     │          │         ▼                                              │
     │          │   [MinIO: input/]                                     │
     │          │         │ copy                                         │
     │          │    [1.2 Project Service]                              │
     │          │         │ FR6 check                                   │
     │          │         │ upsert job                                  │
     │          │         ▼                                              │
     │          │   [PostgreSQL: jobs]                                  │
     │          │         │ trigger                                      │
     │          │    [1.3 DAG-Service] ──XADD──► [Redis: mediaflow:jobs]│
     │          │                                        │               │
     │          └────────────────────────────────────────┼──────────────┘
     │                                                   │
     │             ┌─────────────────────────────────────┼───────────────┐
     │             │  TRUST BOUNDARY: Host (Apple Silicon)│               │
     │             │                                      ▼               │
     │             │                           [2.1 Worker: XREADGROUP]  │
     │             │                                      │               │
     │             │                    ┌─────────────────┤               │
     │             │                    │  download from  │               │
     │             │                    ▼  MinIO:processing/              │
     │             │             [2.2 FFmpeg preprocess]                 │
     │             │                    │                                 │
     │             │                    ▼                                 │
     │             │             [2.3 Whisper :9001]                     │
     │             │                    │                                 │
     │             │                    ▼                                 │
     │             │             [2.4 Ollama :11434]                     │
     │             │                    │                                 │
     │             │                    ▼  upload to MinIO:output/        │
     │             │             [2.5 Stage Callback] ──HTTP──► [1.4 DAG Callback :8080/internal]
     │             └────────────────────────────────────────────────────┘
     │
     │  presigned GET
     └──────────────────► [MinIO: output/, clips/] ◄── direct browser download
```

---

## Data Flows Summary

| ID | From | To | Data | Protocol | Encrypted |
|---|---|---|---|---|---|
| F1 | Browser | MinIO input/ | Raw audio/video | HTTPS (presigned) | No (localhost) |
| F2 | API | MinIO input/ | Metadata (head_object) | HTTP (internal) | No |
| F3 | API | MinIO processing/ | Copy of input file | HTTP (internal) | No |
| F4 | API | PostgreSQL | Job creation, status updates | TCP (internal) | No |
| F5 | API | Redis | XADD job message (job_id, stage_plan, path) | TCP (internal) | No |
| F6 | Worker | Redis | XREADGROUP | TCP (host→docker) | No |
| F7 | Worker | MinIO processing/ | Download audio file | HTTP | No |
| F8 | Worker | Whisper :9001 | Audio bytes | HTTP | No |
| F9 | Worker | Ollama :11434 | Transcript text | HTTP | No |
| F10 | Worker | MinIO output/ | SRT, summary, segments | HTTP | No |
| F11 | Worker | API /internal/stage-callback | Stage result | HTTP | No |
| F12 | Browser | API | Job queries, correction PATCH | HTTP | No |
| F13 | Browser | MinIO clips/ | Segment audio (presigned GET) | HTTP | No |
| F14 | API | Webhook URL | Job completion notification | HTTPS (external) | Yes (external) |
| F15 | Filesystem | Watcher | New files in workspace/1_input/ | inotify/poll | N/A |

---

## Key Observations

1. **All internal communication is unencrypted** — HTTP only within the host and Docker network.
2. **Redis has no password** — unauthenticated on port 6379.
3. **F11 (stage-callback)** flows from Worker (host) to API (Docker) on the **public port 8080**, not an isolated internal port.
4. **F1 (file upload)** goes directly from browser to MinIO, bypassing API content inspection.
5. **MinIO CORS is `*`** — presigned URLs usable from any web origin.
