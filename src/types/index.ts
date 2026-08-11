export interface Campaign {
  id: string;
  name: string;
  slug: string;
  system_id: string;
  edition: string;
  locale: string;
  status: string;
  description: string;
  settings: Record<string, unknown>;
  state: Record<string, unknown>;
  revision: number;
  created_at?: string;
  updated_at?: string;
}

export interface Character {
  id: string;
  campaign_id?: string;
  name: string;
  character_type: string;
  player_name?: string;
  summary: string;
  sheet: Record<string, unknown>;
  notes: Record<string, unknown>;
  revision: number;
}

export interface DndSheet {
  class?: string;
  level?: number;
  race?: string;
  alignment?: string;
  experience?: number;
  ability_scores: {
    str: number; dex: number; con: number;
    int: number; wis: number; cha: number;
  };
  hp: { current: number; max: number; temporary?: number };
  armor_class: number;
  initiative: number;
  speed: number;
  proficiency_bonus: number;
  skills: Record<string, number>;
  spells?: Record<string, number>;
  equipment?: string[];
  features?: string[];
}

export interface ModuleSource {
  id: string;
  title: string;
  source_key: string;
  campaign_id: string;
  active: boolean;
  parser_profile: string;
  warnings: string[];
}

export interface ModuleScene {
  scene_id: string;
  stable_key?: string;
  title: string;
  module_id?: string;
  module: string;
  chapter_id?: string;
  chapter: string;
  chapter_ordinal?: number;
  scene_ordinal?: number;
  scene_type: string;
  visibility: SceneVisibility;
  page_start?: number;
  page_end?: number;
  start_line?: number;
  end_line?: number;
  keywords: string[];
  tags: string[];
  clues?: Clue[];
  checks?: Check[];
  sanity?: SanityEntry[];
  subsections?: Subsection[];
  headings: string[];
  content?: string;
  spatial?: SceneSpatial;
}

export type SceneVisibility = 'keeper' | 'party' | 'public';

export interface SpatialLocation {
  key: string;
  title: string;
  kind?: string;
  line?: number;
  dimensions_ft?: { width?: number; height?: number };
  confidence?: 'explicit' | 'derived' | 'unknown' | string;
}

export interface SpatialConnection {
  from: string;
  to: string;
  kind?: string;
  label?: string;
  bidirectional?: boolean;
  confidence?: 'explicit' | 'derived' | 'unknown' | string;
}

export interface SceneSpatial {
  schema_version?: number;
  grid?: { kind?: string; cell_ft?: number };
  locations?: SpatialLocation[];
  connections?: SpatialConnection[];
}

export interface Clue {
  title: string;
  line: number;
  type: string;
}

export interface Check {
  title: string;
  line: number;
  difficulty?: string;
}

export interface SanityEntry {
  expression: string;
  success_loss: string;
  failure_loss: string;
}

export interface Subsection {
  title: string;
  line: number;
  type: string;
}

export interface SceneProgress {
  id?: string;
  scene_id: string;
  stable_key?: string;
  scope_id: string;
  requested_scope_id?: string;
  inherited_from_party?: boolean;
  status: string;
  percent: number;
  current_room?: string;
  current_location_key?: string;
  state_version: number;
  state: Record<string, unknown>;
}

export interface CurrentScene extends ModuleScene {
  scope_id: string;
  requested_scope_id: string;
  inherited_from_party: boolean;
  progress: SceneProgress;
}

export interface SaveSlot {
  slot: number;
  label: string;
  parent_slot?: number;
  created_at?: string;
}

export interface RuleSource {
  id: string;
  source_key: string;
  title: string;
  edition: string;
  locale: string;
  version: string;
  authority: string;
  status?: string;
  checksum?: string;
}

export interface RuleSection {
  id: string;
  title: string;
  path: string[];
  level: number;
  content: string;
  parent_id?: string;
}

export interface EventLog {
  id: string;
  campaign_id: string;
  type: string;
  summary: string;
  payload: Record<string, unknown>;
  created_at?: string;
}

export interface MemoryInfo {
  id: string;
  subject: string;
  content: string;
  type: string;
  revision_id: string;
}

export interface HealthStatus {
  status: string;
  version: string;
  dense: boolean;
}

export interface GridPosition { x: number; y: number }

export interface BattleMap {
  id: string;
  schema_version: number;
  map_revision?: number;
  lifecycle: 'temporary';
  source: {
    scene_id?: string;
    module_id?: string;
    location_key?: string;
    scene_spatial_schema?: number;
  };
  grid: { kind: 'square'; cell_ft: 5 };
  bounds: { width_cells: number; height_cells: number };
  blocked_cells?: string[];
  difficult_cells?: string[];
  world_patches?: Array<{ key: string; value: unknown }>;
  checksum?: string;
}

export interface CombatantView {
  actor_id: string;
  token_id?: string;
  name: string;
  initiative: number;
  position?: GridPosition | null;
  disposition?: 'friendly' | 'neutral' | 'hostile';
  reach_ft?: number;
  hp?: { current?: number; max?: number; temporary?: number };
  conditions?: string[];
}

export interface CombatStatus {
  active: boolean;
  positioning_mode: 'grid' | 'agent';
  round?: number;
  turn_index?: number;
  current_actor_id?: string;
  campaign_revision?: number;
  branch_id?: string;
  combatants: CombatantView[];
  battle_map?: BattleMap | null;
  pending_reactions?: unknown[];
}

export interface GatewayEnvelope<T> {
  data: T;
  meta: {
    schema_version: number;
    campaign_revision?: number;
    branch_id?: string;
    audience: string;
  };
}

// ── Shape map for D&D sheet fields ──

export const ABILITY_LABELS: Record<string, string> = {
  str: "力量", dex: "敏捷", con: "体质",
  int: "智力", wis: "感知", cha: "魅力",
};

export const ABILITY_NAMES_EN: Record<string, string> = {
  str: "STR", dex: "DEX", con: "CON",
  int: "INT", wis: "WIS", cha: "CHA",
};

export const SKILL_NAMES: Record<string, string> = {
  athletics: "运动",
  acrobatics: "特技",
  stealth: "隐匿",
  sleight_of_hand: "手技",
  arcana: "奥术",
  history: "历史",
  investigation: "调查",
  nature: "自然",
  religion: "宗教",
  animal_handling: "驯兽",
  insight: "洞察",
  medicine: "医疗",
  perception: "感知",
  survival: "生存",
  deception: "欺瞒",
  intimidation: "威吓",
  performance: "表演",
  persuasion: "说服",
};
