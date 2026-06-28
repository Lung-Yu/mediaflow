# ASR 升級實驗計劃：Qwen3-ASR-1.7B on MLX

**建立：** 2026-06-27
**Branch：** `experiment/asr-qwen2audio-mlx`
**狀態：** 計劃中，預計 2026-06-27 23:00 開始實作

---

## 背景與決策過程

### 為什麼放棄 Qwen2-Audio-7B fp16

本次實驗（2026-06-27）嘗試用 Qwen2-Audio-7B-Instruct（HuggingFace safetensors fp16 格式，14 GB）取代 Whisper-medium 做中文 ASR。結果：

| 問題 | 細節 |
|------|------|
| Disk offload | 14 GB 超出 16 GB 統一記憶體，部分 layer 放 SSD |
| 速度 | 7-11 分鐘/chunk（30s 音訊），24 分鐘音訊需 6-9 小時 |
| MPS 不穩定 | `Tensor on device mps:0 is not on the expected device meta!` 每 6-15 chunk 崩一次 |
| 輸出格式 | 加「好的，以下是音頻的轉錄內容：」前綴，輸出簡體 |

### 為什麼放棄 GGUF + llama.cpp

Sub-agent 研究結果（2026-06-27）：
- Qwen2-Audio 在 llama.cpp 有文件記錄的 "poor results"，官方 issue #13759 明確說明
- 無 `/v1/audio/transcriptions` endpoint（feature request #21852，未實作）
- 需 base64 編碼音訊送 chat/completions，手動解析輸出 → 整合複雜度高

### 為什麼選 Qwen3-ASR-1.7B + qwen3-asr-mlx

Sub-agent 研究結果（2026-06-27）：

| 指標 | Qwen2-Audio-7B fp16 | Qwen3-ASR-1.7B MLX | Whisper-medium |
|------|---------------------|---------------------|----------------|
| RAM | 14 GB（disk offload） | ~1.7 GB | ~1.5 GB |
| 24 min 音訊速度 | 6-9 小時 | **~22 秒** | ~3-5 分鐘 |
| 中文 WER | 未知 | **3.81** | ~8-12% 估計 |
| Whisper-large-v3 WER | — | 10.61 | — |
| 時間戳 | 手動分 chunk | 原生 word-level | 原生 |
| PyTorch 依賴 | ✅ 需要 | ❌ 不需要 | ✅ 需要 |

**核心洞察：** safetensors/fp16 是「原料」，未針對硬體優化。MLX 格式針對 Apple Silicon 統一記憶體架構原生設計，4-bit 量化後全進記憶體，無 disk swap。

---

## 技術規格

### 模型
- **主要目標：** `mlx-community/Qwen3-ASR-1.7B-8bit`（或 4bit，體積更小）
- **備選（更快）：** `mlx-community/Qwen3-ASR-0.6B-5bit`（速度更快但精度略低）
- **Package：** `pip install qwen3-asr-mlx`（uses mlx-audio 0.3.1 internally，無 PyTorch）

### 效能預期
- RTF 0.015（11x 實時）= 1 分鐘音訊只需 0.9 秒
- 24 分鐘音訊：51 個 30s chunks × 0.45s/chunk ≈ **23 秒**
- 仍需 chunking（為取得 start/end 時間戳），但每 chunk 速度極快

### qwen3-asr-mlx API（v0.1.1，introspect 確認）

```python
from qwen3_asr_mlx import Qwen3ASR, LANGUAGE_MAP

model = Qwen3ASR.from_pretrained("mlx-community/Qwen3-ASR-1.7B-8bit")
model.warm_up()  # optional

# 支援 str/Path/np.ndarray
result = model.transcribe(audio_array, language="zh")
# result.text     → "轉錄文字..."
# result.language → "Chinese"
# result.duration → 24.3 (seconds)
# ⚠️ result 無 segments/timestamps！

# load_audio 直接回傳 np.ndarray，無需 soundfile
from qwen3_asr_mlx import load_audio
samples = load_audio("audio.wav")  # float32, 16kHz, mono
```

**時間戳策略：** 將 numpy array 切成 30s chunks，每 chunk 分配 start/end = offset/sr。無需 temp file（model.transcribe 接受 numpy array 直接）。

### Python 環境
- **需要 Python 3.10+**（mlx-audio hard requirement）
- 機器上已有 Python 3.11.15 ✅
- 新 venv：`venv-asr-mlx`（與現有 `venv-asr` 分開，避免污染）

---

## 實作計劃

### 步驟一：環境準備（前置）

```bash
python3.11 -m venv venv-asr-mlx
venv-asr-mlx/bin/pip install qwen3-asr-mlx soundfile numpy
```

加到 `.gitignore`：`venv-asr-mlx/`

### 步驟二：新 requirements 檔

`asr/requirements-mlx.txt`:
```
qwen3-asr-mlx
soundfile
numpy
fastapi
uvicorn[standard]
```

### 步驟三：重寫 asr/service.py

保留相同 HTTP 介面（worker 不需改動）：
- `GET /health` → `{status, model, model_loaded}`
- `POST /transcribe_segments` → `{segments: [{id, start, end, text, avg_logprob, no_speech_prob}]}`
- `POST /transcribe_large` → `{text: "..."}`

關鍵變更：
- **移除**：chunking loop、batch logic、checkpoint logic（速度快到不需要）
- **保留**：介面定義、temp file pattern（mlx-audio 只接受檔案路徑）
- **新增**：qwen3-asr-mlx 載入與推理

實作邏輯（API 已 introspect 確認）：
```python
import io, os
import numpy as np
import soundfile as sf
from qwen3_asr_mlx import Qwen3ASR, load_audio

MODEL = os.environ.get("ASR_MODEL", "mlx-community/Qwen3-ASR-1.7B-8bit")
CHUNK_SEC = int(os.environ.get("ASR_CHUNK_SEC", "30"))
SR = 16000
_model = None

def _get_model():
    global _model
    if _model is None:
        _model = Qwen3ASR.from_pretrained(MODEL)
        _model.warm_up()
    return _model

def _do_transcribe(wav_bytes: bytes, language: str) -> dict:
    model = _get_model()
    # load_audio 只接受 path；用 soundfile 直接從 bytes 讀取
    audio, sr = sf.read(io.BytesIO(wav_bytes))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)
    if sr != SR:
        # ponytail: resample; add scipy if needed
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(sr, SR)
        audio = resample_poly(audio, SR // g, sr // g).astype(np.float32)

    chunk_samples = CHUNK_SEC * SR
    total = len(audio)
    lang = "zh" if language == "zh" else language  # LANGUAGE_MAP key

    segments, seg_id, offset = [], 0, 0
    while offset < total:
        chunk = audio[offset: offset + chunk_samples]
        start_sec = round(offset / SR, 3)
        end_sec = round(min((offset + chunk_samples) / SR, total / SR), 3)
        result = model.transcribe(chunk, language=lang)
        text = result.text.strip()
        if text:
            segments.append({
                "id": seg_id, "start": start_sec, "end": end_sec,
                "text": text,
                "avg_logprob": 0.0, "no_speech_prob": 0.0,
            })
            seg_id += 1
        offset += chunk_samples
        print(f"[asr] chunk {seg_id}/{(total + chunk_samples - 1)//chunk_samples}", flush=True)
    return {"segments": segments}
```

**關鍵細節：**
- `model.transcribe()` 接受 `np.ndarray`，不需要 temp file
- `soundfile.read(io.BytesIO(wav_bytes))` 從記憶體讀音訊，不寫磁碟
- `load_audio` 只接受 path，不直接用

### 步驟四：更新 ctl.sh

```bash
if [[ "$svc" == "asr" ]]; then
    _start_bg asr venv-asr-mlx/bin/uvicorn asr.service:app --host 0.0.0.0 --port 9004
fi
```

注意：不需要 `PYTORCH_ENABLE_MPS_FALLBACK=1`（無 PyTorch）

### 步驟五：快速冒煙測試

```bash
# 測試 health
curl http://localhost:9004/health

# 測試轉錄（用現有測試音訊）
curl -X POST http://localhost:9004/transcribe_segments \
  -F "audio=@workspace/1_input/test.m4a" \
  -G -d language=zh | python3 -m json.tool
```

### 步驟六：品質比對

用同一音訊（`策略會議_day4_營運系統開發處_patty.m4a`）：
- Qwen3-ASR-1.7B 輸出 vs 現有 Whisper-medium 輸出
- 評估：繁體中文準確率、專有名詞、分段合理性
- 記錄到 `project_model_evaluation.md`

---

## 已知風險與對策

| 風險 | 說明 | 對策 |
|------|------|------|
| **`result` 無 timestamps**（已確認） | `TranscriptionResult` 只有 text/language/duration | ✅ 用 chunk offset 手動分配 start/end |
| 語言輸出非繁體 | model 會自動偵測語言，`language="zh"` 是 hint 非強制 | 測試時驗證；如輸出簡體，考慮加 `initial_prompt` |
| 每 chunk 文字是完整句子還是截斷 | 30s 邊界可能切斷句子 | 可試 60-120s chunk，SRT 段落更完整；RTF 仍快 |
| soundfile 依賴 | 現有 venv-asr 有 soundfile；新 venv-asr-mlx 需加 | 加到 requirements-mlx.txt |
| 測試音訊之前轉錄失敗 | Qwen2-Audio 實驗產生了很多失敗 job | 重新 rerun 用 Qwen3-ASR 跑 |

---

## 後置工作

- 若 Qwen3-ASR 品質優於 Whisper-medium → 更新 `config.yaml.example`、`docs/operation-manual.md`
- 考慮 Qwen3-ASR-0.6B（更小更快）vs 1.7B 品質取捨
- 更新 `project_model_evaluation.md` 記錄品質比較結果
- Merge `experiment/asr-qwen2audio-mlx` → `main`（如採用）
