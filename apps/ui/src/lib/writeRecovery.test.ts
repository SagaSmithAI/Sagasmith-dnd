import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PendingWrite } from './pendingWrites';

const records = vi.hoisted(() => new Map<string, PendingWrite>());
const store = vi.hoisted(() => vi.fn());
vi.mock('./pendingWrites', () => ({ pendingWriteStore: store }));
import { listPendingWrites, recoverPendingWrite, submitCombatMove, uploadContentPack } from './api';

const move = (x = 1) => submitCombatMove('campaign', 'actor', { x, y: 0 }, 5, 7);
const response = (status = 200, code?: string) => ({
  ok: status === 200, status,
  json: async () => status === 200 ? { data: { revision: 8 }, meta: { schema_version: 1 } }
    : { error: 'test failure', code },
});

describe('durable write recovery', () => {
  beforeEach(() => {
    records.clear();
    store.mockReset().mockImplementation(async (action: string, value?: PendingWrite | string) => {
      if (action === 'list') return [...records.values()];
      if (action === 'delete') records.delete(value as string);
      else {
        const record = value as PendingWrite;
        if (action === 'add' && records.has(record.id)) throw new Error('already recorded');
        records.set(record.id, structuredClone(record));
      }
      return [];
    });
    vi.stubGlobal('window', { setTimeout, clearTimeout });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('replays exactly the same payload and key after dispatch_unknown', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(504, 'dispatch_unknown'))
      .mockResolvedValue(response());
    vi.stubGlobal('fetch', fetch);
    await expect(move()).rejects.toMatchObject({ code: 'dispatch_unknown' });
    expect(await listPendingWrites()).toHaveLength(1);
    await move();
    expect(fetch.mock.calls[1][1].body).toBe(fetch.mock.calls[0][1].body);
    expect(await listPendingWrites()).toHaveLength(0);
    await move();
    expect(fetch.mock.calls[2][1].body).not.toBe(fetch.mock.calls[0][1].body);
  });

  it('keeps unknown requests through auth rejection and restores after module reload', async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new TypeError('connection lost'))
      .mockResolvedValueOnce(response(401)).mockResolvedValue(response());
    vi.stubGlobal('fetch', fetch);
    await expect(move()).rejects.toThrow('connection lost');
    await expect(move(2)).rejects.toThrow('请先恢复原请求');
    expect(fetch).toHaveBeenCalledTimes(1);
    const [record] = await listPendingWrites();
    await expect(recoverPendingWrite(record.id)).rejects.toThrow();
    expect(await listPendingWrites()).toHaveLength(1);
    vi.resetModules();
    const reloaded = await import('./api');
    await reloaded.recoverPendingWrite(record.id);
    expect(fetch.mock.calls.map(call => call[1].body)).toEqual([
      record.payload, record.payload, record.payload,
    ]);
    expect(await reloaded.listPendingWrites()).toHaveLength(0);
  });

  it('does not dispatch when durable storage fails', async () => {
    store.mockRejectedValue(new Error('storage unavailable'));
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    await expect(move()).rejects.toThrow('storage unavailable');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('releases a first request that was definitely rejected before dispatch', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(503, 'backpressure')));
    await expect(move()).rejects.toThrow();
    expect(await listPendingWrites()).toHaveLength(0);
  });

  it('retains upload bytes and the original key', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(504, 'dispatch_unknown'))
      .mockResolvedValue(response());
    vi.stubGlobal('fetch', fetch);
    await expect(uploadContentPack('campaign', 'preset', new Blob(['archive']), 'pack.sagasmith-pack'))
      .rejects.toThrow();
    const [record] = await listPendingWrites();
    await recoverPendingWrite(record.id);
    const first = fetch.mock.calls[0][1].body as FormData;
    const second = fetch.mock.calls[1][1].body as FormData;
    expect(second.get('idempotency_key')).toBe(first.get('idempotency_key'));
    expect(await (second.get('archive') as File).text()).toBe('archive');
  });
});
