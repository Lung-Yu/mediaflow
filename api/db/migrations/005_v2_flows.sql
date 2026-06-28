-- 005_v2_flows.sql
-- Promote general-v2 (Whisper + correct_srt + summarize) as new default.
-- Add general-v2-polish with full-document Chinese/English polish stage.
-- Deprecate general-v1 (no correct_srt, older model refs).

UPDATE dag_flows SET is_default = false WHERE is_default = true;
UPDATE dag_flows SET deprecated = true WHERE id = 'general-v1';

INSERT INTO dag_flows (id, stage_plan, is_default, deprecated, created_at)
VALUES
(
  'general-v2',
  '[
    {"stage":"preprocess",  "config":{"provider":"ffmpeg"}},
    {"stage":"transcribe",  "config":{"provider":"mlx-whisper","language":"zh","model":"medium"}},
    {"stage":"correct_srt", "config":{"provider":"ollama","model":"qwen2.5:14b"}},
    {"stage":"summarize",   "config":{"provider":"ollama","model":"qwen2.5:14b","recording_type":"general"}}
  ]'::jsonb,
  true, false, extract(epoch from now())
),
(
  'general-v2-polish',
  '[
    {"stage":"preprocess",  "config":{"provider":"ffmpeg"}},
    {"stage":"transcribe",  "config":{"provider":"mlx-whisper","language":"zh","model":"medium"}},
    {"stage":"correct_srt", "config":{"provider":"ollama","model":"qwen2.5:14b"}},
    {"stage":"polish_srt",  "config":{"provider":"ollama","model":"qwen2.5:14b"}},
    {"stage":"summarize",   "config":{"provider":"ollama","model":"qwen2.5:14b","recording_type":"general"}}
  ]'::jsonb,
  false, false, extract(epoch from now())
)
ON CONFLICT (id) DO NOTHING;
