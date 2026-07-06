import type { Campaign, Character, ModuleScene, ModuleSource, CurrentScene, SaveSlot, RuleSource, HealthStatus } from '../types';

const API_BASE = 'http://127.0.0.1:3000';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return res.json();
}

// ── Health ──
export function health(): Promise<HealthStatus> {
  return fetchJson('/api/health');
}

// ── Campaigns ──
export function listCampaigns(): Promise<Campaign[]> {
  return fetchJson('/api/campaigns');
}

export function getCampaign(id: string): Promise<Campaign> {
  return fetchJson(`/api/campaigns/${id}`);
}

// ── Characters ──
export function listCharacters(campaignId: string): Promise<Character[]> {
  return fetchJson(`/api/campaigns/${campaignId}/characters`);
}

export function getCharacter(id: string): Promise<Character> {
  return fetchJson(`/api/characters/${id}`);
}

// ── Modules ──
export function listModules(campaignId: string): Promise<ModuleSource[]> {
  return fetchJson(`/api/campaigns/${campaignId}/modules`);
}

export function sceneIndex(campaignId: string): Promise<ModuleScene[]> {
  return fetchJson(`/api/campaigns/${campaignId}/scenes`);
}

export function currentScene(campaignId: string, scope = 'party'): Promise<CurrentScene> {
  return fetchJson(`/api/campaigns/${campaignId}/current-scene?scope=${scope}`);
}

export function searchModules(campaignId: string, query: string, limit = 8) {
  return fetchJson(`/api/campaigns/${campaignId}/search?query=${encodeURIComponent(query)}&limit=${limit}`);
}

// ── Rules ──
export function listRules(systemId = 'dnd5e'): Promise<RuleSource[]> {
  return fetchJson(`/api/rules?system_id=${systemId}`);
}

export function searchRules(query: string, systemId = 'dnd5e', limit = 8) {
  return fetchJson(`/api/rules/search?system_id=${systemId}&query=${encodeURIComponent(query)}&limit=${limit}`);
}

// ── Events ──
export function listEvents(campaignId: string, limit = 50) {
  return fetchJson(`/api/campaigns/${campaignId}/events?limit=${limit}`);
}

// ── Snapshots ──
export function listSaves(campaignId: string): Promise<SaveSlot[]> {
  return fetchJson(`/api/campaigns/${campaignId}/saves`);
}

export function saveLineage(campaignId: string): Promise<SaveSlot[]> {
  return fetchJson(`/api/campaigns/${campaignId}/lineage`);
}

// ── Supported system_ids ──
export const SUPPORTED_SYSTEMS = ['dnd5e'] as const;

// ── Mock data helpers (when API unavailable) ──
export function isApiAvailable(): Promise<boolean> {
  return health().then(() => true).catch(() => false);
}

export const MOCK_CAMPAIGNS: Campaign[] = [
  { id: 'campaign-1', name: '深渊之门', slug: 'gate-of-abyss', system_id: 'dnd5e', edition: '2024', locale: 'zh', status: 'active', description: '博德之门：坠入阿弗纳斯战役', settings: {}, state: {}, revision: 12 },
  { id: 'campaign-2', name: '冰风谷迷案', slug: 'icewind-mystery', system_id: 'dnd5e', edition: '2014', locale: 'en', status: 'active', description: 'Icewind Dale: Rime of the Frostmaiden', settings: {}, state: {}, revision: 5 },
  { id: 'campaign-3', name: '地城试炼', slug: 'dungeon-trial', system_id: 'dnd5e', edition: '2024', locale: 'zh', status: 'active', description: '自定义地城探险', settings: {}, state: {}, revision: 8 },
];
