import type {
  Campaign,
  Character,
  CurrentScene,
  HealthStatus,
  ModuleScene,
  ModuleSource,
  RuleSource,
  SaveSlot,
} from '../types';

export const API_BASE = (import.meta.env.PUBLIC_SAGASMITH_API_BASE || 'http://127.0.0.1:3000').replace(/\/$/, '');

async function fetchJson<T>(path: string): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 3500);
  try {
    const res = await fetch(`${API_BASE}${path}`, { signal: controller.signal });
    if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
    return await res.json() as T;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function health(): Promise<HealthStatus> { return fetchJson('/api/health'); }
export function listCampaigns(): Promise<Campaign[]> { return fetchJson('/api/campaigns'); }
export function getCampaign(id: string): Promise<Campaign> { return fetchJson(`/api/campaigns/${encodeURIComponent(id)}`); }
export function listCharacters(campaignId: string): Promise<Character[]> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/characters`); }
export function getCharacter(id: string): Promise<Character> { return fetchJson(`/api/characters/${encodeURIComponent(id)}`); }
export function listModules(campaignId: string): Promise<ModuleSource[]> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/modules`); }
export function sceneIndex(campaignId: string): Promise<ModuleScene[]> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/scenes`); }
export function currentScene(campaignId: string, scope = 'party'): Promise<CurrentScene> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/current-scene?scope=${encodeURIComponent(scope)}`); }
export function searchModules(campaignId: string, query: string, limit = 8) { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/search?query=${encodeURIComponent(query)}&limit=${limit}`); }
export function listRules(systemId = 'dnd5e'): Promise<RuleSource[]> { return fetchJson(`/api/rules?system_id=${encodeURIComponent(systemId)}`); }
export function searchRules(query: string, systemId = 'dnd5e', limit = 8) { return fetchJson<any>(`/api/rules/search?system_id=${encodeURIComponent(systemId)}&query=${encodeURIComponent(query)}&limit=${limit}`); }
export function listEvents(campaignId: string, limit = 50) { return fetchJson<any[]>(`/api/campaigns/${encodeURIComponent(campaignId)}/events?limit=${limit}`); }
export function listSaves(campaignId: string): Promise<SaveSlot[]> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/saves`); }
export function saveLineage(campaignId: string): Promise<SaveSlot[]> { return fetchJson(`/api/campaigns/${encodeURIComponent(campaignId)}/lineage`); }

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
  scene_id: 'scene-bell-chamber', title: '钟楼下的密室', module: '灰烬穹顶', chapter: '第三章 · 断钟', scene_type: 'exploration', visibility: 'player', page_start: 42, page_end: 45, keywords: ['钟楼', '契约', '密室'], tags: ['exploration', 'clue'], headings: ['第三章', '钟楼下的密室'], content: '断裂的铜钟悬在石室上方。西墙刻着被擦除一半的龙文契约。', scope_id: 'party', requested_scope_id: 'party', inherited_from_party: false,
  progress: { scene_id: 'scene-bell-chamber', scope_id: 'party', status: 'active', progress: 68, current_room: 'B3. 契约厅', state_version: 7, state: { discovered_clues: ['破损印记', '黄铜钥匙孔'], visited_rooms: ['B1', 'B2', 'B3'] } },
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
