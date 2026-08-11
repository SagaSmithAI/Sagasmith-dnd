import { describe, expect, it } from 'vitest';
import type { CatalogEntry, ContentPackageV2, InstalledPackSummary } from './contracts';
import {
  extractDescriptor,
  isCatalogEntryInstalled,
  packOperationIdentity,
  packageRecords,
} from './model';

const installed: InstalledPackSummary = {
  kind: 'preset',
  id: 'example.presets',
  local_ref: 'example.presets.actors',
  version: '1.0.0',
  checksum: 'abc',
  title: 'Example',
  status: 'stored',
  active: false,
  editions: ['2024'],
  dependencies: [],
  component_counts: {},
  warnings: [],
};

const catalog: CatalogEntry = {
  kind: 'preset',
  id: 'example.presets',
  version: '1.0.0',
  checksum: 'abc',
  title: 'Example',
  editions: ['2024'],
  component_counts: {},
  image_count: 0,
  path: 'packages/example.json',
  download_path: 'packages/example.sagasmith-pack',
  archive_checksum: 'def',
  archive_size: 100,
};

const descriptor = {
  format: 'sagasmith.content-package',
  schema_version: 2,
  kind: 'module',
  id: 'example.module',
  version: '1.0.0',
  system_id: 'dnd5e',
  checksum: '123',
  dependencies: [],
  manifest: {},
  content: {
    scene_atlas: [{ id: 'scene-1', title: 'Arrival' }],
    catalogs: { handouts: [{ id: 'handout-1' }] },
    narrative: { endings: [{ id: 'ending-1' }] },
  },
  sources: [],
  assets: [],
  content_reviews: [{ id: 'review-1' }],
  actors: [],
  metadata: {},
} satisfies ContentPackageV2;

describe('Content Pack view model', () => {
  it('matches a catalog entry only to its exact immutable version', () => {
    expect(isCatalogEntryInstalled(catalog, [installed])).toBe(true);
    expect(isCatalogEntryInstalled({ ...catalog, checksum: 'changed' }, [installed])).toBe(false);
  });

  it('uses the installed actor catalog ref for preset mutations', () => {
    expect(packOperationIdentity(installed)).toEqual({
      pack_id: 'example.presets.actors',
      version: '1.0.0',
    });
  });

  it('projects module-specific schema-v2 collections without guessing fields', () => {
    expect(packageRecords(descriptor).map((item) => item._collection)).toEqual([
      'scene_atlas',
      'catalogs.handouts',
      'narrative.endings',
      'content_reviews',
    ]);
  });

  it('extracts only a schema-v2 descriptor from gateway wrappers', () => {
    expect(extractDescriptor({ content_package: descriptor })).toBe(descriptor);
    expect(extractDescriptor({ package: { schema_version: 1 } })).toBeNull();
  });
});
