import { describe, it, expect } from 'vitest'
import type { SrtFile } from '@/api/types'

function filterFiles(files: SrtFile[], q: string): SrtFile[] {
  if (!q) return files
  const lq = q.toLowerCase()
  return files.filter(f => f.stem.toLowerCase().includes(lq))
}

const FILES: SrtFile[] = [
  { stem: 'lesson01', size_kb: 10, mtime: 1000 },
  { stem: 'meeting_2026', size_kb: 20, mtime: 2000 },
  { stem: 'podcast_ep01', size_kb: 15, mtime: 3000 },
]

describe('filterFiles', () => {
  it('returns all when query is empty', () => {
    expect(filterFiles(FILES, '')).toHaveLength(3)
  })
  it('filters by stem substring (case-insensitive)', () => {
    expect(filterFiles(FILES, 'lesson')).toEqual([FILES[0]])
    expect(filterFiles(FILES, 'MEETING')).toEqual([FILES[1]])
  })
  it('returns empty array when no match', () => {
    expect(filterFiles(FILES, 'zzz')).toHaveLength(0)
  })
})
