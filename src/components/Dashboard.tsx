import { useState, useEffect } from 'react';
import { health, listCampaigns, listCharacters, listModules, listSaves, MOCK_CAMPAIGNS } from '../lib/api';
import type { Campaign, HealthStatus } from '../types';

export default function Dashboard() {
  const [connected, setConnected] = useState<'loading' | 'connected' | 'disconnected'>('loading');
  const [healthData, setHealthData] = useState<HealthStatus | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [stats, setStats] = useState({ characters: 0, modules: 0, saves: 0 });

  useEffect(() => {
    health()
      .then(h => { setConnected('connected'); setHealthData(h); })
      .catch(() => setConnected('disconnected'));
    listCampaigns().then(setCampaigns).catch(() => setCampaigns(MOCK_CAMPAIGNS));
  }, []);

  useEffect(() => {
    campaigns.forEach(c => {
      listCharacters(c.id).then(chars => setStats(s => ({ ...s, characters: s.characters + chars.length }))).catch(() => {});
      listModules(c.id).then(mods => setStats(s => ({ ...s, modules: s.modules + mods.length }))).catch(() => {});
      listSaves(c.id).then(svs => setStats(s => ({ ...s, saves: s.saves + svs.length }))).catch(() => {});
    });
  }, [campaigns]);

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto', padding: '24px 16px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>SagaSmith D&D</h1>
          <p style={{ fontSize: '.85rem', color: '#6b7280' }}>Dashboard</p>
        </div>
        <StatusBadge status={connected} health={healthData} />
      </div>

      {/* Stats */}
      <div className="stat-grid" style={{ marginBottom: 24 }}>
        <div className="stat-card"><div className="stat-number">{campaigns.length}</div><div className="stat-label">战役</div></div>
        <div className="stat-card"><div className="stat-number">{stats.characters}</div><div className="stat-label">角色</div></div>
        <div className="stat-card"><div className="stat-number">{stats.modules}</div><div className="stat-label">模组</div></div>
        <div className="stat-card"><div className="stat-number">{stats.saves}</div><div className="stat-label">存档</div></div>
      </div>

      {/* Campaign list */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">战役列表</div>
        {campaigns.length === 0 ? (
          <p style={{ color: '#9ca3af', padding: '20px 0', textAlign: 'center' }}>暂无战役</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {campaigns.map(c => <CampaignRow key={c.id} campaign={c} />)}
          </div>
        )}
      </div>

      {/* Quick actions */}
      <div className="card">
        <div className="card-header">快速操作</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <a href="/campaigns" className="btn btn-primary">管理战役</a>
          <a href="/rules" className="btn btn-ghost">搜索规则</a>
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status, health }: { status: string; health: HealthStatus | null }) {
  const color = status === 'connected' ? '#059669' : status === 'loading' ? '#d97706' : '#dc2626';
  const text = status === 'connected' ? `已连接 ${health?.dense ? '· Dense ✓' : '· FTS5'}` : status === 'loading' ? '连接中...' : 'API 不可用（Mock 数据）';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '.8rem', color }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: color }} />
      {text}
    </div>
  );
}

function CampaignRow({ campaign }: { campaign: Campaign }) {
  return (
    <a href={`/campaigns/${campaign.id}`} style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '12px 16px', borderRadius: 8, background: '#f9fafb', textDecoration: 'none', color: 'inherit',
      transition: 'background .15s',
    }} onMouseOver={e => (e.currentTarget.style.background = '#f3f4f6')}
       onMouseOut={e => (e.currentTarget.style.background = '#f9fafb')}>
      <div>
        <div style={{ fontWeight: 600 }}>{campaign.name}</div>
        <div style={{ fontSize: '.8rem', color: '#6b7280' }}>
          D&D 5e {campaign.edition} · {campaign.locale === 'zh' ? '中文' : 'EN'} · 修订 {campaign.revision}
        </div>
      </div>
      <span className={`badge ${campaign.status === 'active' ? 'badge-green' : 'badge-gray'}`}>
        {campaign.status === 'active' ? '进行中' : campaign.status}
      </span>
    </a>
  );
}
