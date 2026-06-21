import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import { DropZone } from '@/components/DropZone'
import { UploadProgress } from '@/components/UploadProgress'
import type { FileState } from '@/components/UploadProgress'

async function uploadFile(
  file: File,
  onProgress: (pct: number, msg: string) => void,
): Promise<void> {
  onProgress(0, '初始化中…')
  const init = await api.uploadInit({
    filename: file.name,
    size_bytes: file.size,
    content_type: file.type || 'application/octet-stream',
  })

  const completedParts: { part_number: number; etag: string }[] = []

  for (const part of init.parts) {
    const start = (part.part_number - 1) * init.part_size
    const chunk = file.slice(start, start + init.part_size)
    const pct = Math.round(((part.part_number - 1) / init.parts.length) * 90)
    onProgress(pct, `上傳中 ${part.part_number}/${init.parts.length}`)

    const res = await fetch(part.url, { method: 'PUT', body: chunk })
    if (!res.ok) throw new Error(`Part ${part.part_number} failed: ${res.status}`)
    const etag = res.headers.get('ETag') ?? res.headers.get('etag') ?? ''
    completedParts.push({ part_number: part.part_number, etag })
  }

  onProgress(90, '完成中…')
  await api.uploadComplete({ upload_id: init.upload_id, minio_key: init.minio_key, parts: completedParts })
  await api.createJob(init.minio_key)
  onProgress(100, '✓ 已加入佇列')
}

export function Upload() {
  const [queue, setQueue] = useState<FileState[]>([])
  const [running, setRunning] = useState(false)
  const navigate = useNavigate()

  const addFiles = useCallback((files: File[]) => {
    const existing = new Set(queue.map(f => f.file.name))
    const newEntries: FileState[] = files
      .filter(f => !existing.has(f.name))
      .map(f => ({ file: f, status: 'waiting', progress: 0, message: '等待中' }))
    setQueue(q => [...q, ...newEntries])
  }, [queue])

  const setFileState = (index: number, patch: Partial<FileState>) => {
    setQueue(q => q.map((f, i) => i === index ? { ...f, ...patch } : f))
  }

  const startUpload = async () => {
    setRunning(true)
    for (let i = 0; i < queue.length; i++) {
      if (queue[i].status === 'done') continue
      try {
        await uploadFile(queue[i].file, (progress, message) => {
          setFileState(i, {
            progress,
            message,
            status: progress === 100 ? 'done' : 'uploading',
          })
        })
      } catch (err) {
        setFileState(i, { status: 'error', message: `錯誤: ${String(err)}`, progress: 0 })
      }
    }
    setRunning(false)
  }

  const allDone = queue.length > 0 && queue.every(f => f.status === 'done')

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-base font-semibold text-neutral-200 mb-5">上傳音訊 / 視訊</h1>

      <DropZone onFiles={addFiles} />
      <UploadProgress files={queue} />

      {queue.length > 0 && !allDone && (
        <button
          onClick={startUpload}
          disabled={running}
          className="mt-4 w-full py-2.5 bg-purple-700 text-white rounded-lg hover:bg-purple-600 disabled:opacity-50 text-sm font-medium"
        >
          {running ? '上傳中…' : '開始上傳'}
        </button>
      )}

      {allDone && (
        <button
          onClick={() => navigate('/')}
          className="mt-4 w-full py-2.5 bg-green-800 text-green-100 rounded-lg hover:bg-green-700 text-sm font-medium"
        >
          完成 — 前往 Dashboard 查看進度
        </button>
      )}
    </div>
  )
}
