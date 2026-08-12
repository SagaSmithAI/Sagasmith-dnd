import { useEffect, useMemo, useState } from 'react';
import { DEMO_MODE, MOCK_SCENE, sceneIndex, sceneProgress } from '../../lib/api';
import type { ModuleScene, SceneProgress } from '../../types';
import SpatialEvidenceDiagram from './SpatialEvidenceDiagram';

const DEMO_SCENES: ModuleScene[] = [
  { ...MOCK_SCENE, scene_id: 'scene-gate', stable_key: 'chapter-three-lockdown-gate', title: '封锁区入口', scene_ordinal: 0, scene_type: 'social', page_start: 38, page_end: 41, tags: ['social', 'gate'], visibility: 'group', keywords: ['守卫', '通行证'], headings: ['第三章', '封锁区入口'], content: '灰袍守卫封锁了通往钟楼的石桥。', spatial: { schema_version: 1, locations: [{ key: 'gate', title: '封锁门', kind: 'threshold', confidence: 'explicit' }], connections: [] } },
  MOCK_SCENE,
  { ...MOCK_SCENE, scene_id: 'scene-vault', stable_key: 'chapter-three-ember-vault', title: '余烬保险库', scene_ordinal: 2, scene_type: 'combat', page_start: 46, page_end: 50, tags: ['combat', 'dungeon'], visibility: 'restricted', keywords: ['保险库', '守卫构装体'], headings: ['第三章', '余烬保险库'], content: '保险库内的构装体仍处于待机状态。', spatial: { schema_version: 1, grid: { kind: 'square', cell_ft: 5 }, locations: [{ key: 'vault-floor', title: '保险库主层', kind: 'room', dimensions_ft: { width: 60, height: 45 }, confidence: 'explicit' }], connections: [] } },
];

const DEMO_PROGRESS: SceneProgress[] = [
  { scene_id: 'scene-gate', scope_id: 'party', status: 'previous', percent: 100, state_version: 2, state: {} },
  MOCK_SCENE.progress,
];

function placeholder(scene: ModuleScene): string {
  const allowed = new Set(['exploration', 'social', 'combat', 'dungeon', 'travel']);
  const key = allowed.has(scene.scene_type) ? scene.scene_type : 'unknown';
  return `/placeholders/scenes/${key}.svg`;
}

export default function SceneAtlas({ campaignId }: { campaignId: string }) {
  const [scenes, setScenes] = useState<ModuleScene[]>([]);
  const [progress, setProgress] = useState<SceneProgress[]>([]);
  const [scope, setScope] = useState('party');
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const requestedScope = query.get('scope') || 'party';
    setScope(requestedScope);
    Promise.all([sceneIndex(campaignId), sceneProgress(campaignId, requestedScope)])
      .then(([nextScenes, nextProgress]) => {
        setScenes(nextScenes);
        setProgress(nextProgress);
        setSelectedId(query.get('scene') || nextScenes[0]?.scene_id || '');
      })
      .catch((reason) => {
        if (!DEMO_MODE) {
          setError(reason instanceof Error ? reason.message : String(reason));
          return;
        }
        setDemo(true);
        setScenes(DEMO_SCENES);
        setProgress(DEMO_PROGRESS);
        setSelectedId(query.get('scene') || DEMO_SCENES[1].scene_id);
      });
  }, [campaignId]);

  const types = useMemo(() => [...new Set(scenes.map((scene) => scene.scene_type))], [scenes]);
  const filtered = useMemo(() => scenes.filter((scene) => {
    const matchesType = filter === 'all' || scene.scene_type === filter;
    const haystack = `${scene.title} ${scene.chapter} ${scene.keywords.join(' ')} ${scene.tags.join(' ')}`.toLowerCase();
    return matchesType && (!search || haystack.includes(search.toLowerCase()));
  }), [filter, scenes, search]);
  const chapters = useMemo(() => [...new Map(scenes.map((scene) => [scene.chapter_id || scene.chapter, scene.chapter])).entries()], [scenes]);
  const progressByScene = useMemo(() => new Map(progress.map((item) => [item.scene_id, item])), [progress]);
  const selected = scenes.find((scene) => scene.scene_id === selectedId) || filtered[0] || scenes[0];
  const selectedProgress = selected ? progressByScene.get(selected.scene_id) : undefined;

  const selectScene = (scene: ModuleScene) => {
    setSelectedId(scene.scene_id);
    const query = new URLSearchParams(window.location.search);
    query.set('id', campaignId);
    query.set('tab', 'scenes');
    query.set('scene', scene.scene_id);
    query.set('scope', scope);
    query.set('view', 'atlas');
    window.history.replaceState({}, '', `${window.location.pathname}?${query}`);
  };

  return (
    <section className="scene-atlas">
      <header className="atlas-toolbar">
        <div><span>SCENE ATLAS</span><strong>叙事索引与空间证据</strong></div>
        <div className="atlas-controls">
          <label>PROJECTION<input value={scope} onChange={(event) => setScope(event.target.value)} aria-label="Scene projection scope" /></label>
          <input className="form-input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索标题、章节、标签…" />
        </div>
      </header>
      {demo && <div className="atlas-demo">DEMO PROJECTION · 真实连接后由服务端按 principal 过滤，不在 CSS 中隐藏内容。</div>}
      {error && <div className="atlas-demo">RUNTIME OFFLINE · {error}</div>}
      <div className="atlas-filters">
        <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>ALL / {scenes.length}</button>
        {types.map((type) => <button key={type} className={filter === type ? 'active' : ''} onClick={() => setFilter(type)}>{type.toUpperCase()}</button>)}
      </div>
      <div className="atlas-layout">
        <nav className="atlas-chapters" aria-label="Module chapters">
          <small>CHAPTERS</small>
          {chapters.map(([key, title], index) => <button key={key} onClick={() => {
            const scene = scenes.find((item) => (item.chapter_id || item.chapter) === key);
            if (scene) selectScene(scene);
          }}><i>{String(index + 1).padStart(2, '0')}</i><span>{title}</span><b>{scenes.filter((item) => (item.chapter_id || item.chapter) === key).length}</b></button>)}
        </nav>
        <div className="atlas-scenes">
          {filtered.map((scene) => {
            const state = progressByScene.get(scene.scene_id);
            return <button key={scene.scene_id} className={`atlas-scene-card ${selected?.scene_id === scene.scene_id ? 'selected' : ''}`} onClick={() => selectScene(scene)}>
              <img src={placeholder(scene)} alt="" />
              <span className="atlas-scene-order">{String((scene.scene_ordinal ?? 0) + 1).padStart(2, '0')}</span>
              <div><small>{scene.chapter}</small><strong>{scene.title}</strong><p>{scene.tags.slice(0, 3).join(' · ') || scene.scene_type}</p></div>
              <aside><em className={`visibility ${scene.visibility}`}>{scene.visibility}</em><b>{state ? `${state.percent}%` : '—'}</b></aside>
            </button>;
          })}
          {!filtered.length && <div className="empty">没有匹配的场景。</div>}
        </div>
        <aside className="atlas-detail">
          {selected ? <>
            <img className="atlas-cover" src={placeholder(selected)} alt="" />
            <div className="atlas-detail-copy">
              <header><span>{selected.scene_type.toUpperCase()}</span><em>{selected.visibility}</em></header>
              <small>{selected.module} / {selected.chapter}</small>
              <h2>{selected.title}</h2>
              <p>{selected.content || '此投影未包含场景正文。'}</p>
              <dl>
                <div><dt>STABLE KEY</dt><dd>{selected.stable_key || 'NOT PROVIDED'}</dd></div>
                <div><dt>PROGRESS</dt><dd>{selectedProgress?.percent ?? 0}% · {selectedProgress?.status || 'untracked'}</dd></div>
                <div><dt>LOCATION</dt><dd>{selectedProgress?.current_location_key || selectedProgress?.current_room || 'UNSET'}</dd></div>
                <div><dt>SOURCE</dt><dd>{selected.page_start ? `P.${selected.page_start}${selected.page_end && selected.page_end !== selected.page_start ? `–${selected.page_end}` : ''}` : `LINES ${selected.start_line || '?'}–${selected.end_line || '?'}`}</dd></div>
              </dl>
            </div>
            <SpatialEvidenceDiagram spatial={selected.spatial} />
          </> : <div className="empty">选择一个场景。</div>}
        </aside>
      </div>
    </section>
  );
}
