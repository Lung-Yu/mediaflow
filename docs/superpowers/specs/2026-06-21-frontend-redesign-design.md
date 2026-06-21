# Frontend Redesign — Two-Column Single-Page Dashboard

**Date:** 2026-06-21
**Status:** Approved

## Context

Single-user personal tool (Mac mini). Primary workflow: drop audio → track pipeline progress → open transcript and verify against audio playback. Secondary: search old transcripts, read summaries, occasionally edit text.

AI agents consume the API directly (`/status/`, `/files/`). The UI is purely for the human operator.

## Goal

Replace the current three-route React SPA (`/`, `/transcripts`, `/transcripts/:stem`, `/upload`) with a single two-column page. The main workflow — "pick a job, start verifying" — completes without any page navigation.

---

## Layout

```
┌─ Header ─────────────────────────────────────────────────────┐
│ mediaflow          ● live          2026-06-21 14:32:01       │
└──────────────────────────────────────────────────────────────┘
┌─ 左欄 (320px) ──────┬─ 右欄 (flex-1) ──────────────────────┐
│  [上傳區]           │  [音訊播放器 sticky]                   │
│  ── Processing ──   │  filename  🔍 搜尋  [編輯] [下載]     │
│  ── Stats 摘要 ──   │  段落列表（click-to-seek，播放高亮）   │
│  ─────────────────  │  ▸ 摘要（折疊）                       │
│  🔍 搜尋逐字稿…     │  ▸ 高頻主題（折疊）                   │
│  逐字稿清單         │                                        │
│  ── Failed ──       │  （未選取：提示從左側選擇）            │
└─────────────────────┴────────────────────────────────────────┘
```

### 左欄（固定寬 320px，獨立捲動）

**固定區塊（不隨清單捲動）：**

1. **上傳區** — 精簡版 DropZone（比現在小）。拖曳或點擊選檔，支援 `.mp4 .m4a .mp3 .wav .flac`。選檔後直接開始上傳，進度顯示在對應 job 列中。
2. **Processing / Queue** — active jobs，每個顯示 filename + 當前 stage + 動態點點 pips。Queue 中的 job 顯示取消按鈕。
3. **Stats 摘要行** — 一行：`{N} 個任務  {Xh Ym}  {成功率}%`。數字，不展開。

**捲動區塊：**

4. **逐字稿搜尋欄** — 輸入即過濾清單（debounce 300ms）。
5. **Completed 清單** — 按時間倒序，顯示 filename + 日期。預設 30 筆，捲到底自動載入更多。點擊 → 右欄顯示該逐字稿。當前選中項高亮。
6. **Failed 區塊** — 顯示失敗 job，附錯誤訊息縮略 + rerun 按鈕。

### 右欄（flex-1，含內部捲動）

**未選取任何逐字稿：** 置中提示「← 從左側選擇一個逐字稿開始」

**選取後：**

1. **音訊播放器**（sticky top）— 播放/暫停、進度條 click-to-seek、時間顯示。無音訊時不顯示。
2. **標題列** — filename（左）、搜尋框（中）、`[編輯]` `[下載 SRT]` 按鈕（右）。
3. **段落列表** — 時間戳 + 文字，點擊跳播。播放中的段落以左邊框 + 背景高亮標示。搜尋時顯示 `<mark>` 高亮。
4. **摘要**（折疊，預設收合）— 純文字。
5. **高頻主題**（折疊，預設收合）— topic + count 列表。

---

## Routes

| Path | 說明 |
|------|------|
| `/` | 主頁（唯一頁面） |
| `/?stem=lesson01` | 主頁，自動選中並展開該逐字稿（方便書籤） |

React Router 保留但只有一個 route。

---

## 元件對照

| 現有元件 | 處置 | 說明 |
|---------|------|------|
| `Layout.tsx` | 保留，簡化 | 移除 nav links（Dashboard / Transcripts / Upload） |
| `AudioPlayer.tsx` | 保留 | 無變更 |
| `SrtSegmentList.tsx` | 保留 | 無變更 |
| `SrtEditor.tsx` | 保留 | 無變更 |
| `DropZone.tsx` | 保留，縮小 | 高度從 p-12 縮為 p-4，文字精簡 |
| `StatusBar.tsx` | **刪除** | 數字整合進 Stats 摘要行 |
| `TaskAccordion.tsx` | **刪除** | 細節改在右欄顯示 |
| `StatsPanel.tsx` | **刪除** | 拆成左欄 Stats 行 + 右欄 KeywordList |
| `SpeakerPanel.tsx` | **刪除** | diarize 預設關閉，幾乎無資料 |
| `TimelinePanel.tsx` | **刪除** | 各階段耗時移除 |
| `UploadProgress.tsx` | **刪除** | 進度整合進左欄 job 列 |
| `JobList.tsx` | **重寫** → `LeftPanel.tsx` | 整合 upload + processing + list + stats |
| `Dashboard.tsx` | **刪除** | |
| `Transcripts.tsx` | **刪除** | |
| `SrtViewer.tsx` | **重構** → `RightPanel.tsx` | |
| `Upload.tsx` | **刪除** | |

**新元件：**
- `LeftPanel.tsx` — 左欄全部內容
- `RightPanel.tsx` — 右欄全部內容（接收 `stem | null` prop）
- `StatsSummary.tsx` — 一行統計數字
- `TranscriptList.tsx` — 搜尋 + 無限捲動清單
- `KeywordList.tsx` — 高頻主題折疊區塊

---

## 砍掉的功能

- SpeakerPanel（說話者命名）
- TimelinePanel（各階段耗時長條圖）
- StatsPanel 說話者分佈圖
- TaskAccordion 展開的階段耗時
- `/upload`、`/transcripts` 獨立頁面

## 保留但位置改變

- 重跑（rerun）— 從 TaskAccordion 移到 Failed 項目的行內按鈕
- 上傳 — 從獨立頁面移到左欄頂部精簡 DropZone

---

## 不在範圍內

- API 變更（全部 endpoint 不動）
- 多使用者 / 權限
- 行動裝置 RWD（桌面優先）
- SpeakerPanel 相關功能（日後 diarize 開啟後可重新評估）
