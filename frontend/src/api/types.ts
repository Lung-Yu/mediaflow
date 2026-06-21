export interface Task {
  id: string
  stem: string
  filename: string
  status: string
  current_stage: string | null
  submitted_at: number | null
  started_at: number | null
  completed_at: number | null
  duration_sec: number | null
  error_msg: string | null
  retry_count?: number
}

export interface StatusOverview {
  processing: Task[]
  queue: Task[]
  recent: Task[]
  failed: Task[]
}

export interface SrtFile {
  stem: string
  size_kb: number
  mtime: number
}

export interface Segment {
  index: number
  start: string
  end: string
  start_seconds: number
  text: string
}

export interface SpeakerData {
  speakers: string[]
  counts: Record<string, number>
  names: Record<string, string>
  has_audio: boolean
}

export interface TimelineStage {
  stage: string
  completed_at: number | null
  duration_sec: number | null
}

export interface TaskTimeline {
  stem: string
  filename: string | null
  submitted_at: number | null
  started_at: number | null
  completed_at: number | null
  total_pipeline_sec: number
  total_wall_sec: number | null
  stages: TimelineStage[]
}

export interface StatsOverview {
  total_tasks: number
  total_duration_sec: number
  success_rate: number
  speakers: { label: string; seconds: number; pct: number }[]
}

export interface Keyword {
  topic: string
  count: number
}

export interface UploadInitRequest {
  filename: string
  size_bytes: number
  content_type: string
}

export interface UploadPart {
  part_number: number
  url: string
}

export interface UploadInitResponse {
  upload_id: string
  minio_key: string
  stem: string
  part_size: number
  parts: UploadPart[]
}
