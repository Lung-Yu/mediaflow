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

**Aligner chunk 限制：** Qwen3-ForcedAligner-0.6B 每次呼叫最多 5 分鐘（300s），這是硬限制。
因此切塊策略以 Aligner 的上限為準，固定 300s。ASR 不需要這麼小的塊（可吃任意長度），
只是配合 Aligner 同樣切成 300s，讓音訊和文字自然對應，無需額外對齊。

不使用 overlap：Aligner 給 word-level 時間戳，不存在 chunk 邊界的截斷問題，也不需去重。

---

## 四、決策

```
短期：繼續用 Whisper-medium（port 9001）作為生產 ASR
實驗：Qwen3-ASR + ForcedAligner on port 9004
  套件：pip install git+https://github.com/moona3k/mlx-qwen3-asr  (v0.3.5)
  ASR 模型：mlx-community/Qwen3-ASR-0.6B-bf16（已下載）
  Aligner 模型：mlx-community/Qwen3-ForcedAligner-0.6B-8bit（1.27 GB，待下載）
  RAM 估算：~2.8 GB（兩個模型同時在記憶體）

待確認後採用：
  (a) forced_aligner.py v0.3.5 API 驗證
  (b) 端對端輸出 SRT 品質 vs Whisper-medium
  (c) 確認 1.7B 模型品質是否值得 3.8 GB 記憶體
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

### 已釐清（2026-06-28 實裝確認）

**`qwen-asr` = PyTorch-only（確認排除）**

```
qwen-asr           0.0.6
torch              2.12.1
transformers       4.57.6
accelerate         1.12.0
```

`Qwen3ForcedAligner.from_pretrained` 內部呼叫 `AutoModel.from_pretrained`，標準 HuggingFace Transformers 路徑，dtype 預設 `torch.bfloat16`。完全沒有 MLX。

**`moona3k/mlx-qwen3-asr` GitHub v0.3.5 已有 MLX Aligner**

PyPI 停在 0.1.1，GitHub main 已到 v0.3.5（2026-05-16），包含 `forced_aligner.py`（439 行）。
安裝方式：`pip install git+https://github.com/moona3k/mlx-qwen3-asr`

效能：2.64x 快於官方 PyTorch 版，MAE 5.69ms，100% 文字符合率，O(n log n) LIS 演算法。

**Aligner 模型 8-bit 格式安全**

`mlx-community/Qwen3-ForcedAligner-0.6B-8bit` 使用 affine 量化（group_size: 64, bits: 8），
與造成錯誤的 `Qwen3-ASR-1.7B-8bit`（biases/scales 格式）**不同**。也有 bf16 版（1.84 GB）。

### 套件選擇決策

| 套件 | Backend | ASR | Aligner | 結論 |
|--|--|--|--|--|
| `qwen-asr` 官方 | PyTorch | ✅ | ✅ | ❌ 帶入 MPS 風險 |
| `qwen3-asr-mlx` PyPI 0.1.1 | MLX | ✅ | ❌ | ⚠️ 落後 4 個版本 |
| `moona3k/mlx-qwen3-asr` GitHub v0.3.5 | MLX | ✅ | ✅ | ✅ 採用 |

---

## 七、實測比對：Qwen3-ASR 0.6B vs Whisper-medium（2026-06-28）

**測試音訊：** 3 分鐘口語化中文會議片段（技術部門主管報告，大量填充詞：呃、啊、那）
**Qwen3 流程：** preprocess → Qwen3-ASR+Aligner → correct_srt → summarize（`general-v2-qwen3`）
**Whisper 流程：** 直接送 `/tmp/test_3min.wav`（無完整 preprocess filter chain）

### 7.1 時間戳精度

| | Whisper-medium | Qwen3-ASR 0.6B + Aligner |
|--|--|--|
| 格式 | 整秒（`00:00:30,000`） | 毫秒（`00:00:34,490`） |
| 範例 | `00:00:30,000 → 00:00:39,000` | `00:00:34,490 → 00:00:38,330` |

**勝出：** Qwen3-ASR（ForcedAligner 提供字級對齊後組句，精度遠優於 Whisper）

### 7.2 內容準確度（前半段 0:30–2:00）

| 時間 | Whisper | Qwen3 0.6B | 正確答案 |
|--|--|--|--|
| 0:30 | 各位**處長**各位**主管**大家好 | 各位**市場觀眾**大家好 | 處長主管（用戶確認）|
| 0:39 | 接下來由我來報告**技術一部**的部分 | 接下來給我來介紹**新業務的快速通路** | 技術一部 |
| 1:46 | 四個角色：PM、UI、UX、**RD**、GV | 四個角色：**青葉、尤卡、李俊和張** | 職位縮寫，非人名 |

Qwen3 0.6B 穩定把「技術一部」誤識為「新業務」，把職位縮寫（PM/RD）幻覺成中文人名。

**勝出：** Whisper

### 7.3 後半段（2:00–3:00）

- **Whisper：** 完整句子，語意清楚（"PM的部分，其實跟前面這幾天幾個禮拜有談到"）
- **Qwen3 0.6B：** 嚴重碎片化，重複填充詞（"這個這個整個工作的上面那麼畢業的部分其實"）

講者後段語速加快 + 口語填充詞密集，0.6B 參數不夠應對。

**勝出：** Whisper（明顯）

### 7.4 噪音處理

- Whisper：靜音前 30 秒自動跳過
- Qwen3：開場 10 秒轉成「嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯」（誤識環境音）

### 7.5 整體評分

| 維度 | Whisper-medium | Qwen3-ASR 0.6B |
|--|--|--|
| 時間戳精度 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 口語化中文準確度 | ⭐⭐⭐⭐ | ⭐⭐ |
| 專有名詞 / 職位縮寫 | ⭐⭐⭐⭐ | ⭐⭐ |
| 句子完整性（後段） | ⭐⭐⭐⭐ | ⭐ |
| 噪音段落處理 | ✅ 跳過 | ❌ 誤識 |

**結論：** 0.6B 的時間戳精度雖優，但識別品質不及 Whisper-medium，主因是模型容量不足以處理口語化中文。下一步：測試 1.7B-bf16（3.8 GB，參數量 2.8x）。

---

## 八、參考資料

### 套件 / 程式碼

- [moona3k/mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr) — MLX 社群實作，v0.3.5 含 `forced_aligner.py`
- [QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) — 官方 repo（PyTorch），含 `Qwen3ForcedAligner` 參考實作
- [qwen3-asr-mlx on PyPI](https://pypi.org/project/qwen3-asr-mlx/) — 停在 0.1.1，無 Aligner
- [qwen-asr on PyPI](https://pypi.org/project/qwen-asr/) — 官方套件 v0.0.6，PyTorch-only

### 模型（HuggingFace）

- [mlx-community/Qwen3-ASR-0.6B-bf16](https://huggingface.co/mlx-community/Qwen3-ASR-0.6B-bf16) — ASR 0.6B，1.46 GB，已下載
- [mlx-community/Qwen3-ASR-1.7B-bf16](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-bf16) — ASR 1.7B，3.80 GB，待評估
- [mlx-community/Qwen3-ForcedAligner-0.6B-8bit](https://huggingface.co/mlx-community/Qwen3-ForcedAligner-0.6B-8bit) — Aligner 8-bit affine，1.27 GB
- [mlx-community/Qwen3-ForcedAligner-0.6B-bf16](https://huggingface.co/mlx-community/Qwen3-ForcedAligner-0.6B-bf16) — Aligner bf16，1.84 GB
- [mlx-community/whisper-medium-mlx](https://huggingface.co/mlx-community/whisper-medium-mlx) — 現行生產 ASR

### 已知 PyTorch + MPS 問題

- [llama.cpp issue #13759](https://github.com/ggerganov/llama.cpp/issues/13759) — Qwen2-Audio GGUF "poor results" 記錄
