# v2.1.0 驗證 Postmortem — 2026-06-30

端對端驗證 v2.1.0（LLMProvider + general-v3 dag_flow）時發現的問題與解法。

---

## 問題一：API 容器使用舊版 migration SQL

**現象：** `POST /jobs` 回傳 500，dag.py 找不到 default dag_flow。

**原因：** `api/db/migrations/006_general_v3.sql` 在本機已更新（含 `UPDATE ... SET is_default = false` + `INSERT ... ON CONFLICT DO UPDATE SET is_default = true`），但 Docker/Podman 容器鏡像是舊的，跑的還是沒有把 `general-v3` 設為 default 的版本。

**解法：**
```bash
podman compose build api
podman compose up -d --force-recreate api web
```

**教訓：** API 的 migration SQL 改動後，必須 rebuild 容器才生效。`ctl.sh rebuild api` 是標準操作，不能只重啟。

---

## 問題二：Python 3.9 不支援 `X | None` union type 語法

**現象：** Worker 啟動後跑第一個 job 就崩潰：
```
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
```

**原因：** `pipeline/stages.py` 裡的函式簽名用了 `LLMProvider | None`（Python 3.10+ 語法），但 host 的 Python 是 3.9.6。

**解法：** 在 `stages.py` 頂部加：
```python
from __future__ import annotations
```
這讓 annotation 在 runtime 變成字串，不再被直譯器求值。

**教訓：** `from __future__ import annotations` 是 Python 3.9 兼容 union type 的最小改動。若全面升到 3.10+ 則不需要。

---

## 問題三：`summarize()` 的 `model` 變數未定義（兩處）

**現象：** Worker 每次跑 summarize 都失敗：
```
name 'model' is not defined
```

**原因：** 從 Ollama 直接呼叫重構為 `LLMProvider` 時，`summarize()` 裡有兩個地方仍然引用裸變數 `model`（原本來自 `cfg.get("model")`），但沒有跟著更新：

- 第 858 行：markdown f-string
  ```python
  f"... ｜ 模型：{model}"
  ```
- 第 882 行：JSON dict
  ```python
  "model": model,
  ```

第一處修掉後，第二處在同一次執行中觸發，導致又多跑了幾個 retry 才找到根因。

**解法：** 兩處都改為從 provider 取得 model 名稱：
```python
getattr(provider, '_model_id', None) or getattr(provider, '_model', '')
```
`MLXLLMProvider` 用 `_model_id`，`OllamaLLMProvider` 用 `_model`。

**教訓：** 重構時 `grep -n "model" stages.py` 找出所有裸變數用法，一次修完，不要靠 runtime 錯誤逐一發現。

---

## 問題四：`frontend/src/api/client.ts` 的 `rerunTask` 缺少 body 參數

**現象：** TypeScript build 報錯：
```
Expected 3 arguments, but got 2.
```

**原因：** `json<T>(method, path, body)` 的簽名要求 3 個參數，但 `rerunTask` 呼叫時只傳了 2 個（沒有 body）：
```typescript
json<{ job_id: string; status: string }>('POST', `/jobs/${stem}/rerun`)
```

**解法：**
```typescript
json<{ job_id: string; status: string }>('POST', `/jobs/${stem}/rerun`, null)
```

**教訓：** `rerun` endpoint 不需要 body，但 `json()` helper 強制要求第三個參數，傳 `null` 即可。

---

## 操作教訓：Redis PEL 與 Worker 重啟

**現象：** Worker 重啟後，job 永遠停在 `queued`，不被撿起。

**原因：** Worker 讀 Redis stream 用 `XREADGROUP ... ">"` —— 只讀「新」訊息。舊 worker 已持有的訊息在 PEL（Pending Entry List）裡屬於死去的 consumer，新 worker 看不到。

`XCLAIM` 可以把 PEL 的訊息轉給新 consumer，但因為 worker 只讀 `>`，XCLAIM 後仍然沒用。

**正確的手動恢復流程：**
```bash
# 1. 找 pending messages
podman exec mediaflow-redis-1 redis-cli XPENDING mediaflow:jobs pipeline-workers - + 10

# 2. ACK 掉 pending（清掉 PEL）
podman exec mediaflow-redis-1 redis-cli XACK mediaflow:jobs pipeline-workers <id1> <id2> ...

# 3. 直接 XADD 新訊息讓 worker 撿起
podman exec mediaflow-redis-1 redis-cli XADD mediaflow:jobs '*' \
  job_id <job_id> \
  processing_path <path> \
  stage_plan '<json>' \
  retry_attempt <n> \
  resume_from_stage <stage>
```

**潛在改善：** Worker 啟動時先用 `XREADGROUP ... "0"` 掃一輪 pending，可自動回收自己的殘留訊息。

---

## HuggingFace 模型下載中斷的恢復

**現象：** `hf download` 卡在 0 B/s，無進度。

**原因：** 前一次下載被中斷，留下 `.incomplete` 檔案和 `.locks/` 目錄。HuggingFace Hub 的 file-locking 機制讓後續下載認為有其他 process 持有鎖。

**恢復流程：**
```bash
# 找並殺死殘留的 hf/python download process
ps aux | grep "hf download\|huggingface"
kill <PID>

# 清掉 lock 和 incomplete 檔案
find ~/.cache/huggingface/hub/models--mlx-community--Qwen2.5-14B-Instruct-4bit \
  -name "*.lock" -o -name "*.incomplete" | xargs rm -f

# 重新下載（會 resume 已完成的 shard）
source venv/bin/activate
hf download mlx-community/Qwen2.5-14B-Instruct-4bit
```

---

## 最終結果

兩個 test job 均成功完成 `general-v3` 完整流程：

```
preprocess → vad_trim → transcribe → correct_srt → summarize
```

每個 job 輸出 4 個檔案到 MinIO `output/` bucket：
- `{stem}.srt`
- `{stem}_summary.md`
- `{stem}_summary.json`
- `{stem}_segments.json`
