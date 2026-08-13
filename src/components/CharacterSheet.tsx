import { useEffect, useState } from 'react';
import { DEMO_MODE, emitRuntimeStatus, getCharacter, mockCharacter } from '../lib/api';
import type { Character } from '../types';
import { ABILITY_LABELS, ABILITY_NAMES_EN, SKILL_NAMES } from '../types';

export default function CharacterSheet() {
  const [character, setCharacter] = useState<Character | null>(null);
  const [showRaw, setShowRaw] = useState(false);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const id = query.get('id') || 'char-varis';
    const campaignId = query.get('campaign');
    if (!campaignId && !DEMO_MODE) {
      setError('campaign query parameter is required');
      return;
    }
    getCharacter(campaignId || 'campaign-1', id)
      .then((item) => { setCharacter(item); emitRuntimeStatus(true); })
      .catch((reason) => {
        emitRuntimeStatus(false);
        if (DEMO_MODE) { setCharacter(mockCharacter(id)); setDemo(true); return; }
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  if (!character) return <div className="page"><div className="empty">{error ? `RUNTIME OFFLINE · ${error}` : '正在读取角色卡…'}</div></div>;
  const sheet = (character.sheet || {}) as Record<string, any>;
  const abilities = sheet.ability_scores || {};
  const hp = sheet.hp || {};
  const hpPercent = hp.max ? Math.max(0, Math.min(100, (Number(hp.current || 0) / Number(hp.max)) * 100)) : 0;

  return (
    <div className="page">
      <div className="page-heading">
        <div><div className="eyebrow">ACTOR DOSSIER / {character.character_type.toUpperCase()}</div><h1>{character.name}</h1><p>{character.summary || '暂无角色摘要。'}</p></div>
        <div className="heading-actions"><a className="btn btn-ghost" href={`/campaigns/detail?id=${encodeURIComponent(character.campaign_id || 'campaign-1')}`}>← 返回战役</a><span className="badge badge-green">REV {character.revision}</span></div>
      </div>
      {demo && <div className="demo-notice"><strong>DEMO DATA</strong><span>角色卡来自本地演示集；隐藏 notes 与 actor knowledge 在真实环境中由 MCP 按 principal 过滤。</span></div>}

      <section className="character-hero">
        <div className="character-identity"><span>{character.character_type === 'pc' ? 'PLAYER CHARACTER' : 'NON-PLAYER CHARACTER'}</span><h2>{sheet.race || '未知种族'} · {sheet.class || '未知职业'}</h2><p>LEVEL {sheet.level || '—'} {sheet.alignment ? `· ${sheet.alignment}` : ''} {character.player_name ? `· PLAYER ${character.player_name}` : ''}</p></div>
        <div className="vital"><small>HIT POINTS</small><strong>{hp.current ?? '—'} <span>/ {hp.max ?? '—'}</span></strong><div className="progress-bar"><div className="progress-fill" style={{ width: `${hpPercent}%` }} /></div></div>
        <div className="combat-vital"><div><small>ARMOR CLASS</small><b>{sheet.armor_class ?? '—'}</b></div><div><small>INITIATIVE</small><b>{formatBonus(sheet.initiative)}</b></div><div><small>SPEED</small><b>{sheet.speed ? `${sheet.speed} FT` : '—'}</b></div></div>
      </section>

      <section className="ability-grid character-abilities">
        {['str', 'dex', 'con', 'int', 'wis', 'cha'].map((ability) => <div className="ability" key={ability}><small>{ABILITY_NAMES_EN[ability]} · {ABILITY_LABELS[ability]}</small><strong>{abilities[ability] ?? '—'}</strong><span>{formatBonus(modifier(abilities[ability]))}</span></div>)}
      </section>

      <div className="grid-2 character-grid">
        <section className="card"><div className="card-header"><strong>SKILLS</strong><span>PROFICIENCY {formatBonus(sheet.proficiency_bonus ?? 2)}</span></div><div className="skill-grid">{Object.entries(sheet.skills || {}).map(([skill, value]) => <div key={skill}><span>{SKILL_NAMES[skill] || skill}</span><b>{formatBonus(value)}</b></div>)}{Object.keys(sheet.skills || {}).length === 0 && <div className="empty">暂无技能数据。</div>}</div></section>
        <section className="card"><div className="card-header"><strong>SPELL PREPARATION</strong><span>RESOURCE VIEW</span></div><div className="resource-list">{Object.entries(sheet.spells || {}).map(([level, slots]) => <div key={level}><span>{level}</span><b>{String(slots)}</b></div>)}{Object.keys(sheet.spells || {}).length === 0 && <div className="empty">该角色没有法术位数据。</div>}</div></section>
        <section className="card"><div className="card-header"><strong>EQUIPMENT</strong><span>VISIBLE INVENTORY</span></div><div className="equipment-list">{(sheet.equipment || []).map((item: string, index: number) => <span key={`${item}-${index}`}>{item}</span>)}{!sheet.equipment?.length && <div className="empty">暂无装备数据。</div>}</div></section>
        <section className="card knowledge-boundary"><div className="card-header"><strong>ACTOR KNOWLEDGE BOUNDARY</strong><span>{(character.notes as any)?.private ? 'SEALED' : 'SCOPED'}</span></div><div><strong>{String((character.notes as any)?.knowledge_count || 0).padStart(2, '0')}</strong><p>可见知识事实。真实内容必须通过 actor_knowledge_query 按 campaign、branch、actor 与 principal 获取；角色卡不会内嵌其他角色的秘密。</p></div></section>
      </div>

      <section className="card raw-section"><button className="card-header raw-toggle" onClick={() => setShowRaw((value) => !value)}><strong>RAW SHEET / DIAGNOSTIC</strong><span>{showRaw ? 'COLLAPSE ↑' : 'EXPAND ↓'}</span></button>{showRaw && <pre className="raw-json">{JSON.stringify(sheet, null, 2)}</pre>}</section>
    </div>
  );
}

function modifier(value: unknown) { return Math.floor((Number(value ?? 10) - 10) / 2); }
function formatBonus(value: unknown) { const numeric = Number(value ?? 0); return `${numeric >= 0 ? '+' : ''}${numeric}`; }
