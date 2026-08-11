export type PackKind = 'core_rules' | 'addon' | 'module' | 'preset';
export type PackStatus = 'catalog' | 'stored' | 'active' | 'validated' | 'draft' | 'rejected' | string;

export interface ContentDependency {
  kind: PackKind;
  id: string;
  version: string;
  checksum: string;
  optional: boolean;
}

export interface ContentCitation {
  source_key: string;
  chunk_key: string;
  page: number | null;
  note: string;
}

export interface ContentAsset {
  asset_key: string;
  kind: string;
  name: string;
  media_type: string;
  checksum: string;
  size: number;
  alt?: string;
  license: string;
  attribution: string;
  source_refs: ContentCitation[];
  metadata: Record<string, unknown>;
}

export interface ContentChunk {
  key: string;
  ordinal: number;
  heading_path: string[];
  content_hash: string;
  start_offset: number;
  end_offset: number;
  page_start: number | null;
  page_end: number | null;
  token_count: number;
  metadata: Record<string, unknown>;
}

export interface ContentSourceSection {
  section_key?: string;
  ordinal: number;
  parent_ordinal: number | null;
  level: number;
  title: string;
  path: string[];
  content_hash: string;
  start_offset: number;
  end_offset: number;
  chunks: ContentChunk[];
}

export interface ContentSource {
  source_key: string;
  title: string;
  version: string;
  language: string;
  license: string;
  attribution: string;
  normalized_document_asset_key: string;
  original_asset_keys: string[];
  metadata: Record<string, unknown>;
  sections: ContentSourceSection[];
}

export interface ActorImageRef {
  asset_key: string;
  alt: string;
}

export interface ContentActor {
  schema: 'sagasmith.actor-card.v3';
  id: string;
  version: string;
  system_id: string;
  actor_type: string;
  name: string;
  player_name: string | null;
  summary: string;
  image: ActorImageRef | null;
  sheet: Record<string, unknown>;
  notes: Record<string, unknown>;
  provenance: Record<string, unknown>;
  bindings: Record<string, unknown>[];
  metadata: Record<string, unknown>;
}

export interface ContentPackageV2 {
  format: 'sagasmith.content-package';
  schema_version: 2;
  kind: PackKind;
  id: string;
  version: string;
  system_id: string;
  checksum: string;
  dependencies: ContentDependency[];
  manifest: Record<string, unknown>;
  content: Record<string, unknown>;
  sources: ContentSource[];
  assets: ContentAsset[];
  content_reviews: Record<string, unknown>[];
  actors: ContentActor[];
  metadata: Record<string, unknown>;
}

export interface CatalogEntry {
  kind: PackKind;
  id: string;
  version: string;
  checksum: string;
  title: string;
  editions: string[];
  classification?: string;
  license?: string;
  attribution?: string;
  distribution?: string;
  component_counts: Record<string, number>;
  image_count: number;
  path: string;
  download_path: string;
  archive_checksum: string;
  archive_size: number;
}

export interface ContentLibraryIndex {
  schema: 'sagasmith.content-library.v1';
  visibility: 'private' | 'public';
  system_id: string;
  package_format: 'sagasmith.content-package';
  blob_base_path: string;
  browser_asset_kinds: string[];
  packages: CatalogEntry[];
}

export interface InstalledPackSummary {
  kind: PackKind;
  id: string;
  local_ref: string;
  version: string;
  checksum: string;
  title: string;
  status: PackStatus;
  active: boolean;
  editions: string[];
  classification?: string | null;
  license?: string | null;
  dependencies: ContentDependency[];
  component_counts: Record<string, number>;
  warnings: string[];
  activation?: Record<string, unknown> | null;
}

export interface ContentInventory {
  campaign: { id: string; edition: string; phase: string };
  packs: InstalledPackSummary[];
  collections: Record<PackKind, unknown>;
}

export interface DraftJob {
  id: string;
  kind: 'rulebook' | 'module';
  state: string;
  revision: number;
  title?: string;
  source_id?: string;
  warnings?: unknown[];
  candidates?: unknown[];
  result?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface DraftInventory {
  rulebook?: { jobs?: DraftJob[] };
  module?: { jobs?: DraftJob[]; order?: string };
}

export interface GatewayMeta {
  schema_version: number;
  campaign_revision?: number;
  branch_id?: string;
  audience: string;
}

export interface GatewayResult<T> {
  data: T;
  meta: GatewayMeta;
}

export function assertLibraryIndex(value: unknown): asserts value is ContentLibraryIndex {
  const item = value as Partial<ContentLibraryIndex> | null;
  if (!item || item.schema !== 'sagasmith.content-library.v1') {
    throw new Error('unsupported content library schema');
  }
  if (item.package_format !== 'sagasmith.content-package' || !Array.isArray(item.packages)) {
    throw new Error('unsupported content library package format');
  }
}

export function assertContentPackage(value: unknown): asserts value is ContentPackageV2 {
  const item = value as Partial<ContentPackageV2> | null;
  if (!item || item.format !== 'sagasmith.content-package' || item.schema_version !== 2) {
    throw new Error('only sagasmith.content-package schema v2 is supported');
  }
  if (!['core_rules', 'addon', 'module', 'preset'].includes(String(item.kind))) {
    throw new Error('unsupported content Pack kind');
  }
  if (!Array.isArray(item.sources) || !Array.isArray(item.assets) || !Array.isArray(item.actors)) {
    throw new Error('content Pack collections are malformed');
  }
}

