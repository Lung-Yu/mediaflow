# ASR 架構評估與演進紀錄

**最後更新：** 2026-06-28
**Branch：** `experiment/asr-qwen2audio-mlx`

---

## 一、問題背景

原有架構使用 `mlx-community/whisper-medium-mlx`（port 9001）做中文 ASR。
評估目標：是否有品質更好、速度更快的替代方案。

---

## 二、實驗過程與結論

### Phase 3-A：Qwen2-Audio-7B-Instruct fp16 ❌

**方式：** HuggingFace safetensors fp16，`device_map="auto"`，PyTorch + MPS。

**失敗原因（三個，缺一不可）：**

| 問題 | 細節 |
|------|------|
| Disk offload | 14 GB 超出 16 GB 統一記憶體，`device_map="auto"` 把部分 layer 放 SSD |
| 速度不可接受 | 每個 30s chunk 需 7-11 分鐘，24 分鐘音訊估計 6-9 小時 |
| MPS 不穩定 | `Tensor on device mps:0 is not on the expected device meta!` 每 6-15 chunk 崩一次 |
| 輸出格式錯誤 | 加前綴「好的，以下是音頻的转录内容：」，輸出簡體中文 |

**根本原因：** safetensors/fp16 是「原料格式」，未針對 Apple Silicon 統一記憶體優化。
在 16 GB 機器上，14 GB 模型必然觸發 disk offload，MPS + disk offload 組合不穩定。

**放棄 GGUF 路徑的原因：** llama.cpp 對 Qwen2-Audio 有文件記錄的 "poor results"（issue #13759），
且無 `/v1/audio/transcriptions` endpoint，整合複雜度高。

---

### Phase 3-B：Qwen3-ASR-0.6B-bf16 via qwen3-asr-mlx ⚠️ 部分採用

**方式：** `pip install qwen3-asr-mlx`（無 PyTorch），`mlx-community/Qwen3-ASR-0.6B-bf16`（1.46 GB）。

**效能實測（2026-06-28，策略會議_day4_技術開發一部_瑜尾.m4a）：**

| 指標 | 結果 |
|------|------|
| 模型載入（cache 後） | 0.7s |
| 2 分鐘音訊轉錄 | 7.7s（RTF ≈ 0.064） |
| MPS 崩潰 | 無（純 MLX） |
| RAM 使用 | ~1.5 GB（全進記憶體） |
| 繁體中文 | 需 OpenCC s2twp 後處理 |

**與 Whisper-medium 的品質比對（前 3 分鐘）：**

| 維度 | Whisper-medium | Qwen3-ASR-0.6B |
|------|----------------|----------------|
| 時間戳精度 | 句子級（2-5s/段） | 固定 30s 塊 ❌ |
| 詞彙準確 | `居長`（誤） | `處長`（正確）✅ |
| 句子邊界 | 清晰，可讀 | 30s 一大塊 ❌ |
| 速度 | ~3-5 分鐘/24min | ~25 秒/24min ✅ |

**現階段不採用原因：** 30 秒固定 chunk → SRT 無法使用（字幕每 30 秒才換一次）。
詞彙準確率有改善空間，但 timestamp 品質是關鍵阻礙。

---

## 三、Timestamp 問題分析

### 根本原因

我們用 30s numpy slice 手動切塊，每個 chunk 的時間戳只有 chunk 邊界精度。
`qwen3-asr-mlx` v0.1.1 的 `TranscriptionResult` 只有 `.text`、`.language`、`.duration`，無 word-level 時間。

### 三種改善方案評估

#### 方案 A：Overlap Chunking

```
chunk1（0-35s）
                chunk2（25-55s）← 10s 重疊
                                    chunk3（45-75s）
```

**原理：** 讓相鄰 chunk 有重疊區域，避免句子在邊界被截斷。

**問題：**
- ASR 是自迴歸的，同一段音訊在不同 context 下可能產生不同文字
- 需要去重邏輯（字串模糊比對），但邊界處的「腦補銜接」難以偵測
- 時間戳精度沒有根本改善（仍是 chunk 邊界估算）
- **結論：補丁，不治本。**

#### 方案 B：VAD-based Chunking

**原理：** 不固定 30s 切，而是在靜音點切（最低 RMS 能量的 frame）。

**關鍵發現：** `qwen3-asr-mlx` 已內建此邏輯（`_find_split_points`），
在 `_transcribe_chunked` 中當音訊長度超過 `chunk_duration`（預設 1200s）時自動啟動。
我們的手動 30s 切塊**完全繞過了**這個機制。

**改法：** 送更大的塊（3-5 分鐘）給 `model.transcribe(chunk_duration=300)`，
讓模型內部 VAD 處理切割，消除邊界截斷問題。

**問題：** 只解決邊界問題，時間戳精度仍然是 chunk 級別（300s 的 chunk）。
需要配合 Aligner 才能得到 sentence-level 時間。

#### 方案 C：ForcedAligner（根治方案）

**原理：** 兩步驟分離關切：
1. **ASR（Qwen3-ASR）**：只負責把音訊轉成正確的文字
2. **ForcedAligner**：輸入（音訊 + 文字），輸出每個 word 的精確時間

```
音訊（5 分鐘）
  ↓
Qwen3-ASR.transcribe(chunk_duration=300)  ← 內部 VAD 切，文字完整
  ↓ 完整文字（無截斷）
Qwen3-ForcedAligner.align(音訊, 文字)
  ↓ word-level: [("各", 0.31, 0.42), ("位", 0.42, 0.54), ...]
按標點合句
  ↓ sentence-level segments → SRT
```

**優點：**
- 不需要 overlap
- 不需要去重
- 時間精度 ~100ms（word-level）
- Aligner 是確定性的（不是 ASR），不會腦補

**唯一剩下要做的：** word → sentence 合句邏輯（按標點斷句，限制每段最大字數）

**已知限制：** Qwen3-ForcedAligner-0.6B 有 5 分鐘 chunk 限制，以 5 分鐘為單位處理即可。

---

## 四、決策

```
短期：繼續用 Whisper-medium（port 9001）作為生產 ASR
長期：Qwen3-ASR + ForcedAligner，等以下條件：
  (a) qwen3-asr-mlx 整合 Aligner API（或手動整合）
  (b) 確認 1.7B 模型品質是否值得 3.8 GB 記憶體
```

---

## 五、技術備忘

### qwen3-asr-mlx v0.1.1 API（已 introspect）

```python
from qwen3_asr_mlx import Qwen3ASR

model = Qwen3ASR.from_pretrained("mlx-community/Qwen3-ASR-0.6B-bf16")
result = model.transcribe(numpy_array_or_path, language="zh")
# result.text     → str（簡體，需 OpenCC 轉繁體）
# result.language → "Chinese"
# result.duration → float（秒）
# ⚠️ 無 segments/timestamps
```

**內部 VAD（未使用）：** `_find_split_points(samples, chunk_samples, search_samples)`
— 找最低 RMS frame，只在 `chunk_duration` 被超過時啟動。

### 已知不支援的格式

- `mlx-community/Qwen3-ASR-1.7B-8bit`：qwen3-asr-mlx v0.1.1 不支援量化格式
  （`ValueError: Received 394 parameters not in model: embed_tokens.biases, embed_tokens.scales...`）
- 支援格式：`-bf16`（未量化），或等 library 更新

### OpenCC 繁體轉換

```python
import opencc
cc = opencc.OpenCC("s2twp")  # Simplified → Traditional Taiwan + 台灣詞彙
text = cc.convert(simplified_text)
```

`s2twp` 比 `s2tw` 多做台灣特有詞彙替換（如：`軟件→軟體`、`文件→檔案`）。

---

## 六、ForcedAligner 研究結果（2026-06-28 sub-agent）

### 套件：`qwen-asr`（不是 `qwen3-asr-mlx`）

```bash
pip install qwen-asr
```

`qwen-asr` 是官方 Qwen 套件，同時包含 ASR + ForcedAligner，與
`qwen3-asr-mlx`（moona3k 的社群實作）是**不同套件**。

### Aligner 模型

- `mlx-community/Qwen3-ForcedAligner-0.6B-8bit`（1.27 GB，MLX 8-bit）
- `Qwen/Qwen3-ForcedAligner-0.6B`（官方原始，非 MLX）

⚠️ 8-bit 版本需確認 `qwen-asr` 是否支援量化格式（`qwen3-asr-mlx` v0.1.1 不支援，報 biases/scales 錯誤）

### Aligner API

```python
from qwen_asr import Qwen3ForcedAligner

aligner = Qwen3ForcedAligner.from_pretrained("Qwen/Qwen3-ForcedAligner-0.6B")
result = aligner.align(
    audio,      # URL | base64 | (np.ndarray, sr) tuple
    text,       # str 或 list[str]
    language    # "Chinese"
)
# 輸出：[{"text": "各", "start_time": 0.31, "end_time": 0.42}, ...]
```

5 分鐘 chunk 限制**已確認**（官方文件明確說明）。

### word → sentence 合句策略

**標點斷句（主要）：** 遇到 `。！？` 強制斷句。
**逗號（，）**：不強制斷，但配合字數上限（超過 20-22 字則斷）。
**停頓輔助：** 若相鄰 word 間距 > 0.5s 且當前 segment 夠長則斷句。
**SRT 中文上限：** 每行 20-22 個字（對應 12-15 字/秒的語速）。

**推薦邏輯（hybrid）：**
1. 先按 `。！？` 斷
2. 合併相鄰短段（若 pause < 0.5s 且合後 < 20 字）
3. 強制截斷超長段（> 30 字）

### GitHub：moona3k/mlx-qwen3-asr

`moona3k/mlx-qwen3-asr`（即 `qwen3-asr-mlx` 套件）有 native MLX Aligner，
用 O(n log n) LIS-based timestamp correction，比 PyTorch 快 2.6x。
但 v0.1.1 尚未在 pip 套件中暴露 Aligner API。

### 待釐清

- [ ] `qwen-asr` 套件的 Aligner 是否為 PyTorch-based？（需確認是否引入 PyTorch 依賴）
- [ ] `mlx-community/Qwen3-ForcedAligner-0.6B-8bit` + `qwen-asr` 是否能避免 8-bit 格式錯誤？
- [ ] `moona3k/mlx-qwen3-asr` 的 MLX native Aligner 何時會出現在 pip 版本？
