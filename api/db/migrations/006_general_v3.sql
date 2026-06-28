-- general-v3: adds vad_trim between preprocess and transcribe
-- general-v3-large: same but uses whisper-large-v3-mlx for transcription

INSERT INTO dag_flows (id, stage_plan, is_default, deprecated, created_at)
VALUES (
    'general-v3',
    '[
        {"stage": "preprocess", "config": {}},
        {"stage": "vad_trim",   "config": {}},
        {"stage": "transcribe", "config": {}},
        {"stage": "correct_srt","config": {}},
        {"stage": "summarize",  "config": {}}
    ]',
    false, false, extract(epoch from now())
)
ON CONFLICT (id) DO UPDATE
    SET stage_plan = EXCLUDED.stage_plan, deprecated = false;

INSERT INTO dag_flows (id, stage_plan, is_default, deprecated, created_at)
VALUES (
    'general-v3-large',
    '[
        {"stage": "preprocess", "config": {}},
        {"stage": "vad_trim",   "config": {}},
        {"stage": "transcribe", "config": {"service_url": "http://localhost:9001", "model": "mlx-community/whisper-large-v3-mlx"}},
        {"stage": "correct_srt","config": {}},
        {"stage": "summarize",  "config": {}}
    ]',
    false, false, extract(epoch from now())
)
ON CONFLICT (id) DO UPDATE
    SET stage_plan = EXCLUDED.stage_plan, deprecated = false;
