import { useRef, useState } from 'react'

const ACCEPTED = new Set(['.mp4', '.m4a', '.mp3', '.wav', '.flac'])

function ext(name: string) {
  return name.slice(name.lastIndexOf('.')).toLowerCase()
}

interface Props {
  onFiles: (files: File[]) => void
}

export function DropZone({ onFiles }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handle = (files: FileList | null) => {
    if (!files) return
    const valid = Array.from(files).filter(f => ACCEPTED.has(ext(f.name)))
    if (valid.length) onFiles(valid)
  }

  return (
    <div
      className={`border-2 border-dashed rounded-lg text-center transition-colors cursor-pointer px-3 py-2.5 ${
        dragging ? 'border-purple-500 bg-purple-950/20' : 'border-neutral-700 hover:border-neutral-500'
      }`}
      onClick={() => inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={e => { e.preventDefault(); setDragging(false); handle(e.dataTransfer.files) }}
    >
      <p className="text-xs text-neutral-500">拖曳或點擊選擇音訊／視訊</p>
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
