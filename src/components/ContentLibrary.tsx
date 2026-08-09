import { useEffect, useMemo, useState } from 'react';

type Asset = {
  asset_key: string; kind: string; name: string; media_type: string; checksum: string;
  size: number; alt?: string; license: string; attribution: string;
  metadata: Record<string, any>;
};
type Actor = {
  schema: string; id: string; version: string; actor_type: string; name: string;
  summary: string; image: { asset_key: string; alt: string } | null;
  sheet: Record<string, unknown>; notes: Record<string, unknown>;
  provenance: Record<string, unknown>; bindings: Record<string, unknown>[];
  metadata: Record<string, unknown>;
};
type Source = {
  source_key: string; title: string; normalized_document_asset_key: string;
  sections: { section_key: string; title: string; page_start?: number; page_end?: number; chunks: unknown[] }[];
};
type Entry = {
  kind: string; id: string; version: string; checksum: string; title: string;
  editions: string[]; license?: string; classification?: string; distribution?: string;
  component_counts: Record<string, number>; image_count: number; path: string;
  download_path: string; archive_checksum: string; archive_size: number;
};
type Index = {
  schema: string; visibility: 'private' | 'public'; system_id: string; package_format: string;
  blob_base_path: string; browser_asset_kinds: string[]; packages: Entry[];
};
type ContentPackage = {
  format: string; kind: string; id: string; version: string; checksum: string;
  manifest: Record<string, any>; metadata: Record<string, any>;
  content: Record<string, any>;
  actors: Actor[]; assets: Asset[]; sources: Source[]; content_reviews: Record<string, any>[];
};

const defaultUrl = import.meta.env.PUBLIC_SAGASMITH_LIBRARY_URL
  || 'https://sagasmithai.github.io/SagaSmith-dnd-content-library/content-library/index.json';

const rowsOf = (pack: ContentPackage) => {
  const reviews = pack.content_reviews.map((item) => ({ ...item, _collection: 'content_reviews' }));
  if (pack.kind === 'module') {
    const scenes = Array.isArray(pack.content.scene_atlas)
      ? pack.content.scene_atlas.map((item: any) => ({ ...item, _collection: 'scene_atlas' }))
      : [];
    const catalogs = Object.entries(pack.content.catalogs || {}).flatMap(([kind, entries]) =>
      Array.isArray(entries)
        ? entries.map((item: any) => ({ ...item, _collection: `catalogs.${kind}` }))
        : []);
    const narrative = Object.entries(pack.content.narrative || {}).flatMap(([kind, entries]) =>
      Array.isArray(entries)
        ? entries.map((item: any) => ({ ...item, _collection: `narrative.${kind}` }))
        : []);
    return [...scenes, ...catalogs, ...narrative, ...reviews];
  }
  const contentRows = ['artifacts', 'mechanics', 'rule_definitions', 'resolutions', 'selection_rules'].flatMap((key) => {
    const value = pack.content[key];
    if (Array.isArray(value)) return value.map((item) => ({ ...item, _collection: key }));
    if (value && typeof value === 'object') {
      return Object.entries(value).map(([id, item]) => ({
        ...(typeof item === 'object' && item ? item : { value: item }), id, _collection: key,
      }));
    }
    return [];
  });
  return [...contentRows, ...reviews];
};

const partyRange = (value: any) => value?.minimum == null || value?.maximum == null
  ? 'SOURCE REVIEW'
  : value.minimum === value.maximum ? String(value.minimum) : `${value.minimum}–${value.maximum}`;
const levelRange = (profile: any) => profile?.starting_level?.value == null || profile?.expected_end_level?.value == null
  ? 'SOURCE REVIEW'
  : `${profile.starting_level.value}–${profile.expected_end_level.value}`;

function SourceDocument({ sourceItem, pack, index, indexUrl }: {
  sourceItem: Source; pack: ContentPackage; index: Index; indexUrl: string;
}) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const asset = pack.assets.find((item) => item.asset_key === sourceItem.normalized_document_asset_key);
  const load = async () => {
    if (!asset || text) return;
    try {
      const response = await fetch(new URL(`${index.blob_base_path}/${asset.checksum}`, indexUrl));
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      setText(await response.text());
    } catch (reason) { setError(String(reason)); }
  };
  return <details onToggle={(event) => { if (event.currentTarget.open) void load(); }}>
    <summary>{sourceItem.title} · {sourceItem.sections.length} sections</summary>
    {error && <p className="library-error">{error}</p>}
    {text ? <pre>{text}</pre> : <p>展开后按内容哈希加载规范化原文。</p>}
  </details>;
}

export default function ContentLibrary() {
  const [source, setSource] = useState(defaultUrl);
  const [index, setIndex] = useState<Index | null>(null);
  const [selected, setSelected] = useState<Entry | null>(null);
  const [pack, setPack] = useState<ContentPackage | null>(null);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('all');
  const [error, setError] = useState('');
  useEffect(() => {
    const url = new URLSearchParams(location.search).get('source') || defaultUrl;
    setSource(url);
    fetch(url).then((response) => {
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.json();
    }).then((data: Index) => {
      if (data.schema !== 'sagasmith.content-library.v1') throw new Error('unsupported library schema');
      if (data.package_format !== 'sagasmith.content-package') throw new Error('unsupported library format');
      setIndex(data); setSelected(data.packages[0] || null);
    }).catch((reason) => setError(String(reason)));
  }, []);
  useEffect(() => {
    if (!selected) return;
    setPack(null); setError('');
    fetch(new URL(selected.path, source)).then((response) => {
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.json();
    }).then(setPack).catch((reason) => setError(String(reason)));
  }, [selected, source]);
  const visible = useMemo(() => (index?.packages || []).filter((entry) =>
    (kind === 'all' || entry.kind === kind)
    && `${entry.title} ${entry.id} ${(entry.editions || []).join(' ')} ${entry.classification || ''}`
      .toLowerCase().includes(query.toLowerCase())), [index, kind, query]);
  const assets = useMemo(() => new Map((pack?.assets || []).map((item) => [item.asset_key, item])), [pack]);
  const records = pack ? rowsOf(pack) : [];
  const visibleAssets = (pack?.assets || []).filter((asset) =>
    !['actor_image', 'normalized_document'].includes(asset.kind));
  const profile = pack?.kind === 'module' ? pack.content.play_profile : null;
  const continuity = pack?.kind === 'module' ? pack.content.continuity : null;
  return <section className="content-library">
    <header className="library-hero"><div><span>UNIFIED CONTENT / {index?.visibility?.toUpperCase() || 'READ-ONLY'}</span><h2>Core Rules, Addon, Module & Preset Library</h2><p>浏览统一、可校验、可迁移的规则、场景、证据、资产与角色卡；图片仅属于卡定义，不进入战役快照。公开目录只显示许可允许再分发的内容，本地私有目录可显示完整用户语料。</p></div><dl><div><dt>PACKAGES</dt><dd>{index?.packages.length ?? '—'}</dd></div><div><dt>ACTORS</dt><dd>{index?.packages.reduce((total, entry) => total + (entry.component_counts.actor_card || 0), 0) ?? '—'}</dd></div></dl></header>
    {error && <p className="library-error">Library unavailable: {error}</p>}
    <div className="library-controls"><input aria-label="Search packages" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search title, id, edition…"/><select aria-label="Package kind" value={kind} onChange={(event) => setKind(event.target.value)}><option value="all">All packages</option><option value="core_rules">Core rules</option><option value="addon">Addons</option><option value="module">Modules</option><option value="preset">Presets</option></select><code>{source}</code></div>
    <div className="library-layout"><nav className="library-packages" aria-label="Content packages">{visible.map((entry) => <button className={selected?.checksum === entry.checksum ? 'selected' : ''} key={entry.checksum} onClick={() => setSelected(entry)}><small>{entry.kind.toUpperCase()} · {(entry.editions || []).join('/') || 'ALL'}</small><strong>{entry.title}</strong><span>{entry.id}@{entry.version}</span><footer><b>{entry.component_counts.actor_card || Object.values(entry.component_counts).reduce((a, b) => a + b, 0)} records</b><em>{entry.license}</em></footer></button>)}</nav>
      <main className="library-detail">{pack && <><header><div><small>{pack.kind} / {pack.version}{selected?.classification ? ` / ${selected.classification}` : ''}</small><h2>{pack.manifest.title || pack.metadata.title || pack.id}</h2><p>{pack.id}</p></div><div className="checksum"><span>DESCRIPTOR SHA-256</span>{pack.checksum}<span>ARCHIVE SHA-256 · {Math.ceil((selected?.archive_size || 0) / 1024)} KiB</span>{selected?.archive_checksum}</div></header><div className="library-meta"><span>LICENSE <b>{pack.metadata.license || '—'}</b></span><span>DISTRIBUTION <b>{pack.metadata.distribution || '—'}</b></span><span>RECORDS <b>{records.length}</b></span><span>IMAGES <b>{pack.actors.filter((actor) => actor.image).length}</b></span></div>
        <section className="module-contract"><header><div><small>CONTENT PACKAGE V2</small><h3>Immutable Pack contract</h3></div><a className="module-download" href={new URL(selected?.download_path || '', source).href} download>Download .sagasmith-pack</a></header>{profile && <div className="module-stats"><span>PARTY SIZE<b>{partyRange(profile.party_size)}</b></span><span>LEVELS<b>{levelRange(profile)}</b></span><span>ADVANCEMENT<b>{profile.advancement?.recommended || 'SOURCE REVIEW'}</b></span><span>SERIES<b>{continuity?.series_id || 'STANDALONE'}</b></span></div>}</section>
        {records.length > 0 && <section className="library-component"><header><div><small>STRUCTURED CONTENT</small><h3>Rules, scenes, reviews and resolution records</h3></div><code>{records.length} records</code></header><div className="artifact-grid">{records.map((item: any, position) => {
          const image = item.card?.image;
          const imageAsset = image ? assets.get(image.asset_key) : null;
          return <article key={`${item.id || item.key || item.name}-${position}`}>
            {imageAsset && index && (
              <img
                loading="lazy"
                src={new URL(`${index.blob_base_path}/${imageAsset.checksum}`, source).href}
                alt={image.alt || item.card?.name || item.name}
              />
            )}
            <small>{item.kind || item._collection || 'entry'}</small><strong>{item.card?.name || item.name || item.title || item.id || item.key || `Record ${position + 1}`}</strong><p>{item.summary || item.description || item.content || ''}</p><details className="record-json"><summary>Inspect complete record</summary><pre>{JSON.stringify(item, null, 2)}</pre></details>
          </article>;
        })}</div></section>}
        {pack.actors.length > 0 && <section className="actor-gallery"><header><small>UNIFIED ACTOR CARDS</small><h3>PC / NPC / Monster</h3></header><div>{pack.actors.map((actor) => { const asset = actor.image ? assets.get(actor.image.asset_key) : null; return <article key={actor.id}>{asset && index ? <img loading="lazy" src={new URL(`${index.blob_base_path}/${asset.checksum}`, source).href} alt={actor.image?.alt || actor.name}/> : <div className="actor-placeholder" aria-hidden="true">{actor.name.slice(0, 1)}</div>}<small>{actor.actor_type}</small><strong>{actor.name}</strong><p>{actor.summary}</p><footer>{asset ? `${asset.license} · ${asset.attribution}` : 'No reliable source illustration supplied'}</footer><details className="record-json"><summary>Inspect complete actor card</summary><pre>{JSON.stringify(actor, null, 2)}</pre></details></article>; })}</div></section>}
        {visibleAssets.length > 0 && index && <section className="library-component asset-gallery"><header><div><small>PACKAGE ASSETS</small><h3>Maps, handouts and original documents</h3></div><code>{visibleAssets.length} assets</code></header><div>{visibleAssets.map((asset) => { const browserVisible = index.browser_asset_kinds.includes(asset.kind); const href = browserVisible ? new URL(`${index.blob_base_path}/${asset.checksum}`, source).href : new URL(selected?.download_path || '', source).href; return <a key={asset.asset_key} href={href} target="_blank" rel="noreferrer"><small>{asset.kind}</small>{asset.media_type.startsWith('image/') && browserVisible && <img loading="lazy" src={href} alt={asset.name}/>}<strong>{asset.name}</strong><span>{asset.metadata.logical_path || asset.asset_key}</span><footer>{Math.ceil(asset.size / 1024)} KiB · {browserVisible ? 'open asset' : 'inside archive'}</footer></a>; })}</div></section>}
        {pack.sources.length > 0 && index && <section className="library-component"><header><div><small>SOURCE EVIDENCE</small><h3>Normalized source documents</h3></div><code>{pack.sources.length} sources</code></header>{pack.sources.map((item) => <SourceDocument key={item.source_key} sourceItem={item} pack={pack} index={index} indexUrl={source}/>)}</section>}
        <details className="library-raw"><summary>完整统一包描述符 JSON</summary><pre>{JSON.stringify(pack, null, 2)}</pre></details></>}</main></div>
  </section>;
}
