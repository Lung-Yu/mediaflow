-- api/db/migrations/002_seed_flows.sql
INSERT INTO dag_flows (id, stage_plan, is_default, deprecated, created_at)
VALUES
(
  'general-v1',
  '[
    {"stage":"preprocess","config":{"provider":"ffmpeg"}},
    {"stage":"transcribe","config":{"provider":"mlx-whisper","language":"zh","model":"medium"}},
    {"stage":"summarize","config":{"provider":"ollama","model":"qwen2.5:7b","prompt_key":"summarize","recording_type":"general"}}
  ]'::jsonb,
  true, false, extract(epoch from now())
),
(
  'course-v1',
  '[
    {"stage":"preprocess","config":{"provider":"ffmpeg"}},
    {"stage":"transcribe","config":{"provider":"mlx-whisper","language":"zh","model":"medium"}},
    {"stage":"verify_segments","config":{"provider":"mlx-whisper","language":"zh","model":"large-v3"}},
    {"stage":"diarize","config":{"provider":"speechbrain","num_speakers":null,"speaker_format":"【{label}】"}},
    {"stage":"correct_srt","config":{"provider":"ollama","model":"qwen2.5:7b","prompt_key":"correct_srt"}},
    {"stage":"summarize","config":{"provider":"ollama","model":"qwen2.5:7b","prompt_key":"summarize","recording_type":"course"}},
    {"stage":"detect_chapters","config":{"provider":"ollama","model":"qwen2.5:7b","prompt_key":"detect_chapters","min_gap_sec":30}}
  ]'::jsonb,
  false, false, extract(epoch from now())
),
(
  'meeting-v1',
  '[
    {"stage":"preprocess","config":{"provider":"ffmpeg"}},
    {"stage":"transcribe","config":{"provider":"mlx-whisper","language":"zh","model":"medium"}},
    {"stage":"diarize","config":{"provider":"speechbrain","num_speakers":null,"speaker_format":"【{label}】"}},
    {"stage":"summarize","config":{"provider":"ollama","model":"qwen2.5:7b","prompt_key":"summarize","recording_type":"meeting"}}
  ]'::jsonb,
  false, false, extract(epoch from now())
)
ON CONFLICT (id) DO NOTHING;
