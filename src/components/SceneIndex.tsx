import { useState, useEffect } from 'react';
import { sceneIndex } from '../lib/api';
import type { ModuleScene } from '../types';

export default function SceneIndex({ campaignId }: { campaignId: string }) {
  const [scenes, setScenes] = useState<ModuleScene[]>([]);
  const [filter, setFilter] = useState<string>('all');
  const [search, setSearch] = useState('');

  useEffect(() => { sceneIndex(campaignId).then(setScenes).catch(() => {}); }, [campaignId]);

  const types = [...new Set(scenes.map(s => s.scene_type))];
  const filtered = scenes.filter(s => {
    if (filter !== 'all' && s.scene_type !== filter) return false;
    if (search && !s.title.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const byModule: Record<string, ModuleScene[]> = {};
  filtered.forEach(s => {
    const key = s.module || '未知';
    if (!byModule[key]) byModule[key] = [];
    byModule[key].push(s);
  });

  return (
    <div>
      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <button className={`btn btn-sm ${filter === 'all' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setFilter('all')}>全部</button>
        {types.map(t => (
          <button key={t} className={`btn btn-sm ${filter === t ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setFilter(t)}>
            {t === 'combat' ? '⚔️' : t === 'social' ? '💬' : t === 'exploration' ? '🗺️' : t === 'reference' ? '📖' : '🏷️'} {t}
          </button>
        ))}
        <input
          placeholder="搜索场景..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ marginLeft: 'auto', padding: '6px 12px', borderRadius: 6, border: '1px solid #e5e7eb', fontSize: '.85rem', width: 200 }}
        />
      </div>

      {/* Scene tree */}
      {Object.entries(byModule).map(([module, modScenes]) => (
        <div key={module} className="card" style={{ marginBottom: 12, padding: 0 }}>
          <div style={{ padding: '12px 16px', borderBottom: '1px solid #e5e7eb', fontWeight: 600, fontSize: '.9rem' }}>
            📂 {module} · {modScenes.length} 场景
          </div>
          <div style={{ padding: '8px 0' }}>
            {modScenes.map((s, i) => (
              <div key={s.scene_id || i} style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '8px 16px',
                borderBottom: i < modScenes.length - 1 ? '1px solid #f3f4f6' : 'none',
                cursor: 'pointer', transition: 'background .1s', fontSize: '.88rem',
              }} onClick={() => alert(`场景: ${s.title}\n类型: ${s.scene_type}\n可见性: ${s.visibility}\n${s.content?.substring(0, 200) || '(无内容预览)'}`)}>
                <span>{s.scene_type === 'combat' ? '⚔️' : s.scene_type === 'social' ? '💬' : s.scene_type === 'exploration' ? '🗺️' : s.scene_type === 'reference' ? '📖' : '📄'}</span>
                <div style={{ flex: 1 }}>
                  <div>{s.title}</div>
                  <div style={{ fontSize: '.75rem', color: '#9ca3af', display: 'flex', gap: 4 }}>
                    <span className="badge badge-gray" style={{ fontSize: '.68rem' }}>{s.scene_type}</span>
                    {s.tags?.map(t => <span key={t} className="tag" style={{ fontSize: '.68rem' }}>{t}</span>)}
                    {s.page_start && <span>p.{s.page_start}{s.page_end !== s.page_start ? `-${s.page_end}` : ''}</span>}
                  </div>
                </div>
                <span style={{ fontSize: '.75rem', color: '#2563eb' }}>{s.visibility}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
      {filtered.length === 0 && <p style={{ color: '#9ca3af', textAlign: 'center', padding: 40 }}>无匹配场景</p>}
    </div>
  );
}
