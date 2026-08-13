import { afterEach, describe, expect, it, vi } from 'vitest';

import { listCampaigns } from './api';

describe('D&D gateway client', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('includes the opaque gateway session cookie on every request', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ data: [], meta: { schema_version: 1 } }),
    });
    vi.stubGlobal('window', {
      setTimeout: globalThis.setTimeout,
      clearTimeout: globalThis.clearTimeout,
    });
    vi.stubGlobal('fetch', fetchMock);

    await listCampaigns();

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8766/api/campaigns',
      expect.objectContaining({ credentials: 'include' }),
    );
  });
});
