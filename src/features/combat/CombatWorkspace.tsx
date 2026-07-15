import { useCallback, useEffect, useMemo, useState } from 'react';
import { MOCK_COMBAT, combatStatus, submitCombatMove, subscribeCampaign } from '../../lib/api';
import type { CombatStatus, GridPosition } from '../../types';
import CombatMapCanvas from './CombatMapCanvas';

export default function CombatWorkspace() {
  const [campaignId, setCampaignId] = useState('campaign-1');
  const [combat, setCombat] = useState<CombatStatus | null>(null);
  const [selectedActorId, setSelectedActorId] = useState('');
  const [demo, setDemo] = useState(false);
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async (id: string) => {
    try {
      const next = await combatStatus(id);
      setCombat(next);
      setDemo(false);
      setSelectedActorId((current) => current || next?.combatants?.[next.turn_index || 0]?.actor_id || '');
    } catch {
      setCombat(MOCK_COMBAT);
      setDemo(true);
      setSelectedActorId((current) => current || MOCK_COMBAT.combatants[MOCK_COMBAT.turn_index || 0]?.actor_id || '');
    }
  }, []);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get('campaign') || 'campaign-1';
    setCampaignId(id);
    load(id);
    return subscribeCampaign(id, () => load(id));
  }, [load]);

  const ordered = useMemo(() => [...(combat?.combatants || [])].sort((left, right) => right.initiative - left.initiative), [combat]);
  const selected = combat?.combatants.find((item) => item.actor_id === selectedActorId);
  const current = combat?.combatants[combat.turn_index || 0];

  const move = async (actorId: string, destination: GridPosition, distance: number) => {
    if (!combat) return;
    setMessage(`PREVIEW · ${distance} FT → (${destination.x}, ${destination.y})`);
    if (demo) {
      setCombat({ ...combat, combatants: combat.combatants.map((item) => item.actor_id === actorId ? { ...item, position: destination } : item) });
      setMessage('DEMO ONLY · 本地预览已更新，没有写入任何战役状态。');
      return;
    }
    if (combat.campaign_revision == null) {
      setMessage('MOVE NOT SENT · gateway 未提供 campaign_revision。');
      return;
    }
    setSubmitting(true);
    try {
      const next = await submitCombatMove(campaignId, actorId, destination, distance, combat.campaign_revision, combat.branch_id);
      setCombat(next);
      setMessage('MCP ACCEPTED · 移动、阻挡与反应窗口已由规则引擎复核。');
    } catch (error) {
      setMessage(`MCP REJECTED · ${error instanceof Error ? error.message : 'unknown error'}`);
      await load(campaignId);
    } finally {
      setSubmitting(false);
    }
  };

  if (!combat) return <div className="page"><div className="empty">正在读取战斗状态…</div></div>;
  if (!combat.battle_map) return <div className="page"><div className="page-heading"><div><div className="eyebrow">COMBAT WORKSPACE</div><h1>当前没有战斗地图</h1><p>战斗开始时，MCP 会从当前 Scene Spatial 证据创建一张临时地图。</p></div></div><div className="combat-boundary card">Scene Spatial 不会直接当作地图。没有显式证据时，系统也不会猜测墙体、掩体或视线。</div></div>;

  return (
    <div className="page combat-page">
      <div className="page-heading">
        <div><div className="eyebrow">LIVE COMBAT / {combat.active ? `ROUND ${combat.round || 1}` : 'FINAL STATE'}</div><h1>临时战斗地图</h1><p>拖动 Token 只会生成移动请求；MCP 负责验证五尺格、阻挡、距离、权限与借机攻击窗口。</p></div>
        <div className="heading-actions"><a className="btn btn-ghost" href={`/campaigns/detail?id=${encodeURIComponent(campaignId)}&tab=scenes`}>查看来源场景</a><span className={`badge ${combat.active ? 'badge-orange' : 'badge-gray'}`}>{combat.active ? 'ACTIVE' : 'READ ONLY'}</span></div>
      </div>
      {demo && <div className="demo-notice"><strong>DEMO MAP</strong><span>这里可以试拖动，但只改本地演示状态。真实写入必须由 gateway 调用 MCP 工具。</span></div>}
      <div className="combat-shell">
        <aside className="initiative-rail card">
          <div className="card-header"><strong>INITIATIVE</strong><span>R{combat.round || 1}</span></div>
          {ordered.map((actor) => <button key={actor.actor_id} className={`${selectedActorId === actor.actor_id ? 'selected' : ''} ${current?.actor_id === actor.actor_id ? 'current' : ''}`} onClick={() => setSelectedActorId(actor.actor_id)}>
            <b>{actor.initiative}</b><span><strong>{actor.name}</strong><small>{actor.conditions?.join(' · ') || actor.disposition || 'visible combatant'}</small></span>{current?.actor_id === actor.actor_id && <em>TURN</em>}
          </button>)}
        </aside>
        <main className="combat-map-panel">
          <div className="combat-map-toolbar"><span>MAP REV {combat.battle_map.map_revision || 1}</span><span>GRID {combat.battle_map.grid.cell_ft} FT</span><span>{combat.battle_map.bounds.width_cells} × {combat.battle_map.bounds.height_cells}</span><span>{submitting ? 'SUBMITTING…' : 'DRAG TO PROPOSE MOVE'}</span></div>
          <div className="combat-map-frame" style={{ backgroundImage: 'url(/placeholders/maps/stone.svg)' }}>
            <CombatMapCanvas battleMap={combat.battle_map} combatants={combat.combatants} selectedActorId={selectedActorId} onSelect={setSelectedActorId} onMove={move} />
          </div>
          <div className={`combat-message ${message.startsWith('MCP REJECTED') ? 'error' : ''}`}>{message || 'READY · 选择或拖动可见 Token。最终合法性以 MCP 返回为准。'}</div>
        </main>
        <aside className="map-inspector card">
          <div className="card-header"><strong>INSPECTOR</strong><span>SERVER VIEW</span></div>
          {selected ? <div className="token-dossier"><img src={`/placeholders/tokens/${selected.disposition === 'hostile' ? 'hostile' : selected.disposition === 'neutral' ? 'neutral' : 'pc'}.svg`} alt="" /><span>{selected.disposition || 'VISIBLE'}</span><h2>{selected.name}</h2><dl><div><dt>INITIATIVE</dt><dd>{selected.initiative}</dd></div><div><dt>POSITION</dt><dd>{selected.position ? `${selected.position.x}, ${selected.position.y}` : 'UNSET'}</dd></div><div><dt>REACH</dt><dd>{selected.reach_ft ? `${selected.reach_ft} FT` : 'SERVER SEALED'}</dd></div><div><dt>HP</dt><dd>{selected.hp?.current != null ? `${selected.hp.current} / ${selected.hp.max ?? '?'}` : 'SERVER SEALED'}</dd></div></dl></div> : <div className="empty">选择一个 Token。</div>}
          <div className="map-legend"><strong>MAP LEGEND</strong><span><i className="friendly"></i>友方 / 玩家</span><span><i className="hostile"></i>敌对</span><span><i className="neutral"></i>中立</span><span><i className="difficult"></i>困难地形（仅显示）</span><span><i className="blocked"></i>不可进入</span></div>
          <div className="combat-boundary"><strong>NOT AUTOMATED</strong><p>墙体、视线、掩体、高度、体型占位与困难地形移动消耗仍需 DM 判定；界面不会伪造这些机制。</p></div>
        </aside>
      </div>
      <div className="combat-accessibility card"><strong>ACCESSIBLE TOKEN LIST</strong>{combat.combatants.map((actor) => <span key={actor.actor_id}>{actor.name}: initiative {actor.initiative}, position {actor.position ? `${actor.position.x},${actor.position.y}` : 'unset'}</span>)}</div>
    </div>
  );
}
