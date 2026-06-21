type Status = 'waiting' | 'uploading' | 'done' | 'error'

interface FileState {
  file: File
  status: Status
  progress: number
  message: string
}

function fmtBytes(b: number) {
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`
  if (b >= 1e6) return `${(b / 1e6).toFixed(1)} MB`
  return `${(b / 1e3).toFixed(0)} KB`
}

const STATUS_COLOR: Record<Status, string> = {
  waiting:   'text-neutral-500',
  uploading: 'text-blue-400',
  done:      'text-green-400',
  error:     'text-red-400',
}

export function UploadProgress({ files }: { files: FileState[] }) {
  if (files.length === 0) return null
  return (
    <div className="mt-4 space-y-3">
      {files.map((f, i) => (
        <div key={i} className="bg-neutral-900 border border-neutral-800 rounded-lg px-4 py-3">
          <div className="flex items-center gap-3 mb-2">
            <span className="flex-1 text-sm text-neutral-200 truncate">{f.file.name}</span>
            <span className="text-xs text-neutral-600 tabular-nums">{fmtBytes(f.file.size)}</span>
            <span className={`text-xs ${STATUS_COLOR[f.status]}`}>{f.message}</span>
          </div>
          <div className="h-1 bg-neutral-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-300 ${
                f.status === 'done' ? 'bg-green-500' : f.status === 'error' ? 'bg-red-500' : 'bg-blue-500'
              }`}
              style={{ width: `${f.progress}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

export type { FileState }
