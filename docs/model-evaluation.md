# Model Evaluation — mediaflow

評估並記錄各模型在「台灣資訊業討論（電腦、開發、AI、資安）」場景下的實際表現。

---

## 評估標準

### 語音辨識（Whisper 層）

| 指標 | 說明 |
|------|------|
| **技術詞彙辨識** | CI/CD、inference、PR、zero-day、資安 等中英混用詞的正確率 |
| **人名 / 公司名** | 台灣常見公司、開源專案名稱（COSCUP、iThome、趨勢科技…） |
| **斷句品質** | 標點位置是否符合語意，影響後續 LLM 處理品質 |
| **轉錄速度** | 相對於音訊長度的實際耗時（realtime factor） |
| **記憶體峰值** | Apple Silicon VRAM 使用量，超過 ~8GB 易 OOM |
| **幻覺率** | 靜音段或低清晰度段落產生亂文的頻率 |

### LLM（Ollama 層）

| 指標 | 說明 |
|------|------|
| **繁體中文品質** | 不出現簡體字、用詞符合台灣習慣（「軟體」非「软件」） |
| **技術術語保留** | 英文技術詞彙不被誤翻或省略 |
| **correct_srt 修正率** | 同音字（的/得、在/再、以/已）修正正確率 |
| **摘要忠實度** | 摘要不新增原文沒有的資訊 |
| **推論速度** | tokens/sec，影響單次 pipeline 總耗時 |
| **記憶體用量** | 模型載入後的常駐 RAM 需求 |

---

## 試驗計畫

依風險由低到高排序，逐步執行並記錄。

### Phase 1 — LLM 升級（Ollama，幾乎零風險）

#### 1-A: qwen2.5:14b（目前基準：qwen2.5:7b）

**預期效益**：繁中理解與 correct_srt 精度提升，記憶體約 10 GB。

切換方式：
```bash
ollama pull qwen2.5:14b
# config.yaml
ollama:
  model: qwen2.5:14b
```

還原方式：`config.yaml` 改回 `qwen2.5:7b`，無需重啟服務。

#### 1-B: Llama-Breeze2-8B-Instruct（聯發科台灣繁中特化，第二代）

**預期效益**：台灣口語、本地術語理解最佳，基於 Llama 3.1 8B，記憶體約 5 GB。

HuggingFace: `mradermacher/Llama-Breeze2-8B-Instruct-text-only-i1-GGUF`（Q4_K_M）

切換方式：
```bash
curl -L "https://huggingface.co/mradermacher/Llama-Breeze2-8B-Instruct-text-only-i1-GGUF/resolve/main/Llama-Breeze2-8B-Instruct-text-only.i1-Q4_K_M.gguf" \
  -o models/llama-breeze2-8b-q4_k_m.gguf
ollama create breeze2-8b -f models/Modelfile.breeze2
# config.yaml
ollama:
  model: breeze2-8b
```

還原方式：`config.yaml` 改回 `qwen2.5:14b`。

---

### Phase 2 — Whisper 升級（OOM 風險，需監控 VRAM）

#### 2-A: whisper-large-v3-turbo（目前：whisper-medium-mlx）

**預期效益**：比 medium 明顯更準，比 large-v3 快 6x，VRAM 峰值約 6–8 GB。

切換方式：
```bash
# scripts/ctl.sh 裡的 WHISPER_MODEL 改為：
WHISPER_MODEL=mlx-community/whisper-large-v3-turbo
bash scripts/ctl.sh restart whisper
```

還原方式：
```bash
WHISPER_MODEL=mlx-community/whisper-medium-mlx
bash scripts/ctl.sh restart whisper
```

**需監控**：跑 smoke test 期間觀察 `sudo powermetrics --samplers gpu_power` 的 GPU RAM。

#### 2-B: whisper-large-v3（如 turbo 效果不足）

VRAM 峰值 ~10–12 GB，可能 OOM。先完成 2-A 評估再決定。

---

### Phase 3 — 組合最佳化

根據 Phase 1 + 2 的結果，選出最佳組合，更新 `config.yaml.example` 的預設值。

---

## 試驗記錄

每次試驗後填寫。測試音訊使用 `tests/fixtures/test-speech.m4a` 作為基準，搭配一段真實課程錄音（含技術詞彙）做對比。

---

### [基準] qwen2.5:7b + whisper-medium-mlx

- 日期：2026-06-20
- 版本：v0.2.2
- 測試音訊：`ain-tsmc-n8n-20260607-discussion01`（40 分鐘，N8n + LLM 課程規劃討論）

| 指標 | 結果 |
|------|------|
| 技術詞彙辨識 | 普通，N8n / API / LLM 均能辨識 |
| 繁體中文品質 | ✗ 差：整體摘要混入大量簡體（实用性、微调、备忘录），主題段落全簡體 |
| correct_srt 修正率 | 未單獨測量 |
| 摘要品質 | 主題段落時間排列錯亂；關鍵時刻籠統、描述薄弱 |
| 轉錄速度（realtime factor） | 未測量（Whisper 層不變） |
| LLM summarize 耗時 | ~2–3 分鐘（40 分鐘音訊） |
| 記憶體峰值 | 未測量 |
| 備註 | 主題標題全簡體為 prompt 缺少「繁體中文」指令所致；時間排列錯亂為 anchor sort 缺失 |

---

### [1-A] qwen2.5:14b + whisper-medium-mlx

- 日期：2026-06-20
- commit：（本次，含 prompt 修正 + anchor sort 修正）
- 測試音訊：`ain-tsmc-n8n-20260607-discussion01`

| 指標 | 較基準 |
|------|--------|
| 技術詞彙辨識 | 持平（Whisper 層不變） |
| 繁體中文品質（整體摘要） | ✓ 大幅提升：全繁體，詞彙更準確 |
| 繁體中文品質（主題標題） | ✓ 修正 prompt 後全繁體（課程設計與安排、互動式學習體驗…） |
| 摘要品質 | ✓ 明顯提升：摘要更詳細連貫，關鍵時刻更具體 |
| 主題段落時間順序 | ✓ 修正 anchor sort 後正確升序 |
| LLM summarize 耗時 | ~2:49（首次冷跑 ~5:43；Ollama 快取後 ~2:49） |
| 記憶體峰值 | 未測量（無 OOM 發生） |
| **結論** | ✅ 採用。繁中品質與摘要深度均明顯優於 7b；速度尚可接受 |

**附帶發現（與模型無關的 bug，已同步修正）：**
- `prompts.yaml` 三個類型的 `topics` prompt 缺少「繁體中文」指令 → 已修正
- `stages.py` topic anchor 找到後未排序 → 時間段錯亂 → 已加 `sort(key=lambda s: s["start"])`

---

### [1-B] Llama-Breeze2-8B-Instruct + whisper-medium-mlx

- 日期：2026-06-21
- 模型來源：`mradermacher/Llama-Breeze2-8B-Instruct-text-only-i1-GGUF`，Q4_K_M（4.6 GB）
- 匯入方式：`ollama create breeze2-8b -f models/Modelfile.breeze2`
- 測試音訊：`ain-tsmc-n8n-20260607-discussion01`（同 1-A）

| 指標 | 較 qwen2.5:14b |
|------|----------------|
| 繁體中文品質（整體摘要） | ✓ 全繁體，但內容偏短、細節不足 |
| 主題段落指令遵循 | ✗ 自行加上編號且順序錯亂（1,2,3,7,5） |
| 幻覺 | ✗ 出現「Pairite備案」等無意義詞 |
| 關鍵時刻格式 | △ 混入「描述：」前綴，原始 SPEAKER tag 未整理 |
| 時間段品質 | △ 出現 0 秒長度段落 |
| LLM summarize 耗時 | ~2:58（與 14b 相近） |
| 記憶體峰值 | 無 OOM（4.9 GB 模型） |
| **結論** | ❌ 不採用。指令遵循不穩、有幻覺，全面輸給 qwen2.5:14b |

**備註：**
- Breeze2 基於 Llama 3.1 8B，繁中能力理論上優秀，但在結構化輸出任務（固定格式 prompt）的指令遵循明顯弱於 Qwen2.5 架構
- config.yaml 已還原為 `qwen2.5:14b`

---

### [2-A] qwen2.5:14b + whisper-large-v3-turbo

- 日期：2026-06-21
- 模型：`mlx-community/whisper-large-v3-turbo`（mlx-whisper 0.4.3）
- 測試音訊：`tests/fixtures/test-speech.m4a`（25 秒，macOS TTS Meijia 聲音）

| 指標 | 結果 |
|------|------|
| OOM 發生 | ✅ 無 OOM（Apple Silicon 可正常執行） |
| 轉錄速度 | ✅ 約 4 秒 / 25 秒音訊（~0.16× realtime，極快） |
| beam_size > 1 | ❌ `NotImplementedError: Beam search decoder is not yet implemented` |
| condition_on_previous_text | ❌ 同樣觸發 beam search 錯誤，必須完全跳過 |
| 繁體中文（無 initial_prompt） | ❌ 輸出簡體中文 |
| 繁體中文（有 initial_prompt 補救） | △ 切換為繁體，但所有句子合成一段、標點符號爛（出現 `７` 亂碼） |
| 分段品質 | ❌ 無法正常斷句，全文一行 |
| **結論** | ❌ 不採用。mlx-whisper 0.4.3 對 turbo 架構支援不完整；品質無法接受 |

**根本原因：**
- `whisper-large-v3-turbo` 使用 4 層 decoder（vs large-v3 的 32 層），在 mlx-whisper 0.4.3 中 beam search 路徑未實作
- `condition_on_previous_text=True` 也觸發同樣問題，跳過後輸出退化
- 0.4.3 是目前最新版，無更新可升

**觀察到的 workaround（均不可接受）：**
```yaml
whisper:
  beam_size: 1
  # 仍需移除 condition_on_previous_text（在 whisper/service.py 層處理）
  initial_prompt: "以下是繁體中文的錄音內容，請使用繁體中文輸出："
```

**後續行動：**
- 監控 mlx-whisper 更新（0.5.x 或以上可能修正 turbo 支援）
- 屆時重新測試：若 beam search 和 condition_on_previous_text 正常，重跑本 Phase
- 還原：`ctl.sh` 改回 `whisper-medium-mlx`，`config.yaml` 移除 beam_size/initial_prompt

---

## 如何量化指標

### 轉錄速度（realtime factor）

```bash
# 音訊長度
ffprobe -v quiet -show_entries format=duration -of csv=p=0 audio.m4a

# watcher.log 裡的 stage 耗時
grep "stage.completed.*transcribe" data/logs/watcher.log | tail -1
```

realtime factor = 轉錄耗時 / 音訊長度，< 1 表示比實時快。

### VRAM 監控

```bash
sudo powermetrics --samplers gpu_power -i 1000 -n 30 | grep "GPU"
# 或
bash scripts/ctl.sh status  # 含 GPU 使用率（需 gpu_exporter 啟動）
```

### correct_srt 修正率（手動評估）

從 `3_output/{stem}.srt` 取 50 個句子，對比修正前後的同音字錯誤數量。
