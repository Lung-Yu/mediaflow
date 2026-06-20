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

#### 1-B: Breeze-7B-Instruct（聯發科台灣繁中特化）

**預期效益**：台灣口語、本地術語理解最佳，7B 大小，記憶體與 7b 相當。

HuggingFace: `MediaTek-Research/Breeze-7B-Instruct-v1_0`

切換方式（需先轉 GGUF）：
```bash
# 下載並用 llama.cpp 轉換（或找現成 GGUF）
ollama create breeze-7b -f Modelfile
# config.yaml
ollama:
  model: breeze-7b
```

> Ollama Hub 上如有現成 `mlx-community/breeze` 可直接 pull。

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

- 日期：–
- 版本：v0.2.2
- 測試音訊：–

| 指標 | 結果 |
|------|------|
| 技術詞彙辨識 | – |
| 繁體中文品質 | – |
| correct_srt 修正率 | – |
| 摘要品質 | – |
| 轉錄速度（realtime factor） | – |
| LLM 推論速度 | – |
| 記憶體峰值 | – |
| 備註 | 基準版本，未測試 |

---

### [1-A] qwen2.5:14b + whisper-medium-mlx

- 日期：–
- commit：–

| 指標 | 較基準 |
|------|--------|
| 技術詞彙辨識 | – |
| 繁體中文品質 | – |
| correct_srt 修正率 | – |
| 摘要品質 | – |
| LLM 推論速度 | – |
| 記憶體峰值 | – |
| **結論** | – |

---

### [1-B] Breeze-7B-Instruct + whisper-medium-mlx

- 日期：–
- commit：–

| 指標 | 較基準 |
|------|--------|
| 技術詞彙辨識 | – |
| 繁體中文品質 | – |
| correct_srt 修正率 | – |
| 摘要品質 | – |
| LLM 推論速度 | – |
| 記憶體峰值 | – |
| **結論** | – |

---

### [2-A] （最佳LLM）+ whisper-large-v3-turbo

- 日期：–
- commit：–

| 指標 | 較基準 |
|------|--------|
| 技術詞彙辨識 | – |
| 幻覺率 | – |
| 轉錄速度（realtime factor） | – |
| VRAM 峰值 | – |
| OOM 發生 | – |
| **結論** | – |

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
