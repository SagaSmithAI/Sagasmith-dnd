import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  GatewayRequestError,
  createActorFromPreset,
  downloadContentPackArtifact,
  getContentPackDetail,
  listCampaigns,
  listContentPacks,
  listDrafts,
  mutateContentPack,
  uploadContentPack,
} from '../../lib/api';
import type { Campaign } from '../../types';
import type {
  CatalogEntry,
  ContentActor,
  ContentAsset,
  ContentInventory,
  ContentLibraryIndex,
  ContentPackageV2,
  ContentSource,
  DraftInventory,
  GatewayMeta,
  InstalledPackSummary,
  PackKind,
} from './contracts';
import { assertContentPackage, assertLibraryIndex } from './contracts';
import {
  PACK_KIND_HELP,
  PACK_KIND_LABELS,
  PACK_KINDS,
  actorChallengeRating,
  catalogIdentity,
  extractDescriptor,
  installedIdentity,
  isCatalogEntryInstalled,
  packOperationIdentity,
  packageRecords,
  packageTitle,
  recordTitle,
} from './model';

type WorkbenchView = 'catalog' | 'installed' | 'drafts';
type DetailTab = 'overview' | 'content' | 'actors' | 'sources' | 'assets' | 'integrity';

const defaultUrl = import.meta.env.PUBLIC_SAGASMITH_LIBRARY_URL
  || 'https://sagasmithai.github.io/SagaSmith-dnd-content-library/content-library/index.json';

function errorMessage(error: unknown): string {
  if (error instanceof GatewayRequestError) {
    const labels = {
      offline: 'Gateway 离线', unauthorized: '身份未认证', forbidden: '没有 DM 权限',
      conflict: 'revision 或幂等键冲突', not_found: '目标不存在', contract: '请求契约不匹配', server: 'Gateway 内部错误',
    };
    return `${labels[error.category]}：${error.message}`;
  }
  return error instanceof Error ? error.message : String(error);
}

async function digest(blob: Blob): Promise<string> {
  const bytes = await blob.arrayBuffer();
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
    .map((value) => value.toString(16).padStart(2, '0')).join('');
}

function updateLocation(view: WorkbenchView, campaignId: string) {
  const query = new URLSearchParams(window.location.search);
  query.set('mode', view);
  if (campaignId) query.set('campaign', campaignId);
  window.history.replaceState({}, '', `${window.location.pathname}?${query}`);
}

export default function ContentWorkbench() {
  const [view, setView] = useState<WorkbenchView>('catalog');
  const [source, setSource] = useState(defaultUrl);
  const [index, setIndex] = useState<ContentLibraryIndex | null>(null);
  const [catalogSelection, setCatalogSelection] = useState<CatalogEntry | null>(null);
  const [installedSelection, setInstalledSelection] = useState<InstalledPackSummary | null>(null);
  const [descriptor, setDescriptor] = useState<ContentPackageV2 | null>(null);
  const [rawDetail, setRawDetail] = useState<unknown>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [campaignId, setCampaignId] = useState('');
  const [inventory, setInventory] = useState<ContentInventory | null>(null);
  const [inventoryMeta, setInventoryMeta] = useState<GatewayMeta | null>(null);
  const [drafts, setDrafts] = useState<DraftInventory | null>(null);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<'all' | PackKind>('all');
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [gatewayError, setGatewayError] = useState('');
  const [operation, setOperation] = useState('');
  const [moduleRemaps, setModuleRemaps] = useState('[]');

  const refreshInventory = useCallback(async (id = campaignId) => {
    if (!id) return;
    try {
      const result = await listContentPacks(id);
      setInventory(result.data);
      setInventoryMeta(result.meta);
      setGatewayError('');
      setInstalledSelection((current) => {
        if (!current) return result.data.packs[0] || null;
        return result.data.packs.find((item) => installedIdentity(item) === installedIdentity(current))
          || result.data.packs[0] || null;
      });
    } catch (error) {
      setInventory(null);
      setGatewayError(errorMessage(error));
    }
  }, [campaignId]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedView = params.get('mode');
    if (requestedView && ['catalog', 'installed', 'drafts'].includes(requestedView)) {
      setView(requestedView as WorkbenchView);
    }
    const indexUrl = params.get('source') || defaultUrl;
    setSource(indexUrl);
    Promise.all([
      fetch(indexUrl).then(async (response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        const value: unknown = await response.json();
        assertLibraryIndex(value);
        return value;
      }),
      listCampaigns().catch(() => []),
    ]).then(([nextIndex, nextCampaigns]) => {
      setIndex(nextIndex);
      setCatalogSelection(nextIndex.packages[0] || null);
      setCampaigns(nextCampaigns);
      const requestedCampaign = params.get('campaign');
      const selected = nextCampaigns.find((item) => item.id === requestedCampaign)
        || nextCampaigns.find((item) => item.status === 'active')
        || nextCampaigns[0];
      if (selected) setCampaignId(selected.id);
    }).catch((error) => setCatalogError(errorMessage(error)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { if (campaignId) void refreshInventory(campaignId); }, [campaignId, refreshInventory]);

  useEffect(() => {
    if (view !== 'drafts' || !campaignId) return;
    listDrafts(campaignId).then((result) => {
      setDrafts(result.data); setGatewayError('');
    }).catch((error) => setGatewayError(errorMessage(error)));
  }, [view, campaignId]);

  useEffect(() => {
    if (view !== 'catalog' || !catalogSelection) return;
    let cancelled = false;
    setDescriptor(null); setRawDetail(null); setDetailLoading(true);
    fetch(new URL(catalogSelection.path, source)).then(async (response) => {
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const value: unknown = await response.json();
      assertContentPackage(value);
      return value;
    }).then((value) => { if (!cancelled) setDescriptor(value); })
      .catch((error) => { if (!cancelled) setCatalogError(errorMessage(error)); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [catalogSelection, source, view]);

  useEffect(() => {
    if (view !== 'installed' || !installedSelection || !campaignId) return;
    let cancelled = false;
    setDescriptor(null); setRawDetail(null); setDetailLoading(true);
    getContentPackDetail(campaignId, installedSelection).then((result) => {
      if (cancelled) return;
      setRawDetail(result.data);
      setDescriptor(extractDescriptor(result.data));
    }).catch((error) => { if (!cancelled) setGatewayError(errorMessage(error)); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [campaignId, installedSelection, view]);

  const visibleCatalog = useMemo(() => (index?.packages || []).filter((entry) => (
    (kind === 'all' || entry.kind === kind)
    && `${entry.title} ${entry.id} ${entry.version} ${(entry.editions || []).join(' ')} ${entry.classification || ''}`
      .toLocaleLowerCase().includes(query.toLocaleLowerCase())
  )), [index, kind, query]);

  const visibleInstalled = useMemo(() => (inventory?.packs || []).filter((entry) => (
    (kind === 'all' || entry.kind === kind)
    && `${entry.title} ${entry.id} ${entry.version} ${entry.status}`.toLocaleLowerCase()
      .includes(query.toLocaleLowerCase())
  )), [inventory, kind, query]);

  const selectView = (next: WorkbenchView) => {
    setView(next); setQuery(''); setDescriptor(null); setRawDetail(null); setGatewayError('');
    updateLocation(next, campaignId);
  };

  const selectCampaign = (id: string) => {
    setCampaignId(id); setInstalledSelection(null); setInventory(null);
    updateLocation(view, id);
  };

  const installCatalogPack = async () => {
    if (!catalogSelection || !campaignId) return;
    setOperation('正在下载并校验归档…'); setGatewayError('');
    try {
      const response = await fetch(new URL(catalogSelection.download_path, source));
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const archive = await response.blob();
      const checksum = await digest(archive);
      if (checksum !== catalogSelection.archive_checksum) {
        throw new Error('目录声明的 archive checksum 与下载内容不一致');
      }
      setOperation('MCP 正在验证并导入 Pack…');
      await uploadContentPack(
        campaignId,
        catalogSelection.kind,
        archive,
        catalogSelection.download_path.split('/').pop() || `${catalogSelection.id}.sagasmith-pack`,
      );
      await refreshInventory();
      setOperation('导入完成；尚未自动激活。');
    } catch (error) {
      setOperation(''); setGatewayError(errorMessage(error));
    }
  };

  const runPackAction = async (action: 'activate' | 'deactivate' | 'remove' | 'export') => {
    if (!installedSelection || !campaignId) return;
    if (action === 'remove' && !window.confirm(`移除 ${installedSelection.title}@${installedSelection.version}？此操作只允许未被引用的版本。`)) return;
    try {
      let progressRemaps: unknown[] | undefined;
      if (installedSelection.kind === 'module' && action === 'activate') {
        const parsed: unknown = JSON.parse(moduleRemaps);
        if (!Array.isArray(parsed)) throw new Error('progress remaps 必须是数组');
        progressRemaps = parsed;
      }
      setOperation(`${action} 正在提交…`); setGatewayError('');
      const result = await mutateContentPack(campaignId, {
        kind: installedSelection.kind,
        action,
        ...packOperationIdentity(installedSelection),
        expected_revision: inventoryMeta?.campaign_revision,
        progress_remaps: progressRemaps,
      });
      await refreshInventory();
      const artifact = (result.data as any)?.artifact?.artifact;
      if (action === 'export' && artifact) {
        const blob = await downloadContentPackArtifact(
          campaignId,
          installedSelection.kind,
          artifact,
        );
        const href = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = href;
        anchor.download = artifact;
        anchor.click();
        URL.revokeObjectURL(href);
      }
      setOperation(artifact ? `导出完成：${artifact}` : `${action} 完成。`);
    } catch (error) {
      setOperation(''); setGatewayError(errorMessage(error));
    }
  };

  const phase = inventory?.campaign.phase || 'unknown';
  const lobby = phase === 'lobby';
  const installedExact = catalogSelection
    ? isCatalogEntryInstalled(catalogSelection, inventory?.packs || []) : false;

  return <section className="content-workbench">
    <header className="content-hero">
      <div><span>CONTENT CONTROL PLANE / SCHEMA V2</span><h1>内容包控制台</h1><p>发现、审计、导入并为战役显式激活不可变 Content Pack。Catalog 声明不等于安装验证；所有写入仍由 D&D MCP 权威执行。</p></div>
      <dl><div><dt>CATALOG</dt><dd>{index?.packages.length ?? '—'}</dd></div><div><dt>STORED</dt><dd>{inventory?.packs.length ?? '—'}</dd></div><div><dt>PHASE</dt><dd>{phase.toUpperCase()}</dd></div></dl>
    </header>

    <div className="content-context">
      <div className="content-modes" role="tablist">
        {(['catalog', 'installed', 'drafts'] as WorkbenchView[]).map((item) => <button role="tab" aria-selected={view === item} className={view === item ? 'active' : ''} onClick={() => selectView(item)} key={item}>{item === 'catalog' ? 'Catalog' : item === 'installed' ? 'Installed' : 'Drafts'}</button>)}
      </div>
      <label>战役<select value={campaignId} onChange={(event) => selectCampaign(event.target.value)}><option value="">未连接 Gateway</option>{campaigns.map((campaign) => <option value={campaign.id} key={campaign.id}>{campaign.name} · {campaign.edition}</option>)}</select></label>
      <div className={`phase-pill ${lobby ? 'lobby' : ''}`}>{phase.toUpperCase()} · {lobby ? 'WRITES AVAILABLE' : 'READ ONLY'}</div>
    </div>

    {catalogError && <div className="content-alert error"><strong>CATALOG</strong>{catalogError}</div>}
    {gatewayError && <div className="content-alert error"><strong>GATEWAY</strong>{gatewayError}</div>}
    {operation && <div className="content-alert success"><strong>OPERATION</strong>{operation}</div>}
    {!lobby && campaignId && <div className="content-alert info"><strong>PHASE BOUNDARY</strong>安全读取继续可用；导入、激活、移除和 Actor 创建必须返回 Lobby。</div>}

    {view !== 'drafts' && <div className="content-controls">
      <input aria-label="搜索内容包" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、ID、版本、状态…"/>
      <select aria-label="内容包类型" value={kind} onChange={(event) => setKind(event.target.value as 'all' | PackKind)}><option value="all">All kinds</option>{PACK_KINDS.map((item) => <option value={item} key={item}>{PACK_KIND_LABELS[item]}</option>)}</select>
      <code>{view === 'catalog' ? source : campaignId || 'NO CAMPAIGN'}</code>
    </div>}

    {loading ? <div className="content-empty">正在读取 Content Pack catalog…</div> : view === 'drafts'
      ? <DraftsPanel drafts={drafts} lobby={lobby}/>
      : <div className="content-layout">
        <nav className="pack-list" aria-label={view === 'catalog' ? 'Catalog packages' : 'Installed packages'}>
          {view === 'catalog' ? visibleCatalog.map((entry) => {
            const installed = isCatalogEntryInstalled(entry, inventory?.packs || []);
            return <button className={catalogSelection && catalogIdentity(catalogSelection) === catalogIdentity(entry) ? 'selected' : ''} key={catalogIdentity(entry)} onClick={() => setCatalogSelection(entry)}><small>{PACK_KIND_LABELS[entry.kind]} · {(entry.editions || []).join('/') || 'ALL'}</small><strong>{entry.title}</strong><span>{entry.id}@{entry.version}</span><footer><b>{Object.values(entry.component_counts).reduce((a, b) => a + b, 0)} records</b><em className={installed ? 'stored' : ''}>{installed ? 'STORED' : entry.license || 'UNLICENSED'}</em></footer></button>;
          }) : visibleInstalled.map((entry) => <button className={installedSelection && installedIdentity(installedSelection) === installedIdentity(entry) ? 'selected' : ''} key={installedIdentity(entry)} onClick={() => setInstalledSelection(entry)}><small>{PACK_KIND_LABELS[entry.kind]} · {entry.active ? 'ACTIVE' : entry.status.toUpperCase()}</small><strong>{entry.title}</strong><span>{entry.id}@{entry.version || 'LOCAL'}</span><footer><b>{entry.checksum ? entry.checksum.slice(0, 12) : entry.local_ref.slice(0, 12)}</b><em className={entry.active ? 'active' : 'stored'}>{entry.active ? 'ACTIVE' : 'STORED'}</em></footer></button>)}
          {((view === 'catalog' && visibleCatalog.length === 0) || (view === 'installed' && visibleInstalled.length === 0)) && <div className="content-empty">当前筛选没有内容包。</div>}
        </nav>
        <main className="pack-detail">
          {detailLoading && <div className="content-empty">正在读取 Pack 详情…</div>}
          {!detailLoading && descriptor && <DescriptorDetail
            pack={descriptor}
            index={index}
            indexUrl={source}
            catalogEntry={view === 'catalog' ? catalogSelection : null}
            installed={view === 'installed' ? installedSelection : null}
            lobby={lobby}
            installedExact={installedExact}
            onInstall={installCatalogPack}
            onAction={runPackAction}
            campaignId={campaignId}
            onActorCreated={(message) => setOperation(message)}
            onError={(message) => setGatewayError(message)}
            moduleRemaps={moduleRemaps}
            onModuleRemaps={setModuleRemaps}
          />}
          {!detailLoading && !descriptor && rawDetail != null && <RawInstalledDetail pack={installedSelection} raw={rawDetail} lobby={lobby} onAction={runPackAction}/>} 
          {!detailLoading && !descriptor && rawDetail == null && <div className="content-empty">从左侧选择一个 Content Pack。</div>}
        </main>
      </div>}
  </section>;
}

function DescriptorDetail({
  pack, index, indexUrl, catalogEntry, installed, lobby, installedExact, onInstall, onAction,
  campaignId, onActorCreated, onError, moduleRemaps, onModuleRemaps,
}: {
  pack: ContentPackageV2; index: ContentLibraryIndex | null; indexUrl: string;
  catalogEntry: CatalogEntry | null; installed: InstalledPackSummary | null; lobby: boolean;
  installedExact: boolean; onInstall: () => void; onAction: (action: 'activate' | 'deactivate' | 'remove' | 'export') => void;
  campaignId: string; onActorCreated: (message: string) => void; onError: (message: string) => void;
  moduleRemaps: string; onModuleRemaps: (value: string) => void;
}) {
  const [tab, setTab] = useState<DetailTab>('overview');
  const [recordPage, setRecordPage] = useState(0);
  const [actorPage, setActorPage] = useState(0);
  const [actorQuery, setActorQuery] = useState('');
  const [actorType, setActorType] = useState('all');
  const records = useMemo(() => packageRecords(pack), [pack]);
  const assets = useMemo(() => new Map(pack.assets.map((asset) => [asset.asset_key, asset])), [pack]);
  const actors = useMemo(() => pack.actors.filter((actor) => (
    (actorType === 'all' || actor.actor_type === actorType)
    && `${actor.name} ${actor.summary} ${actorChallengeRating(actor)}`.toLocaleLowerCase().includes(actorQuery.toLocaleLowerCase())
  )), [pack, actorQuery, actorType]);
  const actorTypes = [...new Set(pack.actors.map((actor) => actor.actor_type))];
  const profile = pack.kind === 'module' ? pack.content.play_profile as Record<string, any> | undefined : undefined;
  const finalization = pack.metadata.agent_finalization as Record<string, unknown> | undefined;
  const activate = installed && ['core_rules', 'addon', 'module'].includes(installed.kind) && !installed.active;
  const deactivate = installed?.active && ['core_rules', 'addon'].includes(installed.kind);
  const catalogBacked = Boolean(
    catalogEntry
    || index?.packages.some((entry) => (
      entry.kind === pack.kind
      && entry.id === pack.id
      && entry.version === pack.version
      && entry.checksum === pack.checksum
    )),
  );
  const browserIndex = catalogBacked ? index : null;

  return <>
    <header className="pack-detail-head"><div><small>{PACK_KIND_LABELS[pack.kind]} / SCHEMA {pack.schema_version}</small><h2>{packageTitle(pack)}</h2><p>{pack.id}@{pack.version}</p></div><div className="pack-head-actions">
      {catalogEntry && <button className="btn btn-primary" disabled={!lobby || installedExact || !campaignId} onClick={onInstall}>{installedExact ? '已导入此精确版本' : '导入到战役'}</button>}
      {activate && <button className="btn btn-primary" disabled={!lobby} onClick={() => onAction('activate')}>激活</button>}
      {deactivate && <button className="btn btn-ghost" disabled={!lobby} onClick={() => onAction('deactivate')}>停用</button>}
      {installed && <button className="btn btn-ghost" disabled={!lobby} onClick={() => onAction('export')}>导出</button>}
      {installed && !installed.active && <button className="btn btn-danger" disabled={!lobby} onClick={() => onAction('remove')}>移除</button>}
    </div></header>
    <div className="pack-state-strip"><span>FORMAT<b>{pack.format}</b></span><span>STATUS<b>{installed?.active ? 'ACTIVE' : installed ? 'STORED' : 'CATALOG'}</b></span><span>LICENSE<b>{String(pack.metadata.license || '—')}</b></span><span>DISTRIBUTION<b>{String(pack.metadata.distribution || '—')}</b></span></div>
    {installed?.kind === 'module' && !installed.active && <section className="remap-panel"><div><strong>MODULE PROGRESS REMAP</strong><p>仅粘贴 Agent 已确认的显式映射。空数组表示不迁移旧进度；UI 不按标题或相似度猜测。</p></div><textarea value={moduleRemaps} onChange={(event) => onModuleRemaps(event.target.value)} spellCheck={false}/></section>}
    <div className="pack-tabs" role="tablist">{(['overview', 'content', 'actors', 'sources', 'assets', 'integrity'] as DetailTab[]).map((item) => <button role="tab" aria-selected={tab === item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)} key={item}>{item}</button>)}</div>

    {tab === 'overview' && <div className="pack-pane overview-pane">
      <section><small>KIND SEMANTICS</small><h3>{PACK_KIND_LABELS[pack.kind]}</h3><p>{PACK_KIND_HELP[pack.kind]}</p></section>
      <section><small>COMPONENTS</small><dl><div><dt>Actors</dt><dd>{pack.actors.length}</dd></div><div><dt>Sources</dt><dd>{pack.sources.length}</dd></div><div><dt>Assets</dt><dd>{pack.assets.length}</dd></div><div><dt>Records</dt><dd>{records.length}</dd></div></dl></section>
      {profile && <section className="wide"><small>MODULE PLAY PROFILE</small><dl><div><dt>Party</dt><dd>{profile.party_size?.minimum ?? '—'}–{profile.party_size?.maximum ?? '—'}</dd></div><div><dt>Levels</dt><dd>{profile.starting_level?.value ?? '—'}–{profile.expected_end_level?.value ?? '—'}</dd></div><div><dt>Advancement</dt><dd>{profile.advancement?.recommended || '—'}</dd></div><div><dt>Finalized</dt><dd>{finalization?.confirmed === true ? 'YES' : 'NO'}</dd></div></dl></section>}
      <section className="wide"><small>DEPENDENCY LOCKS</small>{pack.dependencies.length ? <div className="dependency-list">{pack.dependencies.map((item) => <div key={`${item.kind}:${item.id}@${item.version}`}><b>{item.kind}</b><strong>{item.id}@{item.version}</strong><code>{item.checksum}</code><em>{item.optional ? 'OPTIONAL' : 'REQUIRED'}</em></div>)}</div> : <p>没有外部依赖。</p>}</section>
    </div>}

    {tab === 'content' && <div className="pack-pane"><PaneHeading label="STRUCTURED CONTENT" title="规则、场景、叙事与审查记录" count={records.length}/><div className="record-grid">{records.slice(recordPage * 30, recordPage * 30 + 30).map((record, index) => <article key={`${recordTitle(record, index)}-${index}`}><small>{String(record.kind || record._collection || 'record')}</small><strong>{recordTitle(record, index)}</strong><p>{String(record.summary || record.description || record.content || '')}</p><details><summary>Inspect record</summary><pre>{JSON.stringify(record, null, 2)}</pre></details></article>)}</div><Pager page={recordPage} total={records.length} pageSize={30} onPage={setRecordPage}/></div>}

    {tab === 'actors' && <div className="pack-pane"><PaneHeading label="ACTOR-CARD.V3" title="PC / NPC / Monster" count={pack.actors.length}/><div className="actor-filters"><input value={actorQuery} onChange={(event) => { setActorQuery(event.target.value); setActorPage(0); }} placeholder="搜索名称、摘要或 CR…"/><select value={actorType} onChange={(event) => { setActorType(event.target.value); setActorPage(0); }}><option value="all">All actor types</option>{actorTypes.map((item) => <option key={item}>{item}</option>)}</select></div><div className="actor-card-grid">{actors.slice(actorPage * 24, actorPage * 24 + 24).map((actor) => <ActorCard key={actor.id} actor={actor} asset={actor.image ? assets.get(actor.image.asset_key) : undefined} index={browserIndex} indexUrl={indexUrl} campaignId={campaignId} installed={pack.kind === 'preset' ? installed : null} canCreate={lobby && pack.kind === 'preset' && Boolean(installed)} onCreated={onActorCreated} onError={onError}/>)}</div><Pager page={actorPage} total={actors.length} pageSize={24} onPage={setActorPage}/></div>}

    {tab === 'sources' && <div className="pack-pane"><PaneHeading label="SOURCE EVIDENCE" title="规范化来源与精确 chunks" count={pack.sources.length}/><div className="source-list">{pack.sources.map((sourceItem) => <SourceDocument key={sourceItem.source_key} sourceItem={sourceItem} pack={pack} index={browserIndex} indexUrl={indexUrl}/>)}</div></div>}

    {tab === 'assets' && <div className="pack-pane"><PaneHeading label="CONTENT-ADDRESSED BLOBS" title="图片、地图、Handout 与原始文档" count={pack.assets.length}/><div className="asset-card-grid">{pack.assets.map((asset) => <AssetCard key={asset.asset_key} asset={asset} index={browserIndex} indexUrl={indexUrl}/>)}</div></div>}

    {tab === 'integrity' && <div className="pack-pane integrity-pane"><section><small>DESCRIPTOR SHA-256</small><code>{pack.checksum}</code></section>{catalogEntry && <section><small>ARCHIVE SHA-256</small><code>{catalogEntry.archive_checksum}</code><p>{Math.ceil(catalogEntry.archive_size / 1024)} KiB · import 时由 MCP 重新校验全部 blobs。</p></section>}<section><small>AGENT FINALIZATION</small><pre>{JSON.stringify(finalization || null, null, 2)}</pre></section><section><small>AUTHORING REVIEW</small><pre>{JSON.stringify(pack.metadata.authoring_review || null, null, 2)}</pre></section><details className="raw-descriptor"><summary>完整 schema v2 descriptor</summary><pre>{JSON.stringify(pack, null, 2)}</pre></details></div>}
  </>;
}

function RawInstalledDetail({ pack, raw, lobby, onAction }: { pack: InstalledPackSummary | null; raw: unknown; lobby: boolean; onAction: (action: 'activate' | 'deactivate' | 'remove' | 'export') => void }) {
  if (!pack) return null;
  return <><header className="pack-detail-head"><div><small>{PACK_KIND_LABELS[pack.kind]} / LOCAL INVENTORY</small><h2>{pack.title}</h2><p>{pack.id}@{pack.version || 'local'}</p></div><div className="pack-head-actions">{!pack.active && pack.kind !== 'preset' && <button className="btn btn-primary" disabled={!lobby} onClick={() => onAction('activate')}>激活</button>}{pack.active && ['core_rules', 'addon'].includes(pack.kind) && <button className="btn btn-ghost" disabled={!lobby} onClick={() => onAction('deactivate')}>停用</button>}<button className="btn btn-ghost" disabled={!lobby} onClick={() => onAction('export')}>导出</button>{!pack.active && <button className="btn btn-danger" disabled={!lobby} onClick={() => onAction('remove')}>移除</button>}</div></header><div className="pack-pane"><PaneHeading label="AUTHORITATIVE LOCAL RECORD" title="Gateway 投影"/><pre className="raw-record">{JSON.stringify(raw, null, 2)}</pre></div></>;
}

function ActorCard({ actor, asset, index, indexUrl, campaignId, installed, canCreate, onCreated, onError }: { actor: ContentActor; asset?: ContentAsset; index: ContentLibraryIndex | null; indexUrl: string; campaignId: string; installed: InstalledPackSummary | null; canCreate: boolean; onCreated: (message: string) => void; onError: (message: string) => void }) {
  const [creating, setCreating] = useState(false);
  const imageUrl = asset && index?.browser_asset_kinds.includes(asset.kind)
    ? new URL(`${index.blob_base_path}/${asset.checksum}`, indexUrl).href : null;
  const create = async () => {
    setCreating(true);
    try {
      if (!installed) throw new Error('Actor 创建要求精确的已安装 preset 版本');
      const result = await createActorFromPreset(campaignId, installed, actor.id);
      onCreated(`已从 ${actor.name} 创建 Character：${result.data.character.name}`);
    } catch (error) { onError(errorMessage(error)); }
    finally { setCreating(false); }
  };
  return <article>{imageUrl ? <img loading="lazy" src={imageUrl} alt={actor.image?.alt || actor.name}/> : <div className="actor-fallback" aria-hidden="true">{actor.name.slice(0, 1)}</div>}<small>{actor.actor_type} · CR {actorChallengeRating(actor)}</small><strong>{actor.name}</strong><p>{actor.summary}</p><footer><span>{String(actor.metadata.edition || actor.sheet.edition || 'ALL')}</span>{canCreate && <button onClick={create} disabled={creating}>{creating ? '创建中…' : '创建 Character'}</button>}</footer><details><summary>Actor Card</summary><pre>{JSON.stringify(actor, null, 2)}</pre></details></article>;
}

function SourceDocument({ sourceItem, pack, index, indexUrl }: { sourceItem: ContentSource; pack: ContentPackageV2; index: ContentLibraryIndex | null; indexUrl: string }) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const asset = pack.assets.find((item) => item.asset_key === sourceItem.normalized_document_asset_key);
  const load = async () => {
    if (!asset || text || !index || !index.browser_asset_kinds.includes(asset.kind)) return;
    try {
      const response = await fetch(new URL(`${index.blob_base_path}/${asset.checksum}`, indexUrl));
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      setText(await response.text());
    } catch (reason) { setError(errorMessage(reason)); }
  };
  const chunks = sourceItem.sections.reduce((total, section) => total + section.chunks.length, 0);
  return <details onToggle={(event) => { if (event.currentTarget.open) void load(); }}><summary><span><b>{sourceItem.title}</b><small>{sourceItem.source_key}</small></span><em>{sourceItem.sections.length} sections · {chunks} chunks</em></summary><div className="source-meta"><span>{sourceItem.edition || 'ALL'} · {sourceItem.locale || '—'}</span><span>{sourceItem.authority || '—'}</span><span>{sourceItem.original_asset_keys.length} original files</span></div>{error && <p className="inline-error">{error}</p>}{text ? <pre>{text}</pre> : <p>展开后按内容哈希加载可在浏览器公开的规范化来源；私有原文不会通过 Catalog 泄露。</p>}</details>;
}

function AssetCard({ asset, index, indexUrl }: { asset: ContentAsset; index: ContentLibraryIndex | null; indexUrl: string }) {
  const visible = Boolean(index?.browser_asset_kinds.includes(asset.kind));
  const href = visible && index ? new URL(`${index.blob_base_path}/${asset.checksum}`, indexUrl).href : null;
  return <article>{href && asset.media_type.startsWith('image/') && <img loading="lazy" src={href} alt={asset.alt || asset.name}/>}<small>{asset.kind}</small><strong>{asset.name}</strong><span>{asset.media_type} · {Math.ceil(asset.size / 1024)} KiB</span><p>{asset.attribution}</p><footer>{visible ? <a href={href || '#'} target="_blank" rel="noreferrer">OPEN ASSET ↗</a> : <em>ARCHIVE ONLY</em>}</footer></article>;
}

function DraftsPanel({ drafts, lobby }: { drafts: DraftInventory | null; lobby: boolean }) {
  const groups = [
    ['MODULE DRAFTS', drafts?.module?.jobs || []],
    ['RULEBOOK DRAFTS', drafts?.rulebook?.jobs || []],
  ] as const;
  return <div className="drafts-view"><header><div><span>AGENT-OWNED AUTHORING</span><h2>Draft 可观察性</h2><p>这里展示 revision、状态、候选和最终 Pack 记录；来源解释、语义修复和 finalization 仍由 Agent + Skills 完成。</p></div><b>{lobby ? 'LOBBY' : 'READ UNAVAILABLE OUTSIDE LOBBY'}</b></header>{groups.map(([label, jobs]) => <section key={label}><div className="pane-heading"><div><small>{label}</small><h3>{jobs.length} jobs</h3></div></div>{jobs.map((job) => { const finalized = Boolean((job.result as any)?.finalized_package); return <article key={job.id}><span className={`draft-state ${finalized ? 'finalized' : ''}`}>{finalized ? 'FINALIZED' : job.state}</span><div><strong>{String(job.title || job.name || job.id)}</strong><small>{job.id} · REV {job.revision}</small></div><dl><div><dt>Candidates</dt><dd>{Array.isArray(job.candidates) ? job.candidates.length : 0}</dd></div><div><dt>Warnings</dt><dd>{Array.isArray(job.warnings) ? job.warnings.length : 0}</dd></div></dl><details><summary>Inspect job</summary><pre>{JSON.stringify(job, null, 2)}</pre></details></article>; })}{jobs.length === 0 && <div className="content-empty">没有 {label.toLocaleLowerCase()}。</div>}</section>)}</div>;
}

function PaneHeading({ label, title, count }: { label: string; title: string; count?: number }) {
  return <div className="pane-heading"><div><small>{label}</small><h3>{title}</h3></div>{count != null && <code>{count} records</code>}</div>;
}

function Pager({ page, total, pageSize, onPage }: { page: number; total: number; pageSize: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return <div className="content-pager"><button disabled={page === 0} onClick={() => onPage(page - 1)}>← 上一页</button><span>{page + 1} / {pages}</span><button disabled={page >= pages - 1} onClick={() => onPage(page + 1)}>下一页 →</button></div>;
}
