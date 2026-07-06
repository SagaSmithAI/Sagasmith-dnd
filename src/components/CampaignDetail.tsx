import { useState, useEffect } from 'react';
import { getCampaign, listCharacters, listModules, listSaves, currentScene } from '../lib/api';
import type { Campaign, Character, ModuleSource, SaveSlot, CurrentScene } from '../types';

export default function CampaignDetail() {
  const id = typeof window !== 'undefined' ? window.location.pathname.split('/').pop() : '';
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [modules, setModules] = useState<ModuleSource[]>([]);
  const [saves, setSaves] = useState<SaveSlot[]>([]);
  const [scene, setScene] = useState<CurrentScene | null>(null);
  const [activeTab, setActiveTab] = useState<'overview' | 'timeline'>('overview');

  useEffect(() => {
    if (!id) return;
    getCampaign(id).then(setCampaign).catch(() => setCampaign(MOCK_CAMPAIGNS[0]));
    listCharacters(id).then(setCharacters).catch(() => {});
    listModules(id).then(setModules).catch(() => {});
    listSaves(id).then(setSaves).catch(() => {});
    currentScene(id).then(setScene).catch(() => {});
  }, [id]);

  if (!campaign) return <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>加载中...</div>;

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto', padding: '24px 16px' }}>
      {/* Header */}
      <div className="page-header">
        <a href="/" className="btn btn-ghost btn-sm" style={{ marginRight: 4 }}>←</a>
        <div>
          <h1>{campaign.name}</h1>
          <div className="subtitle">D&D 5e {campaign.edition} · 修订 {campaign.revision}</div>
        </div>
        <span style={{ marginLeft: 'auto' }} className={`badge ${campaign.status === 'active' ? 'badge-green' : 'badge-gray'}`}>
          {campaign.status === 'active' ? '进行中' : campaign.status}
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        {/* Characters */}
        <div className="card">
          <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span>角色 ({characters.length})</span>
            <a href={`/characters?campaign=${id}`} style={{ fontSize: '.8rem' }}>查看全部</a>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {characters.slice(0, 6).map(c => (
              <a key={c.id} href={`/characters/${c.id}`} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', borderRadius: 6, background: '#f9fafb', textDecoration: 'none', color: 'inherit', fontSize: '.9rem' }}>
                <span>{c.character_type === 'pc' ? '🧙' : '👤'}</span>
                <span style={{ fontWeight: 500 }}>{c.name}</span>
                {(c.sheet as any)?.class && <span style={{ fontSize: '.8rem', color: '#6b7280' }}>{(c.sheet as any).class} Lv.{(c.sheet as any).level}</span>}
              </a>
            ))}
            {characters.length === 0 && <p style={{ color: '#9ca3af', fontSize: '.85rem', padding: '8px 0' }}>暂无角色</p>}
          </div>
        </div>

        {/* Modules */}
        <div className="card">
          <div className="card-header">模组 ({modules.length})</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {modules.map(m => (
              <a key={m.id} href={`/modules/${m.id}`} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', borderRadius: 6, background: '#f9fafb', textDecoration: 'none', color: 'inherit', fontSize: '.9rem' }}>
                <span>📖 {m.title}</span>
                <span className="badge badge-blue" style={{ fontSize: '.7rem' }}>{m.parser_profile}</span>
              </a>
            ))}
            {modules.length === 0 && <p style={{ color: '#9ca3af', fontSize: '.85rem', padding: '8px 0' }}>暂无模组</p>}
          </div>
        </div>
      </div>

      {/* Current scene */}
      {scene && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-header">当前场景 — scope: {scene.scope_id}</div>
          <div style={{ fontSize: '.9rem', marginBottom: 8 }}>📍 {scene.title}</div>
          {scene.progress && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '.8rem', color: '#6b7280', marginBottom: 4 }}>
                <span>进度</span><span>{scene.progress.progress}%</span>
              </div>
              <div className="progress-bar"><div className="progress-fill" style={{ width: `${scene.progress.progress}%` }} /></div>
            </div>
          )}
        </div>
      )}

      {/* Tab: Overview / Timeline */}
      <div style={{ display: 'flex', gap: 0, marginBottom: 0, borderBottom: '2px solid #e5e7eb' }}>
        {(['overview', 'timeline'] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            style={{
              padding: '10px 20px', border: 'none', background: 'transparent', cursor: 'pointer',
              fontWeight: activeTab === tab ? 600 : 400, color: activeTab === tab ? '#2563eb' : '#6b7280',
              borderBottom: activeTab === tab ? '2px solid #2563eb' : '2px solid transparent',
              marginBottom: -2, transition: 'all .15s',
            }}>
            {tab === 'overview' ? '概览' : '存档时间线'}
          </button>
        ))}
      </div>

      {activeTab === 'overview' ? (
        <div style={{ marginTop: 16 }}>
          <div className="card">
            <div className="card-header">战役信息</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: '.85rem' }}>
              <div><span style={{ color: '#6b7280' }}>Slug</span><br/>{campaign.slug}</div>
              <div><span style={{ color: '#6b7280' }}>修订版本</span><br/>{campaign.revision}</div>
              <div><span style={{ color: '#6b7280' }}>系统</span><br/>{campaign.system_id} {campaign.edition}</div>
              <div><span style={{ color: '#6b7280' }}>语言</span><br/>{campaign.locale === 'zh' ? '中文' : 'English'}</div>
            </div>
          </div>
        </div>
      ) : (
        <TimelineView saves={saves} campaignId={campaign.id} />
      )}
    </div>
  );
}

function TimelineView({ saves, campaignId }: { saves: SaveSlot[]; campaignId: string }) {
  return (
    <div style={{ marginTop: 16, position: 'relative', minHeight: 200 }}>
      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: 16, borderBottom: '1px solid #e5e7eb', fontSize: '.9rem', fontWeight: 600 }}>
          存档时间线 · 共 {saves.length} 个存档
        </div>
        {saves.length === 0 ? (
          <div style={{ padding: '40px 20px', textAlign: 'center', color: '#9ca3af' }}>暂无存档</div>
        ) : (
          <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 0, position: 'relative' }}>
            <div style={{ position: 'absolute', left: 28, top: 20, bottom: 20, width: 2, background: '#e5e7eb' }} />
            {saves.map((s, i) => (
              <div key={s.slot} style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '10px 0', position: 'relative' }}>
                <div style={{
                  width: 12, height: 12, borderRadius: '50%', background: i === 0 ? '#2563eb' : '#d1d5db',
                  border: '2px solid #fff', boxShadow: '0 0 0 2px #e5e7eb', flexShrink: 0, marginTop: 4, zIndex: 1,
                }} />
                <div>
                  <div style={{ fontWeight: 500, fontSize: '.9rem' }}>Slot {s.slot}{s.label ? ` · ${s.label}` : ''}</div>
                  <div style={{ fontSize: '.78rem', color: '#6b7280' }}>
                    {s.parent_slot ? `父存档: Slot ${s.parent_slot}` : '初始存档'}
                    {s.created_at ? ` · ${new Date(s.created_at).toLocaleString()}` : ''}
                  </div>
                  <div style={{ marginTop: 4, display: 'flex', gap: 4 }}>
                    <a href={`/saves/${campaignId}/${s.slot}`} className="btn btn-sm btn-ghost" style={{ fontSize: '.75rem' }}>查看</a>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
