-- 004_qwen3_flow.sql
-- Qwen3-ASR + ForcedAligner pipeline flow.
-- Uses stage-level service_url so Whisper and Qwen3-ASR can run simultaneously.
-- Excludes verify_segments (no logprob scores) and segment_audio (handled internally).
INSERT INTO dag_flows (id, stage_plan, is_default, deprecated, created_at)
VALUES (
  'general-v2-qwen3',
  '[
    {"stage":"preprocess",  "config":{"provider":"ffmpeg"}},
    {"stage":"transcribe",  "config":{"provider":"qwen3-asr","service_url":"http://localhost:9004","language":"zh"}},
    {"stage":"correct_srt", "config":{"provider":"ollama","model":"qwen2.5:14b"}},
    {"stage":"summarize",   "config":{"provider":"ollama","model":"qwen2.5:14b","recording_type":"general"}}
  ]'::jsonb,
  false, false, extract(epoch from now())
)
ON CONFLICT (id) DO NOTHING;
