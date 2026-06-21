import { useRef, useState } from 'react'

const ACCEPTED = new Set(['.mp4', '.m4a', '.mp3', '.wav', '.flac'])

function ext(name: string) {
  return name.slice(name.lastIndexOf('.')).toLowerCase()
}

interface Props {
  onFiles: (files: File[]) => void
  compact?: boolean
}

export function DropZone({ onFiles, compact = false }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handle = (files: FileList | null) => {
    if (!files) return
    const valid = Array.from(files).filter(f => ACCEPTED.has(ext(f.name)))
    if (valid.length) onFiles(valid)
  }

  return (
    <div
      className={`border-2 border-dashed rounded-lg text-center transition-colors cursor-pointer ${
        compact ? 'px-3 py-2.5' : 'p-12'
      } ${dragging ? 'border-purple-500 bg-purple-950/20' : 'border-neutral-700 hover:border-neutral-500'}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={e => { e.preventDefault(); setDragging(false); handle(e.dataTransfer.files) }}
    >
      {compact ? (
        <p className="text-xs text-neutral-500">拖曳或點擊選擇音訊／視訊</p>
      ) : (
        <>
          <div className="text-4xl mb-3 text-neutral-500">⬆</div>
          <p className="text-neutral-400 mb-2">拖曳檔案到這裡，或</p>
          <button
            type="button"
            className="px-4 py-1.5 bg-purple-700 text-white text-sm rounded hover:bg-purple-600"
          >
            選擇檔案
          </button>
          <p className="text-xs text-neutral-600 mt-3">
            支援 .mp4 .m4a .mp3 .wav .flac｜單檔上限 5 GB
          </p>
        </>
      )}
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".mp4,.m4a,.mp3,.wav,.flac"
        className="hidden"
        onChange={e => handle(e.target.files)}
      />
    </div>
  )
}
