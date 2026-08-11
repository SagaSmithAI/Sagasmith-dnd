import type {
  Campaign,
  CombatStatus,
  Character,
  CurrentScene,
  HealthStatus,
  ModuleScene,
  ModuleSource,
  RuleSource,
  SaveSlot,
  SceneProgress,
} from '../types';
import type {
  ContentInventory,
  DraftInventory,
  GatewayResult,
  InstalledPackSummary,
  PackKind,
} from '../features/content/contracts';

export const API_BASE = (import.meta.env.PUBLIC_SAGASMITH_API_BASE || 'http://127.0.0.1:8766').replace(/\/$/, '');
export const PRINCIPAL_ID = import.meta.env.PUBLIC_SAGASMITH_PRINCIPAL_ID || 'system:local';
const API_TOKEN = import.meta.env.PUBLIC_SAGASMITH_API_TOKEN || '';

function requestHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  headers.set('X-SagaSmith-Principal', PRINCIPAL_ID);
  if (API_TOKEN) headers.set('Authorization', `Bearer ${API_TOKEN}`);
  return headers;
}

export class GatewayRequestError extends Error {
  status: number;
  category: 'offline' | 'unauthorized' | 'forbidden' | 'conflict' | 'not_found' | 'contract' | 'server';

  constructor(status: number, message: string) {
    super(message);
    this.name = 'GatewayRequestError';
    this.status = status;
    this.category = status === 0 ? 'offline'
      : status === 401 ? 'unauthorized'
        : status === 403 ? 'forbidden'
          : status === 404 ? 'not_found'
            : status === 409 ? 'conflict'
              : status >= 500 ? 'server' : 'contract';
  }
}

function unwrap<T>(value: T | { data: T }): T {
  return value && typeof value === 'object' && 'data' in value
    ? (value as { data: T }).data
    : value as T;
}

async function gatewayRequest<T>(path: string, init?: RequestInit, timeoutMs = 8000): Promise<GatewayResult<T>> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: requestHeaders(init?.headers),
    });
    if (!res.ok) {
      const problem = await res.json().catch(() => ({})) as { error?: string };
      throw new GatewayRequestError(res.status, problem.error || `API ${res.status}: ${res.statusText}`);
    }
    const value = await res.json() as GatewayResult<T> | T;
    if (value && typeof value === 'object' && 'data' in value && 'meta' in value) {
      return value as GatewayResult<T>;
    }
    return { data: value as T, meta: { schema_version: 1, audience: PRINCIPAL_ID } };
  } catch (error) {
    if (error instanceof GatewayRequestError) throw error;
    throw new GatewayRequestError(0, error instanceof Error ? error.message : String(error));
  } finally {
    window.clearTimeout(timeout);
  }
}

async function fetchJson<T>(path: string): Promise<T> {
  return (await gatewayRequest<T>(path)).data;
}

export function health(): Promise<HealthStatus> { return fetchJson('/api/health'); }
export function listCampaigns(): Promise<Campaign[]> { return fetchJson('/api/campaigns'); }
export function getCampaign(id: string): Promise<Campaign> { return fetchJson(`/api/campaigns/${encodeURIComponent(id)}`); }
export function listCharacters(campaignId: string): Promise<Character[]> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/characters`); }
export function getCharacter(id: string): Promise<Character> { return fetchJson(`/api/characters/${encodeURIComponent(id)}`); }
export function listModules(campaignId: string): Promise<ModuleSource[]> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/modules`); }
export function sceneIndex(campaignId: string): Promise<ModuleScene[]> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/scenes`); }
export function sceneProgress(campaignId: string, scope = 'party'): Promise<SceneProgress[]> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/scene-progress?scope=${encodeURIComponent(scope)}`); }
export function currentScene(campaignId: string, scope = 'party'): Promise<CurrentScene> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/current-scene?scope=${encodeURIComponent(scope)}`); }
export function searchModules(campaignId: string, query: string, limit = 8) { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/search?query=${encodeURIComponent(query)}&limit=${limit}`); }
export function listRules(campaignId: string, packId?: string): Promise<RuleSource[]> {
  const query = new URLSearchParams({ campaign_id: campaignId });
  if (packId) query.set('pack_id', packId);
  return fetchJson(`/api/rules?${query}`);
}
export function searchRules(queryText: string, campaignId: string, limit = 8, filters?: { edition?: string; locale?: string }) {
  const query = new URLSearchParams({ campaign_id: campaignId, query: queryText, limit: String(limit) });
  if (filters?.edition) query.set('edition', filters.edition);
  if (filters?.locale) query.set('locale', filters.locale);
  return fetchJson<any>(`/api/rules/search?${query}`);
}
export function listEvents(campaignId: string, limit = 50) { return fetchJson<any[]>(`/api/campaigns/${encodeURIComponent(campaignId)}/events?limit=${limit}`); }
export function listSaves(campaignId: string): Promise<SaveSlot[]> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/saves`); }
export function saveLineage(campaignId: string): Promise<SaveSlot[]> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/lineage`); }
export function combatStatus(campaignId: string): Promise<CombatStatus> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/combat`); }

export function listContentPacks(campaignId: string, kind?: PackKind): Promise<GatewayResult<ContentInventory>> {
  const query = kind ? `?kind=${encodeURIComponent(kind)}` : '';
  return gatewayRequest(`/api/campaigns/${encodeURIComponent(campaignId)}/content-packs${query}`);
}

export function getContentPackDetail(campaignId: string, pack: InstalledPackSummary): Promise<GatewayResult<unknown>> {
  const query = new URLSearchParams({ kind: pack.kind, pack_id: pack.id });
  if (pack.version) query.set('version', pack.version);
  if (pack.local_ref) query.set('local_ref', pack.local_ref);
  const edition = pack.editions[0];
  if (edition) query.set('edition', edition);
  return gatewayRequest(`/api/campaigns/${encodeURIComponent(campaignId)}/content-packs/detail?${query}`);
}

export async function uploadContentPack(
  campaignId: string,
  kind: PackKind,
  archive: Blob,
  filename: string,
  progressRemaps?: unknown[],
): Promise<GatewayResult<unknown>> {
  const body = new FormData();
  body.set('kind', kind);
  body.set('idempotency_key', globalThis.crypto?.randomUUID?.() || `ui-import-${Date.now()}`);
  if (progressRemaps?.length) body.set('progress_remaps', JSON.stringify(progressRemaps));
  body.set('archive', archive, filename);
  return gatewayRequest(
    `/api/campaigns/${encodeURIComponent(campaignId)}/content-packs/import`,
    { method: 'POST', body },
    120000,
  );
}

export function mutateContentPack(
  campaignId: string,
  input: Record<string, unknown> & { kind: PackKind; action: 'activate' | 'deactivate' | 'remove' | 'export' },
): Promise<GatewayResult<unknown>> {
  return gatewayRequest(`/api/campaigns/${encodeURIComponent(campaignId)}/content-packs/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...input,
      idempotency_key: input.idempotency_key || globalThis.crypto?.randomUUID?.() || `ui-pack-${Date.now()}`,
    }),
  }, 120000);
}

export function createActorFromPreset(
  campaignId: string,
  artifactId: string,
  name?: string,
): Promise<GatewayResult<{ character: Character }>> {
  return gatewayRequest(`/api/campaigns/${encodeURIComponent(campaignId)}/actors/from-preset`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      artifact_id: artifactId,
      name: name || undefined,
      idempotency_key: globalThis.crypto?.randomUUID?.() || `ui-actor-${Date.now()}`,
    }),
  });
}

export function getRuleContext(campaignId: string): Promise<GatewayResult<Record<string, unknown>>> {
  return gatewayRequest(`/api/campaigns/${encodeURIComponent(campaignId)}/rule-context`);
}

export function listDrafts(campaignId: string): Promise<GatewayResult<DraftInventory>> {
  return gatewayRequest(`/api/campaigns/${encodeURIComponent(campaignId)}/drafts`);
}

export async function combatRender(
  campaignId: string,
  audienceProjection: 'caller' | 'party_public' = 'party_public',
): Promise<Blob> {
  const query = new URLSearchParams({ audience_projection: audienceProjection });
  const response = await fetch(
    `${API_BASE}/api/campaigns/${encodeURIComponent(campaignId)}/combat/render?${query}`,
    { headers: requestHeaders() },
  );
  if (!response.ok) {
    const problem = await response.json().catch(() => ({})) as { error?: string };
    throw new Error(problem.error || `Render rejected (${response.status})`);
  }
  return response.blob();
}

export async function submitCombatMove(
  campaignId: string,
  actorId: string,
  destination: { x: number; y: number },
  distance: number,
  expectedRevision: number,
  branchId?: string,
): Promise<CombatStatus> {
  const response = await fetch(`${API_BASE}/api/campaigns/${encodeURIComponent(campaignId)}/combat/move`, {
    method: 'POST',
    headers: requestHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      actor_id: actorId,
      destination,
      distance,
      expected_revision: expectedRevision,
      branch_id: branchId,
      idempotency_key: globalThis.crypto?.randomUUID?.() || `ui-${Date.now()}`,
    }),
  });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({})) as { error?: string };
    throw new Error(problem.error || `Move rejected (${response.status})`);
  }
  return unwrap(await response.json() as CombatStatus | { data: CombatStatus });
}

export function subscribeCampaign(campaignId: string, onRevision: () => void): () => void {
  const query = new URLSearchParams({ principal_id: PRINCIPAL_ID });
  if (API_TOKEN) query.set('token', API_TOKEN);
  const source = new EventSource(`${API_BASE}/api/campaigns/${encodeURIComponent(campaignId)}/stream?${query}`);
  source.addEventListener('revision', onRevision);
  return () => source.close();
}

export const SUPPORTED_SYSTEMS = ['dnd5e'] as const;
export function isApiAvailable(): Promise<boolean> { return health().then(() => true).catch(() => false); }

export const MOCK_CAMPAIGNS: Campaign[] = [
  { id: 'campaign-1', name: '灰烬穹顶', slug: 'vault-of-ash', system_id: 'dnd5e', edition: '2024', locale: 'zh', status: 'active', description: '一场围绕失落钟楼与龙裔契约的长期战役', settings: { rule_profile: '2024 + ember-codex' }, state: { game_phase: 'play' }, revision: 18 },
  { id: 'campaign-2', name: '北境无星之夜', slug: 'starless-north', system_id: 'dnd5e', edition: '2014', locale: 'zh', status: 'paused', description: '冰原调查与生存短团', settings: {}, state: { game_phase: 'lobby' }, revision: 7 },
  { id: 'campaign-3', name: 'The Brass Concord', slug: 'brass-concord', system_id: 'dnd5e', edition: '2024', locale: 'en', status: 'archived', description: 'Planar diplomacy one-shot', settings: {}, state: { game_phase: 'play' }, revision: 4 },
];

export const MOCK_CHARACTERS: Character[] = [
  { id: 'char-varis', campaign_id: 'campaign-1', name: '瓦里斯·月痕', character_type: 'pc', player_name: 'Aster', summary: '寻找失落家徽的精灵游侠', revision: 8, notes: { knowledge_count: 3 }, sheet: { class: '游侠', level: 5, race: '高等精灵', ability_scores: { str: 10, dex: 18, con: 14, int: 12, wis: 16, cha: 8 }, hp: { current: 38, max: 44 }, armor_class: 16, initiative: 4, speed: 35, proficiency_bonus: 3, skills: { perception: 6, survival: 6, stealth: 7, investigation: 4 }, spells: { '1环': '3 / 4', '2环': '2 / 2' }, equipment: ['长弓', '双短剑', '灰烬徽记'] } },
  { id: 'char-sera', campaign_id: 'campaign-1', name: '瑟拉·铜誓', character_type: 'pc', player_name: 'Mori', summary: '遵守旧誓的龙裔圣武士', revision: 6, notes: { knowledge_count: 4 }, sheet: { class: '圣武士', level: 5, race: '铜龙裔', ability_scores: { str: 18, dex: 10, con: 16, int: 8, wis: 12, cha: 16 }, hp: { current: 46, max: 49 }, armor_class: 19, initiative: 0, speed: 30, proficiency_bonus: 3, skills: { athletics: 7, persuasion: 6, insight: 4, intimidation: 6 }, spells: { '1环': '2 / 4', '2环': '2 / 2' }, equipment: ['长剑', '盾牌', '板条甲'] } },
  { id: 'char-mira', campaign_id: 'campaign-1', name: '米拉·维恩', character_type: 'npc', summary: '钟楼守钥人；仍未说明真正效忠对象', revision: 11, notes: { knowledge_count: 2, private: true }, sheet: { class: '密探', level: 4, race: '人类', ability_scores: { str: 9, dex: 16, con: 12, int: 15, wis: 14, cha: 17 }, hp: { current: 24, max: 24 }, armor_class: 14, initiative: 3, speed: 30, proficiency_bonus: 2, skills: { deception: 7, insight: 4, stealth: 5, persuasion: 5 }, equipment: ['细剑', '加密钥匙'] } },
];

export const MOCK_MODULES: ModuleSource[] = [
  { id: 'module-ash-vault', title: '灰烬穹顶 / Vault of Ash', source_key: 'vault-of-ash-v3', campaign_id: 'campaign-1', active: true, parser_profile: 'dnd5e.module.v1', warnings: [] },
];

export const MOCK_SAVES: SaveSlot[] = [
  { slot: 4, label: '钟楼下的选择', parent_slot: 3, created_at: '2026-07-14T21:44:00+08:00' },
  { slot: 3, label: '进入封锁区', parent_slot: 2, created_at: '2026-07-12T22:18:00+08:00' },
  { slot: 2, label: '与米拉结盟', parent_slot: 1, created_at: '2026-07-10T23:02:00+08:00' },
  { slot: 1, label: '战役起点', created_at: '2026-07-08T20:00:00+08:00' },
];

export const MOCK_SCENE: CurrentScene = {
  scene_id: 'scene-bell-chamber', stable_key: 'chapter-three-bell-chamber', title: '钟楼下的密室', module_id: 'module-ash-vault', module: '灰烬穹顶', chapter_id: 'chapter-three', chapter: '第三章 · 断钟', chapter_ordinal: 2, scene_ordinal: 1, scene_type: 'exploration', visibility: 'party', page_start: 42, page_end: 45, keywords: ['钟楼', '契约', '密室'], tags: ['exploration', 'clue'], headings: ['第三章', '钟楼下的密室'], content: '断裂的铜钟悬在石室上方。西墙刻着被擦除一半的龙文契约。', scope_id: 'party', requested_scope_id: 'party', inherited_from_party: false,
  spatial: { schema_version: 1, grid: { kind: 'square', cell_ft: 5 }, locations: [{ key: 'b1-entry', title: 'B1. 石阶入口', kind: 'room', dimensions_ft: { width: 20, height: 15 }, confidence: 'explicit' }, { key: 'b2-bell', title: 'B2. 断钟厅', kind: 'room', dimensions_ft: { width: 40, height: 30 }, confidence: 'explicit' }, { key: 'b3-oath', title: 'B3. 契约厅', kind: 'room', confidence: 'derived' }], connections: [{ from: 'b1-entry', to: 'b2-bell', kind: 'passage', bidirectional: true, confidence: 'explicit' }, { from: 'b2-bell', to: 'b3-oath', kind: 'door', bidirectional: true, confidence: 'derived' }] },
  progress: { scene_id: 'scene-bell-chamber', scope_id: 'party', status: 'current', percent: 68, current_room: 'B3. 契约厅', current_location_key: 'b3-oath', state_version: 7, state: { discovered_clues: ['破损印记', '黄铜钥匙孔'], visited_rooms: ['B1', 'B2', 'B3'] } },
};

export const MOCK_COMBAT: CombatStatus = {
  active: true,
  positioning_mode: 'grid',
  round: 3,
  turn_index: 1,
  current_actor_id: 'char-sera',
  campaign_revision: 18,
  branch_id: 'main',
  battle_map: { id: 'battle-map-demo', schema_version: 1, map_revision: 1, lifecycle: 'temporary', source: { scene_id: 'scene-bell-chamber', module_id: 'module-ash-vault', location_key: 'b2-bell', scene_spatial_schema: 1 }, grid: { kind: 'square', cell_ft: 5 }, bounds: { width_cells: 12, height_cells: 9 }, blocked_cells: ['5,2', '5,3', '5,4'], difficult_cells: ['3,5', '4,5', '5,5'], checksum: 'demo' },
  combatants: [
    { actor_id: 'char-varis', token_id: 'token-varis', name: '瓦里斯', initiative: 18, position: { x: 2, y: 3 }, disposition: 'friendly', hp: { current: 38, max: 44 } },
    { actor_id: 'char-sera', token_id: 'token-sera', name: '瑟拉', initiative: 15, position: { x: 3, y: 4 }, disposition: 'friendly', hp: { current: 46, max: 49 } },
    { actor_id: 'ember-construct', token_id: 'token-construct', name: '余烬构装体', initiative: 11, position: { x: 8, y: 3 }, disposition: 'hostile', hp: { current: 31, max: 52 }, conditions: ['marked'] },
    { actor_id: 'mira', token_id: 'token-mira', name: '米拉', initiative: 8, position: { x: 4, y: 6 }, disposition: 'neutral', hp: { current: 24, max: 24 } },
  ],
};

export const MOCK_RULES: RuleSource[] = [
  { id: 'core-2024', source_key: 'dnd5e.core.2024', title: 'D&D 5e 2024 Core Rule Pack', edition: '2024', locale: 'en', version: '2024.1', authority: 'core' },
  { id: 'srd-521', source_key: 'srd-5.2.1', title: 'System Reference Document 5.2.1', edition: '2024', locale: 'en', version: '5.2.1', authority: 'srd' },
  { id: 'core-2014', source_key: 'dnd5e.core.2014', title: 'D&D 5e 2014 Core Rule Pack', edition: '2014', locale: 'en', version: '2014.1', authority: 'core' },
];

export function mockCampaign(id?: string | null): Campaign { return MOCK_CAMPAIGNS.find((item) => item.id === id) || MOCK_CAMPAIGNS[0]; }
export function mockCharacter(id?: string | null): Character { return MOCK_CHARACTERS.find((item) => item.id === id) || MOCK_CHARACTERS[0]; }
export function mockCharactersFor(campaignId: string): Character[] { return campaignId === 'campaign-1' ? MOCK_CHARACTERS : []; }

export function emitRuntimeStatus(connected: boolean, version?: string) {
  window.dispatchEvent(new CustomEvent('sagasmith:runtime', { detail: { connected, version } }));
}
