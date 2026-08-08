import { useEffect, useMemo, useState } from 'react';

type Entry = { kind: string; id: string; version: string; checksum: string; title: string; edition?: string; license?: string; component_counts: Record<string, number>; image_count: number; path: string };
type Index = { schema: string; system_id: string; packages: Entry[] };
type Portable = { kind: string; id: string; version: string; checksum: string; metadata: Record<string, any>; payload: Record<string, any> };
const defaultUrl = import.meta.env.PUBLIC_SAGASMITH_LIBRARY_URL || 'https://sagasmithai.github.io/content-library/index.json';

const cardsOf = (pack: Portable | null): Portable[] => {
  if (!pack) return [];
  if (pack.kind === 'preset_pack') return pack.payload.cards || [];
  if (pack.kind === 'module_pack') return pack.payload.actors || [];
  return (pack.payload.components || []).flatMap((item: Portable) => {
    if (item.kind === 'preset_pack') return item.payload.cards || [];
    if (item.kind === 'module_pack') return item.payload.actors || [];
    return [];
  });
};
const recordsOf = (component: Portable) => ['artifacts', 'mechanics', 'entries', 'definitions', 'sources', 'scene_atlas', 'actors', 'assets']
  .flatMap((key) => Array.isArray(component.payload?.[key]) ? component.payload[key].map((item: any) => ({ ...item, _collection: key })) : []);

export default function ContentLibrary() {
  const [source, setSource] = useState(defaultUrl), [index, setIndex] = useState<Index | null>(null);
  const [selected, setSelected] = useState<Entry | null>(null), [pack, setPack] = useState<Portable | null>(null);
  const [query, setQuery] = useState(''), [kind, setKind] = useState('all'), [error, setError] = useState('');
  useEffect(() => {
    const url = new URLSearchParams(location.search).get('source') || defaultUrl; setSource(url);
    fetch(url).then((r) => { if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return r.json(); })
      .then((data: Index) => { setIndex(data); setSelected(data.packages[0] || null); }).catch((e) => setError(String(e)));
  }, []);
  useEffect(() => { if (selected) fetch(new URL(selected.path, source)).then((r) => { if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return r.json(); }).then(setPack).catch((e) => setError(String(e))); }, [selected, source]);
  const visible = useMemo(() => (index?.packages || []).filter((entry) => (kind === 'all' || entry.kind === kind) && `${entry.title} ${entry.id} ${entry.edition || ''}`.toLowerCase().includes(query.toLowerCase())), [index, kind, query]);
  const cards = cardsOf(pack), components: Portable[] = pack?.kind === 'addon_pack' ? pack.payload.components || [] : pack ? [pack] : [];
  return <section className="content-library">
    <header className="library-hero"><div><span>PORTABLE CONTENT / READ-ONLY</span><h2>Preset & Addon Library</h2><p>浏览包、规则组件、来源、角色卡与图片。安装和战役启用仍由 MCP 分别审批。</p></div><dl><div><dt>PACKAGES</dt><dd>{index?.packages.length ?? '—'}</dd></div><div><dt>ACTORS</dt><dd>{index?.packages.reduce((n, x) => n + (x.component_counts.actor_card || 0), 0) ?? '—'}</dd></div></dl></header>
    {error && <p className="library-error">Library unavailable: {error}</p>}
    <div className="library-controls"><input aria-label="Search packages" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search title, id, edition…"/><select aria-label="Package kind" value={kind} onChange={(e) => setKind(e.target.value)}><option value="all">All packages</option><option value="preset_pack">Presets</option><option value="addon_pack">Addons</option><option value="module_pack">Modules</option></select><code>{source}</code></div>
    <div className="library-layout"><nav className="library-packages" aria-label="Content packages">{visible.map((entry) => <button className={selected?.checksum === entry.checksum ? 'selected' : ''} key={entry.checksum} onClick={() => setSelected(entry)}><small>{entry.kind.replace('_pack', '').toUpperCase()} · {entry.edition || 'ALL'}</small><strong>{entry.title}</strong><span>{entry.id}@{entry.version}</span><footer><b>{entry.component_counts.actor_card || Object.values(entry.component_counts).reduce((a,b) => a+b, 0)} records</b><em>{entry.license}</em></footer></button>)}</nav>
      <main className="library-detail">{pack && <><header><div><small>{pack.kind} / {pack.version}</small><h2>{pack.metadata.title || pack.id}</h2><p>{pack.id}</p></div><div className="checksum"><span>SHA-256</span>{pack.checksum}</div></header><div className="library-meta"><span>LICENSE <b>{pack.metadata.license || '—'}</b></span><span>DISTRIBUTION <b>{pack.metadata.distribution || '—'}</b></span><span>COMPONENTS <b>{components.length}</b></span><span>IMAGES <b>{cards.filter((x) => x.payload.image).length}</b></span></div>
        {components.map((component) => <section className="library-component" key={component.checksum}><header><div><small>{component.kind}</small><h3>{component.metadata?.title || component.id}</h3></div><code>{component.id}@{component.version}</code></header>{component.kind !== 'preset_pack' && <div className="artifact-grid">{recordsOf(component).map((item, i) => <article key={`${item.id || item.key || item.name}-${i}`}><small>{item.kind || item._collection || 'entry'}</small><strong>{item.name || item.title || item.id || item.key || `Record ${i+1}`}</strong><p>{item.summary || item.description || item.content || ''}</p></article>)}</div>}</section>)}
        {cards.length > 0 && <section className="actor-gallery"><header><small>UNIFIED ACTOR CARDS</small><h3>PC / NPC / Monster</h3></header><div>{cards.map((card) => { const image = card.payload.image; return <article key={card.checksum}>{image ? <img loading="lazy" src={`data:${image.media_type};base64,${image.data_base64}`} alt={image.alt}/> : <div className="actor-placeholder" aria-hidden="true">{String(card.payload.name).slice(0,1)}</div>}<small>{card.payload.actor_type}</small><strong>{card.payload.name}</strong><p>{card.payload.summary}</p><footer>{image ? `${image.license} · ${image.attribution}` : 'No redistributable image supplied'}</footer></article>; })}</div></section>}
        <details className="library-raw"><summary>完整 portable JSON</summary><pre>{JSON.stringify(pack, null, 2)}</pre></details></>}</main></div>
  </section>;
}
