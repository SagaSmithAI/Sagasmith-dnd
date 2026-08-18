import { useEffect, useMemo, useState } from 'react';
import {
  DEMO_MODE,
  MOCK_CAMPAIGNS,
  MOCK_CHARACTERS,
  MOCK_MODULES,
  MOCK_SAVES,
  MOCK_SCENE,
  currentScene,
  emitRuntimeStatus,
  health,
  listCampaigns,
  listCharacters,
  listModules,
  listSaves,
} from '../lib/api';
import type { Campaign, Character, CurrentScene, HealthStatus } from '../types';

type Connection = 'loading' | 'connected' | 'demo' | 'offline';

export default function Dashboard() {
  const [connection, setConnection] = useState<Connection>('loading');
  const [healthData, setHealthData] = useState<HealthStatus | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [scene, setScene] = useState<CurrentScene | null>(null);
  const [stats, setStats] = useState({ modules: 0, saves: 0 });
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const runtime = await health();
        const liveCampaigns = await listCampaigns();
        const active = liveCampaigns.find((item) => item.status === 'active') || liveCampaigns[0];
        const [liveCharacters, modules, saves, liveScene] = active
          ? await Promise.all([
              listCharacters(active.id).catch(() => []),
              listModules(active.id).catch(() => []),
              listSaves(active.id).catch(() => []),
              currentScene(active.id).catch(() => null),
            ])
          : [[], [], [], null];
        if (cancelled) return;
        setConnection('connected');
        setHealthData(runtime);
        setCampaigns(liveCampaigns);
        setCharacters(liveCharacters);
        setScene(liveScene);
        setStats({ modules: modules.length, saves: saves.length });
        emitRuntimeStatus(true, runtime.version);
      } catch (reason) {
        if (cancelled) return;
        emitRuntimeStatus(false);
        if (!DEMO_MODE) {
          setConnection('offline');
          setError(reason instanceof Error ? reason.message : String(reason));
          return;
        }
        setConnection('demo');
        setCampaigns(MOCK_CAMPAIGNS);
        setCharacters(MOCK_CHARACTERS);
        setScene(MOCK_SCENE);
        setStats({ modules: MOCK_MODULES.length, saves: MOCK_SAVES.length });
      }
    };
    load();
    return () => { cancelled = true; };
  }, []);

  const active = useMemo(() => campaigns.find((item) => item.status === 'active') || campaigns[0], [campaigns]);
  const phase = String(active?.state?.game_phase || 'play');

  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">LIVE TABLE / {connection === 'connected' ? 'RUNTIME CONNECTED' : connection === 'demo' ? 'DEMO ENABLED' : connection === 'offline' ? 'RUNTIME OFFLINE' : 'CONNECTING'}</div>
          <h1>今晚的桌面</h1>
          <p>把战役连续性、当前场景、角色认知与 MCP 工具阶段放在同一个开团视图中。</p>
        </div>
        <div className="heading-actions">
          <a className="btn btn-ghost" href="/rules">检查规则来源</a>
          <a className="btn btn-primary" href={active ? `/campaigns/detail?id=${encodeURIComponent(active.id)}` : '/campaigns'}>打开当前战役</a>
        </div>
      </div>

      {connection === 'demo' && (
        <div className="demo-notice">
          <strong>DEMO DATA</strong>
          <span>未发现兼容的只读 gateway，界面正在使用本地演示数据。权威写入始终属于 D&D MCP。</span>
        </div>
      )}
      {connection === 'offline' && (
        <div className="demo-notice"><strong>RUNTIME OFFLINE</strong><span>{error}</span></div>
      )}

      <section className="table-hero">
        <div className="table-hero-copy">
          <div className="table-meta"><span className="badge badge-green">{active?.status || 'loading'}</span><span>D&D 5E {active?.edition || '—'}</span><span>REV {active?.revision ?? '—'}</span></div>
          <h2>{active?.name || '正在读取战役…'}</h2>
          <p>{active?.description || '等待 compatible gateway 返回当前桌面。'}</p>
          <div className="phase-switch" aria-label="Current game phase">
            {['lobby', 'play', 'combat'].map((item, index) => (
              <div key={item} className={`${phase === item ? 'active' : ''} ${['lobby', 'play', 'combat'].indexOf(phase) > index ? 'past' : ''}`}>
                <small>0{index + 1}</small><strong>{item.toUpperCase()}</strong><span>{item === 'lobby' ? '准备' : item === 'play' ? '探索' : '结算'}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="scene-focus">
          <header><span>CURRENT SCENE</span><em>{scene?.scope_id || 'party'}</em></header>
          <small>{scene?.chapter || 'NO ACTIVE CHAPTER'}</small>
          <h3>{scene?.title || '尚未选择场景'}</h3>
          <p>{scene?.content || '从战役详情进入场景索引，或让 Agent 通过 play.scene 读取当前场景。'}</p>
          <div className="scene-progress"><div><i style={{ width: `${scene?.progress?.percent || 0}%` }} /></div><b>{scene?.progress?.percent || 0}%</b></div>
          <footer><span>{scene?.progress?.current_location_key || scene?.progress?.current_room || 'ROOM UNSET'}</span><a href={active ? `/campaigns/detail?id=${encodeURIComponent(active.id)}&tab=scenes&scene=${encodeURIComponent(scene?.scene_id || '')}` : '/campaigns'}>VIEW SCENE →</a></footer>
        </div>
      </section>

      <section className="stat-grid dashboard-stats">
        <div className="stat-card"><small>01</small><div className="stat-number">{campaigns.length}</div><div className="stat-label">Campaigns</div></div>
        <div className="stat-card"><small>02</small><div className="stat-number">{characters.length}</div><div className="stat-label">Actors at this table</div></div>
        <div className="stat-card"><small>03</small><div className="stat-number">{stats.modules}</div><div className="stat-label">Active modules</div></div>
        <div className="stat-card"><small>04</small><div className="stat-number">{stats.saves}</div><div className="stat-label">Branch snapshots</div></div>
      </section>

      <div className="dashboard-grid">
        <section className="card actor-panel">
          <div className="card-header"><strong>PARTY & ACTOR KNOWLEDGE</strong><a href={active ? `/campaigns/detail?id=${encodeURIComponent(active.id)}` : '/campaigns'}>ALL ACTORS →</a></div>
          <div>
            {characters.slice(0, 5).map((character) => {
              const sheet = character.sheet as Record<string, any>;
              const hp = sheet.hp || {};
              return (
                <a className="actor-row" key={character.id} href={`/characters/detail?campaign=${encodeURIComponent(active?.id || character.campaign_id || '')}&id=${encodeURIComponent(character.id)}`}>
                  <span className={`actor-sigil ${character.character_type}`}>{character.name.slice(0, 1)}</span>
                  <span className="actor-info"><strong>{character.name}</strong><small>{character.character_type.toUpperCase()} · {sheet.class || 'UNCLASSIFIED'} {sheet.level ? `LV.${sheet.level}` : ''}</small></span>
                  <span className="actor-hp"><b>{hp.current ?? '—'}</b><small>/ {hp.max ?? '—'} HP</small></span>
                  <span className="knowledge-count"><b>{String((character.notes as any)?.knowledge_count || 0).padStart(2, '0')}</b><small>KNOWN</small></span>
                </a>
              );
            })}
            {characters.length === 0 && <div className="empty">当前战役没有可见角色。</div>}
          </div>
        </section>

        <section className="card operation-panel">
          <div className="card-header"><strong>MCP SESSION SURFACE</strong><span className={`badge ${connection === 'connected' ? 'badge-green' : 'badge-orange'}`}>{connection}</span></div>
          <div className="operation-body">
            <p>当前阶段建议只向 Agent 暴露与桌面任务匹配的能力组。</p>
            <div className="tool-group active"><span>PLAY.SCENE</span><small>scene · event · continuity · memory</small><b>LOADED</b></div>
            <div className="tool-group active"><span>PLAY.RESOLUTION</span><small>checks · dice · rules · combat start</small><b>LOADED</b></div>
            <div className="tool-group"><span>COMBAT.ACTIONS</span><small>attacks · spells · movement · reactions</small><b>LOCKED</b></div>
            <div className="operation-boundary"><strong>SERVER ENFORCED</strong><span>phase · campaign · principal · role · TTL</span></div>
          </div>
        </section>
      </div>

      <section className="quick-strip">
        <div><span>QUICK OPERATIONS</span><p>浏览界面用于观察与导航；状态提交仍通过 Agent + MCP。</p></div>
        <a href="/campaigns">战役档案 <b>→</b></a>
        <a href="/rules">规则来源 <b>→</b></a>
        <a href="https://github.com/SagaSmithAI/SagaSmith-dnd-mcp" target="_blank" rel="noreferrer">MCP Contract <b>↗</b></a>
      </section>
    </div>
  );
}
