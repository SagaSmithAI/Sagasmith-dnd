import { afterEach, describe, expect, it, vi } from 'vitest';

import { GatewayRequestError, listCampaigns } from './api';

describe('D&D gateway client', () => {
  it('preserves exact runtime recovery data through an HTTP rejection', async () => {
    const problem = {
      error: 'stale revision',
      structured_content: { error: {
        code: 'revision_conflict', retryable: false,
        recovery: { action: 'read_current_state', idempotency_key: 'original-key' },
      } },
      tool_result: { isError: true, content: [{ type: 'text', text: 'stale revision' }] },
    };
    vi.stubGlobal('window', globalThis);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => problem }));
    const error = await listCampaigns().catch((value: unknown) => value);
    expect(error).toBeInstanceOf(GatewayRequestError);
    expect(error).toMatchObject({
      code: 'revision_conflict', retryable: false, problem,
      recovery: problem.structured_content.error.recovery,
    });
  });
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
