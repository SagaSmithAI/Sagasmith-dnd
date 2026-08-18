import { describe, expect, it } from 'vitest';
import { assertContentPackage, assertLibraryIndex } from './contracts';

describe('Content Pack contracts', () => {
  it('rejects legacy descriptors', () => {
    expect(() => assertContentPackage({ format: 'sagasmith.content-package', schema_version: 1 }))
      .toThrow(/schema v2/);
  });

  it('rejects a catalog that does not declare the unified package format', () => {
    expect(() => assertLibraryIndex({
      schema: 'sagasmith.content-library.v1',
      package_format: 'legacy',
      packages: [],
    })).toThrow(/package format/);
  });
});
