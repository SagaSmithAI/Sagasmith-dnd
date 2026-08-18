import { useEffect, useState } from 'react';
import { GatewayRequestError, listContentPacks, subscribeCampaign } from '../../lib/api';
import type { Campaign } from '../../types';
import type { ContentInventory, InstalledPackSummary, PackKind } from './contracts';
import { PACK_KIND_HELP, PACK_KIND_LABELS, PACK_KINDS } from './model';

function message(error: unknown) {
  if (error instanceof GatewayRequestError) return `${error.category}: ${error.message}`;
  return error instanceof Error ? error.message : String(error);
}

export default function CampaignContentPanel({ campaign }: { campaign: Campaign }) {
  const [inventory, setInventory] = useState<ContentInventory | null>(null);
  const [error, setError] = useState('');
  const load = () => listContentPacks(campaign.id).then((result) => {
    setInventory(result.data); setError('');
  }).catch((reason) => setError(message(reason)));

  useEffect(() => {
    void load();
    const close = subscribeCampaign(campaign.id, load);
    return close;
  }, [campaign.id]);

  const groups = Object.fromEntries(PACK_KINDS.map((kind) => [
    kind,
    (inventory?.packs || []).filter((pack) => pack.kind === kind),
  ])) as Record<PackKind, InstalledPackSummary[]>;

  return <div className="campaign-content-panel">
    <header><div><span>CONTENT LOADOUT / BRANCH-AWARE</span><h2>战役内容配置</h2><p>显示当前战役已存储和激活的不可变 Pack。更改只允许在 Lobby，由 MCP 再次检查 DM、revision 和 branch。</p></div><a className="btn btn-primary" href={`/library?mode=installed&campaign=${encodeURIComponent(campaign.id)}`}>打开内容包控制台</a></header>
    {error && <div className="content-alert error"><strong>GATEWAY</strong>{error}</div>}
    <div className="loadout-summary"><span>EDITION<b>{inventory?.campaign.edition || campaign.edition}</b></span><span>PHASE<b>{inventory?.campaign.phase || String(campaign.state?.game_phase || '—')}</b></span><span>STORED<b>{inventory?.packs.length ?? '—'}</b></span><span>ACTIVE<b>{inventory?.packs.filter((item) => item.active).length ?? '—'}</b></span></div>
    <div className="loadout-grid">{PACK_KINDS.map((kind) => <section key={kind}><header><div><small>{kind}</small><h3>{PACK_KIND_LABELS[kind]}</h3></div><b>{groups[kind].length}</b></header><p>{PACK_KIND_HELP[kind]}</p>{groups[kind].map((pack) => <article key={`${pack.id}:${pack.version}:${pack.local_ref}`}><span className={pack.active ? 'active' : ''}>{pack.active ? 'ACTIVE' : pack.status.toUpperCase()}</span><div><strong>{pack.title}</strong><small>{pack.id}@{pack.version || 'local'}</small></div><code>{pack.checksum ? pack.checksum.slice(0, 16) : pack.local_ref.slice(0, 16)}</code>{pack.warnings.length > 0 && <em>{pack.warnings.length} WARN</em>}</article>)}{groups[kind].length === 0 && <div className="empty">没有 {PACK_KIND_LABELS[kind]}。</div>}</section>)}</div>
  </div>;
}

