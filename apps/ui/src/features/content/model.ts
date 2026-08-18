import type {
  CatalogEntry,
  ContentPackageV2,
  InstalledPackSummary,
  PackKind,
} from './contracts';

export const PACK_KINDS: PackKind[] = ['core_rules', 'addon', 'module', 'preset'];

export const PACK_KIND_LABELS: Record<PackKind, string> = {
  core_rules: 'Core Rules',
  addon: 'Addon',
  module: 'Module',
  preset: 'Preset',
};

export const PACK_KIND_HELP: Record<PackKind, string> = {
  core_rules: '规则定义；导入后由战役分支显式锁定。',
  addon: '可选规则与内容；激活前检查依赖、冲突与选项。',
  module: '场景、角色、叙事、地图与结局；导入后单独激活。',
  preset: '可复用 Actor Card 库；不会作为规则或模组激活。',
};

export function catalogIdentity(entry: CatalogEntry): string {
  return `${entry.kind}:${entry.id}@${entry.version}:${entry.checksum}`;
}

export function installedIdentity(entry: InstalledPackSummary): string {
  return `${entry.kind}:${entry.id}@${entry.version}:${entry.checksum}`;
}

export function isCatalogEntryInstalled(
  entry: CatalogEntry,
  installed: InstalledPackSummary[],
): boolean {
  return installed.some((item) => (
    item.kind === entry.kind
    && item.id === entry.id
    && item.version === entry.version
    && (!item.checksum || item.checksum === entry.checksum)
  ));
}

export function packageTitle(pack: ContentPackageV2): string {
  return String(pack.manifest.title || pack.metadata.title || pack.id);
}

export function packageRecords(pack: ContentPackageV2): Array<Record<string, unknown>> {
  const tagged = (collection: string, value: unknown) => {
    if (Array.isArray(value)) {
      return value.map((item) => ({
        ...(typeof item === 'object' && item ? item as Record<string, unknown> : { value: item }),
        _collection: collection,
      }));
    }
    if (value && typeof value === 'object') {
      return Object.entries(value).map(([id, item]) => ({
        ...(typeof item === 'object' && item ? item as Record<string, unknown> : { value: item }),
        id,
        _collection: collection,
      }));
    }
    return [];
  };
  if (pack.kind === 'module') {
    const catalogs = Object.entries((pack.content.catalogs || {}) as Record<string, unknown>)
      .flatMap(([key, value]) => tagged(`catalogs.${key}`, value));
    const narrative = Object.entries((pack.content.narrative || {}) as Record<string, unknown>)
      .flatMap(([key, value]) => tagged(`narrative.${key}`, value));
    return [
      ...tagged('scene_atlas', pack.content.scene_atlas),
      ...catalogs,
      ...narrative,
      ...tagged('content_reviews', pack.content_reviews),
    ];
  }
  return [
    ...tagged('rule_definitions', pack.content.rule_definitions),
    ...tagged('artifacts', pack.content.artifacts),
    ...tagged('mechanics', pack.content.mechanics),
    ...tagged('resolutions', pack.content.resolutions),
    ...tagged('selection_rules', pack.content.selection_rules),
    ...tagged('content_reviews', pack.content_reviews),
  ];
}

export function recordTitle(record: Record<string, unknown>, index: number): string {
  const card = record.card as Record<string, unknown> | undefined;
  return String(
    card?.name
    || record.name
    || record.title
    || record.id
    || record.key
    || `Record ${index + 1}`
  );
}

export function actorChallengeRating(actor: ContentPackageV2['actors'][number]): string {
  return String(actor.provenance.challenge_rating || '—');
}

export function packOperationIdentity(pack: InstalledPackSummary): Record<string, string> {
  if (pack.kind === 'addon') return { addon_id: pack.id, version: pack.version };
  if (pack.kind === 'module') {
    return { module_id: pack.local_ref, pack_id: pack.id, version: pack.version };
  }
  if (pack.kind === 'preset') return { pack_id: pack.local_ref, version: pack.version };
  return { pack_id: pack.local_ref, version: pack.version };
}

export function extractDescriptor(value: unknown): ContentPackageV2 | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  const candidates = [item, item.package, item.content_package];
  for (const candidate of candidates) {
    if (
      candidate
      && typeof candidate === 'object'
      && (candidate as Record<string, unknown>).format === 'sagasmith.content-package'
      && (candidate as Record<string, unknown>).schema_version === 2
    ) return candidate as ContentPackageV2;
  }
  return null;
}
