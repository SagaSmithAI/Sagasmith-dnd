import { useEffect, useMemo, useState } from 'react';
import { MOCK_SCENE, sceneIndex } from '../lib/api';
import type { ModuleScene } from '../types';

const DEMO_SCENES: ModuleScene[] = [
  { ...MOCK_SCENE, scene_id: 'scene-gate', title: '封锁区入口', chapter: '第三章 · 断钟', scene_type: 'social', page_start: 38, page_end: 41, tags: ['social', 'gate'], visibility: 'player', keywords: ['守卫', '通行证'], headings: ['第三章', '封锁区入口'], content: '灰袍守卫封锁了通往钟楼的石桥。' },
  MOCK_SCENE,
  { ...MOCK_SCENE, scene_id: 'scene-vault', title: '余烬保险库', chapter: '第三章 · 断钟', scene_type: 'combat', page_start: 46, page_end: 50, tags: ['combat', 'dungeon'], visibility: 'keeper', keywords: ['保险库', '守卫构装体'], headings: ['第三章', '余烬保险库'], content: '未解锁的战斗场景；玩家视图不应看到 Keeper 正文。' },
];

export default function SceneIndex({ campaignId }: { campaignId: string }) {
  const [scenes, setScenes] = useState<ModuleScene[]>([]);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');

  useEffect(() => { sceneIndex(campaignId).then(setScenes).catch(() => setScenes(DEMO_SCENES)); }, [campaignId]);

  const types = useMemo(() => [...new Set(scenes.map((scene) => scene.scene_type))], [scenes]);
  const filtered = scenes.filter((scene) => (filter === 'all' || scene.scene_type === filter) && (!search || `${scene.title} ${scene.keywords.join(' ')}`.toLowerCase().includes(search.toLowerCase())));

  return (
    <div>
      <div className="filter-row">
        <button className={`btn btn-sm ${filter === 'all' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setFilter('all')}>全部</button>
        {types.map((type) => <button key={type} className={`btn btn-sm ${filter === type ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setFilter(type)}>{type}</button>)}
        <input className="form-input" placeholder="搜索场景、房间或关键词…" value={search} onChange={(event) => setSearch(event.target.value)} />
      </div>
      <div className="scene-list card">
        {filtered.map((scene, index) => (
          <article className="scene-row" key={scene.scene_id}>
            <span className="scene-number">{String(index + 1).padStart(2, '0')}</span>
            <div><div className="scene-path">{scene.chapter} · {scene.module}</div><h4>{scene.title}</h4><p>{scene.visibility === 'keeper' ? 'Keeper-only scene content is sealed in this audience view.' : scene.content || 'No preview text available.'}</p><div className="scene-tags"><span className="badge badge-blue">{scene.scene_type}</span>{scene.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div></div>
            <aside><span className={`badge ${scene.visibility === 'keeper' ? 'badge-orange' : 'badge-green'}`}>{scene.visibility}</span><small>{scene.page_start ? `P.${scene.page_start}${scene.page_end && scene.page_end !== scene.page_start ? `–${scene.page_end}` : ''}` : 'NO PAGE'}</small></aside>
          </article>
        ))}
        {filtered.length === 0 && <div className="empty">没有匹配的场景。</div>}
      </div>
    </div>
  );
}
