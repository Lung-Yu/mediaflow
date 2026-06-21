import { describe, it, expect, vi, beforeEach } from 'vitest'

// stub global fetch
const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

// stub window.location so URL() works in jsdom
Object.defineProperty(window, 'location', {
  value: { origin: 'http://localhost:3000' },
  writable: true,
})

describe('api client', () => {
  beforeEach(() => mockFetch.mockReset())

  it('getStatus calls /api/status/', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ processing: [], queue: [], recent: [], failed: [] }),
    })
    const { api } = await import('./client')
    const result = await api.getStatus()
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/status/'),
    )
    expect(result.processing).toEqual([])
  })

  it('saveSrt sends PUT with content', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ saved: true, bytes: 5 }) })
    const { api } = await import('./client')
    await api.saveSrt('lesson01', 'hello')
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/files/lesson01/srt'),
      expect.objectContaining({ method: 'PUT' }),
    )
  })

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 404 })
    const { api } = await import('./client')
    await expect(api.getFiles()).rejects.toThrow('404')
  })
})
