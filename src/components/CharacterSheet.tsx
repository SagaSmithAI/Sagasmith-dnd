import { useState, useEffect } from 'react';
import { getCharacter } from '../lib/api';
import type { Character } from '../types';
import { ABILITY_LABELS, SKILL_NAMES } from '../types';

export default function CharacterSheet({ id: propId }: { id?: string } = {}) {
  const id = propId || (typeof window !== 'undefined' ? window.location.pathname.split('/').pop() : '');
  const [char, setChar] = useState<Character | null>(null);
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => { if (id) getCharacter(id).then(setChar).catch(() => {}); }, [id]);

  if (!char) return <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>加载中...</div>;

  const sheet = (char.sheet || {}) as Record<string, any>;
  const abilities = sheet.ability_scores || {};
  const hp = sheet.hp || { current: 0, max: 10 };
  const skillsList = sheet.skills || {};

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: '24px 16px' }}>
      {/* Header */}
      <div className="page-header">
        <a href={`/campaigns/${char.campaign_id}`} className="btn btn-ghost btn-sm">←</a>
        <div>
          <h1>{char.name}</h1>
          <div className="subtitle">{char.character_type === 'pc' ? '🧙 PC' : '👤 NPC'} · {sheet.class || ''} Lv.{sheet.level || '?'}</div>
        </div>
      </div>

      {/* Ability scores */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">属性</div>
        <div className="stat-grid">
          {['str', 'dex', 'con', 'int', 'wis', 'cha'].map(ab => (
            <div key={ab} className="stat-card">
              <div style={{ fontSize: '.7rem', color: '#6b7280', textTransform: 'uppercase' }}>{ABILITY_LABELS[ab] || ab}</div>
              <div className="stat-number">{abilities[ab] ?? '-'}</div>
              <div style={{ fontSize: '.75rem', color: '#9ca3af' }}>{Math.floor(((abilities[ab] ?? 10) - 10) / 2) >= 0 ? '+' : ''}{Math.floor(((abilities[ab] ?? 10) - 10) / 2)}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        {/* Combat stats */}
        <div className="card">
          <div className="card-header">战斗</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: '.9rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#6b7280' }}>HP</span><span>{hp.current || '?'} / {hp.max || '?'}</span></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#6b7280' }}>AC</span><span>{sheet.armor_class ?? '-'}</span></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#6b7280' }}>先攻</span><span>{sheet.initiative ?? '-'}</span></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#6b7280' }}>速度</span><span>{sheet.speed ?? '-'} ft</span></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#6b7280' }}>熟练加值</span><span>+{sheet.proficiency_bonus ?? 2}</span></div>
          </div>
        </div>

        {/* Spell slots */}
        <div className="card">
          <div className="card-header">法术位</div>
          {sheet.spells && Object.keys(sheet.spells).length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: '.9rem' }}>
              {Object.entries(sheet.spells).map(([level, slots]) => (
                <div key={level} style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#6b7280' }}>{level}</span>
                  <span>{String(slots)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p style={{ color: '#9ca3af', fontSize: '.85rem' }}>无法术位数据</p>
          )}
        </div>
      </div>

      {/* Skills */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">技能</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 6 }}>
          {Object.entries(skillsList).length > 0 ? (
            Object.entries(skillsList).map(([skill, value]) => (
              <div key={skill} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 8px', background: '#f9fafb', borderRadius: 4, fontSize: '.85rem' }}>
                <span>{SKILL_NAMES[skill] || skill}</span>
                <span style={{ fontWeight: 600 }}>{typeof value === 'number' && value >= 0 ? `+${value}` : value}</span>
              </div>
            ))
          ) : (
            <p style={{ color: '#9ca3af', fontSize: '.85rem', gridColumn: '1 / -1' }}>暂无技能数据</p>
          )}
        </div>
      </div>

      {/* Raw JSON */}
      <div className="card">
        <div className="card-header" style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between' }} onClick={() => setShowRaw(!showRaw)}>
          <span>Sheet 原始数据</span>
          <span style={{ fontSize: '.8rem', color: '#6b7280' }}>{showRaw ? '收起' : '展开'}</span>
        </div>
        {showRaw && (
          <pre style={{ fontSize: '.75rem', overflow: 'auto', maxHeight: 400, background: '#1a1a2e', color: '#e5e7eb', padding: 12, borderRadius: 6, marginTop: 8 }}>
            {JSON.stringify(sheet, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
