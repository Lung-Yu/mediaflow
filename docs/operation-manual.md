# MediaFlow 操作手冊

音訊轉錄 + 結構化摘要 Pipeline。上傳錄音 → 自動輸出 SRT 字幕 + 繁體中文摘要。

---

## 前置條件

- Mac Apple Silicon（MPS GPU）
- Docker 已安裝並啟動
- Ollama 已安裝並啟動（`ollama serve`）

---

## 啟動 / 停止

```bash
make start    # 啟動所有服務
make stop     # 停止所有服務
make status   # 查看所有服務狀態
```

或從 `scripts/` 目錄內：

```bash
cd scripts && make start
```

### 啟動後各服務位址

| 服務 | 位址 | 說明 |
|------|------|------|
| Web UI | http://localhost:3000 | 上傳介面、字幕瀏覽 |
| API | http://localhost:8080 | REST API |
| MinIO | http://localhost:9000 | 物件儲存（mediaflow / changeme） |
| Whisper ASR | http://localhost:9001 | 語音轉文字（中文） |
| Grafana | http://localhost:3001 | 監控儀表板 |
| Ollama | http://localhost:11434 | LLM（qwen2.5:14b） |

可選服務（手動啟動）：

```bash
make start-diarize   # 說話者分離 :9003
make start-asr       # Qwen2-Audio ASR :9004（Whisper 替代方案，需下載 ~14GB 模型）
```

---

## 提交錄音（三種方式）

### 方式 1：拖放檔案（最簡單）

直接將 `.m4a` / `.mp3` / `.wav` 放入：

```
workspace/1_input/
```

Watcher 會自動偵測並送出。

### 方式 2：API 上傳（大檔案 / 程式呼叫）

**Step 1 — 取得 presigned URL**

```bash
curl -s -X POST http://localhost:8080/upload/init \
  -H "Content-Type: application/json" \
  -d '{"filename":"meeting.m4a","size_bytes":5242880}' | tee /tmp/init.json
```

回傳：

```json
{
  "upload_id": "xxx",
  "minio_key": "input/meeting/meeting.m4a",
  "presigned_url": "http://localhost:9000/...",
  "part_urls": ["http://localhost:9000/...?partNumber=1&..."]
}
```

**Step 2 — 上傳檔案（單 part，< 5 GB）**

```bash
ETAG=$(curl -s -X PUT "$(jq -r '.part_urls[0]' /tmp/init.json)" \
  --data-binary @meeting.m4a -D - | grep -i etag | awk '{print $2}' | tr -d '\r')

curl -s -X POST http://localhost:8080/upload/complete \
  -H "Content-Type: application/json" \
  -d "{
    \"upload_id\": \"$(jq -r .upload_id /tmp/init.json)\",
    \"minio_key\": \"$(jq -r .minio_key /tmp/init.json)\",
    \"parts\": [{\"part_number\": 1, \"etag\": \"$ETAG\"}]
  }"
```

回傳 `job_id`。

### 方式 3：直接建立 Job（檔案已在 MinIO）

```bash
curl -s -X POST http://localhost:8080/jobs \
  -H "Content-Type: application/json" \
  -d '{"file_key":"input/meeting/meeting.m4a","filename":"meeting.m4a","submitted_by":"agent-1"}'
```

---

## 查詢狀態

```bash
# 所有 job 概覽
curl -s http://localhost:8080/jobs | python3 -m json.tool

# 單一 job
curl -s http://localhost:8080/jobs/{job_id} | python3 -m json.tool

# 儀表板（processing / queue / recent）
curl -s http://localhost:8080/status/ | python3 -m json.tool
```

### Job 狀態流程

```
queued → processing → completed
                   ↘ failed（最多重試 3 次）
```

### 處理階段（stages）

| 階段 | 說明 |
|------|------|
| `preprocess` | FFmpeg 轉換為 16kHz WAV |
| `transcribe` | Whisper → SRT 字幕 |
| `summarize` | Ollama → 結構化摘要 |

---

## 取得輸出

Job 完成後，透過 `stem`（job 的 `stem` 欄位）取得：

```bash
JOB=$(curl -s http://localhost:8080/jobs/{job_id})
STEM=$(echo $JOB | python3 -c "import json,sys; print(json.load(sys.stdin)['stem'])")

# SRT 字幕
curl -s "http://localhost:8080/files/$STEM/srt"

# 結構化摘要（JSON）
curl -s "http://localhost:8080/files/$STEM/summary"

# 段落音訊（index 從 0 開始）
curl -s "http://localhost:8080/jobs/{job_id}/segment/0/audio" -o seg0.wav

# 所有可用檔案
curl -s http://localhost:8080/files/
```

### 摘要格式（summary JSON）

```json
{
  "title": "...",
  "date": "...",
  "participants": ["..."],
  "agenda": ["..."],
  "decisions": ["..."],
  "action_items": [{"owner":"...","task":"...","deadline":"..."}],
  "key_points": ["..."]
}
```

---

## 健康檢查

```bash
curl http://localhost:8080/health
# {"status":"ok"}
```

---

## 常用維運指令

```bash
make logs-worker    # 查看 pipeline 處理 log
make logs-api       # 查看 API log
make restart-worker # 更新 config 後重啟 worker
make status         # 全服務狀態一覽
```

---

## 切換 ASR 模型

預設使用 Whisper（:9001）。切換至 Qwen2-Audio（品質更高，較慢）：

1. `make start-asr`（首次下載 ~14 GB，需等待）
2. 編輯 `config.yaml`：
   ```yaml
   whisper:
     service_url: http://localhost:9004
   ```
3. `make restart-worker`

切回 Whisper：改回 `http://localhost:9001`，再 `make restart-worker`。

---

## 重新執行 Job

```bash
curl -s -X POST http://localhost:8080/jobs/{job_id}/rerun
```

---

## 刪除 Job

```bash
curl -s -X DELETE http://localhost:8080/jobs/{job_id}
```

---

## 修正字幕（人工校對後送回）

```bash
# 送出修正後的 SRT
curl -s -X PATCH http://localhost:8080/jobs/{job_id}/correction \
  -H "Content-Type: application/json" \
  -d '{"srt_content": "1\n00:00:01,000 --> 00:00:05,000\n修正後的文字\n\n"}'

# 確認完成
curl -s -X POST http://localhost:8080/jobs/{job_id}/correction/finalize
```

---

## 故障排除

| 症狀 | 處理 |
|------|------|
| Job 卡在 `queued` | `make logs-worker` 查看錯誤；`make restart-worker` |
| Whisper 無回應 | `make status` 確認 `:9001`；`make restart-whisper` |
| API 無回應 | `make logs-api`；`make restart-api` |
| Worker OOM | 降低 `config.yaml` 的 `max_concurrent_jobs`（目前：2） |
| 全部重置 | `make stop && make start` |
