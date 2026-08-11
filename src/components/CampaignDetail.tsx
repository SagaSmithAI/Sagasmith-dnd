import { useEffect, useState } from 'react';
import {
  MOCK_CHARACTERS,
  MOCK_MODULES,
  MOCK_SAVES,
  MOCK_SCENE,
  currentScene,
  emitRuntimeStatus,
  getCampaign,
  listCharacters,
  listModules,
  listSaves,
  mockCampaign,
  mockCharactersFor,
} from '../lib/api';
import type { Campaign, Character, CurrentScene, ModuleSource, SaveSlot } from '../types';
import CampaignContentPanel from '../features/content/CampaignContentPanel';
import SceneIndex from './SceneIndex';

type Tab = 'overview' | 'content' | 'scenes' | 'knowledge' | 'timeline';

export default function CampaignDetail() {
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [modules, setModules] = useState<ModuleSource[]>([]);
  const [saves, setSaves] = useState<SaveSlot[]>([]);
  const [scene, setScene] = useState<CurrentScene | null>(null);
  const [tab, setTab] = useState<Tab>('overview');
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const id = query.get('id') || 'campaign-1';
    const requestedTab = query.get('tab');
    if (requestedTab && ['overview', 'content', 'scenes', 'knowledge', 'timeline'].includes(requestedTab)) setTab(requestedTab as Tab);
    Promise.all([getCampaign(id), listCharacters(id), listModules(id), listSaves(id), currentScene(id)])
      .then(([nextCampaign, nextCharacters, nextModules, nextSaves, nextScene]) => {
        setCampaign(nextCampaign); setCharacters(nextCharacters); setModules(nextModules); setSaves(nextSaves); setScene(nextScene); emitRuntimeStatus(true);
      })
      .catch(() => {
        const fallback = mockCampaign(id);
        setCampaign(fallback);
        setCharacters(mockCharactersFor(fallback.id).length ? mockCharactersFor(fallback.id) : MOCK_CHARACTERS.slice(0, 2));
        setModules(fallback.id === 'campaign-1' ? MOCK_MODULES : []);
        setSaves(fallback.id === 'campaign-1' ? MOCK_SAVES : MOCK_SAVES.slice(-1));
        setScene(fallback.id === 'campaign-1' ? MOCK_SCENE : null);
        setDemo(true); emitRuntimeStatus(false);
      });
  }, []);

  if (!campaign) return <div className="page"><div className="empty">正在读取战役档案…</div></div>;
  const phase = String(campaign.state?.game_phase || 'lobby');
  const chooseTab = (next: Tab) => {
    setTab(next);
    const query = new URLSearchParams(window.location.search);
    query.set('id', campaign.id);
    query.set('tab', next);
    window.history.replaceState({}, '', `${window.location.pathname}?${query}`);
  };

  return (
    <div className="page">
      <div className="page-heading">
        <div><div className="eyebrow">CAMPAIGN DOSSIER / {campaign.slug.toUpperCase()}</div><h1>{campaign.name}</h1><p>{campaign.description || '暂无战役摘要。'}</p></div>
        <div className="heading-actions"><a href="/campaigns" className="btn btn-ghost">← 战役档案</a><span className={`badge ${campaign.status === 'active' ? 'badge-green' : 'badge-gray'}`}>{campaign.status}</span></div>
      </div>
      {demo && <div className="demo-notice"><strong>DEMO DATA</strong><span>详情来自本地演示集；MCP 仍是所有权威状态的唯一所有者。</span></div>}

      <section className="campaign-banner">
        <div><span>RULE PROFILE</span><strong>{String(campaign.settings?.rule_profile || `D&D 5E CORE ${campaign.edition}`)}</strong></div>
        <div><span>PHASE</span><strong className="accent">{phase.toUpperCase()}</strong></div>
        <div><span>REVISION</span><strong>{campaign.revision}</strong></div>
        <div><span>BRANCH HEAD</span><strong>{saves[0]?.label || 'NO SNAPSHOT'}</strong></div>
      </section>

      <div className="tabs campaign-tabs">
        {(['overview', 'content', 'scenes', 'knowledge', 'timeline'] as Tab[]).map((item) => <button key={item} className={`tab ${tab === item ? 'active' : ''}`} onClick={() => chooseTab(item)}>{item === 'overview' ? '桌面概览' : item === 'content' ? '内容配置' : item === 'scenes' ? '场景索引' : item === 'knowledge' ? '角色认知' : '分支存档'}</button>)}
      </div>

      <div className="tab-content">
        {tab === 'overview' && <Overview campaign={campaign} characters={characters} modules={modules} scene={scene} saves={saves} />}
        {tab === 'content' && <CampaignContentPanel campaign={campaign} />}
        {tab === 'scenes' && <div id="scene"><SceneIndex campaignId={campaign.id} /></div>}
        {tab === 'knowledge' && <KnowledgeView characters={characters} />}
        {tab === 'timeline' && <TimelineView saves={saves} />}
      </div>
    </div>
  );
}

function Overview({ campaign, characters, modules, scene, saves }: { campaign: Campaign; characters: Character[]; modules: ModuleSource[]; scene: CurrentScene | null; saves: SaveSlot[] }) {
  return (
    <div className="grid-2 campaign-overview">
      <section className="card">
        <div className="card-header"><strong>ACTORS</strong><span>{characters.length} VISIBLE</span></div>
        {characters.map((character) => {
          const sheet = character.sheet as Record<string, any>;
          return <a className="list-row" key={character.id} href={`/characters/detail?id=${encodeURIComponent(character.id)}`}><div><div className="list-row-title">{character.name}</div><div className="list-row-meta">{character.character_type.toUpperCase()} · {sheet.class || '—'} {sheet.level ? `LV.${sheet.level}` : ''} · REV {character.revision}</div></div><span className="badge badge-gray">{(character.notes as any)?.knowledge_count || 0} FACTS</span></a>;
        })}
        {characters.length === 0 && <div className="empty">没有可见角色。</div>}
      </section>
      <section className="card">
        <div className="card-header"><strong>CURRENT SCENE</strong><span>{scene?.scope_id || 'party'}</span></div>
        <div className="current-scene-card"><span>{scene?.chapter || 'NO CHAPTER'}</span><h3>{scene?.title || '尚未设置当前场景'}</h3><p>{scene?.content || '让 Agent 在 play.scene 中选择并读取当前场景。'}</p><div className="progress-bar"><div className="progress-fill" style={{ width: `${scene?.progress?.percent || 0}%` }} /></div><footer><span>{scene?.progress?.current_location_key || scene?.progress?.current_room || 'ROOM UNSET'}</span><b>{scene?.progress?.percent || 0}%</b></footer></div>
      </section>
      <section className="card">
        <div className="card-header"><strong>MODULE SOURCES</strong><span>{modules.length} ACTIVE</span></div>
        {modules.map((module) => <div className="list-row" key={module.id}><div><div className="list-row-title">{module.title}</div><div className="list-row-meta">{module.source_key} · {module.parser_profile}</div></div><span className={`badge ${module.warnings.length ? 'badge-orange' : 'badge-green'}`}>{module.warnings.length ? `${module.warnings.length} WARN` : 'VALID'}</span></div>)}
        {modules.length === 0 && <div className="empty">没有活动模组。</div>}
      </section>
      <section className="card">
        <div className="card-header"><strong>CONTINUITY</strong><span>{saves.length} SNAPSHOTS</span></div>
        <div className="continuity-summary"><div><small>CAMPAIGN ID</small><b>{campaign.id}</b></div><div><small>SYSTEM</small><b>{campaign.system_id} / {campaign.edition}</b></div><div><small>LOCALE</small><b>{campaign.locale}</b></div><div><small>ACTIVE HEAD</small><b>{saves[0]?.label || '—'}</b></div></div>
      </section>
    </div>
  );
}

function KnowledgeView({ characters }: { characters: Character[] }) {
  return (
    <div className="knowledge-grid">
      {characters.map((character) => (
        <article className="knowledge-dossier" key={character.id}>
          <header><span>{character.character_type.toUpperCase()}</span><b>ACTOR-SCOPED</b></header>
          <h3>{character.name}</h3><p>{character.summary}</p>
          <div><span>VISIBLE FACTS</span><strong>{String((character.notes as any)?.knowledge_count || 0).padStart(2, '0')}</strong></div>
          <footer><span>branch / current ancestry</span><em>{(character.notes as any)?.private ? 'PRIVATE FIELDS SEALED' : 'PLAYER VIEW SAFE'}</em></footer>
        </article>
      ))}
      {characters.length === 0 && <div className="empty card">没有可见 actor knowledge。</div>}
    </div>
  );
}

function TimelineView({ saves }: { saves: SaveSlot[] }) {
  return (
    <div className="timeline card">
      <div className="card-header"><strong>SNAPSHOT LINEAGE</strong><span>ACTIVE ANCESTRY ONLY</span></div>
      <div className="timeline-body">
        {saves.map((save, index) => <div className="timeline-row" key={save.slot}><span className={index === 0 ? 'active' : ''}></span><div><small>SLOT {save.slot}{save.parent_slot ? ` ← ${save.parent_slot}` : ' · ROOT'}</small><h4>{save.label || 'Untitled snapshot'}</h4><p>{save.created_at ? new Date(save.created_at).toLocaleString('zh-CN') : '时间未知'}</p></div><b>{index === 0 ? 'HEAD' : 'ANCESTOR'}</b></div>)}
        {saves.length === 0 && <div className="empty">尚无 Snapshot。</div>}
      </div>
    </div>
  );
}
