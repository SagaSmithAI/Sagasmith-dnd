import { useEffect, useMemo, useState } from 'react';
import { MOCK_RULES, emitRuntimeStatus, listRules, searchRules } from '../lib/api';
import type { RuleSource } from '../types';

type Hit = { id?: string; title?: string; content?: string; snippet?: string; score?: number; retrieval?: string[]; source_key?: string; page?: number };

const DEMO_HITS: Hit[] = [
  { id: 'demo-1', title: 'Opportunity Attacks', content: 'A creature can make an opportunity attack when a hostile creature it can see leaves its reach using its action, bonus action, reaction, or movement.', score: 0.94, retrieval: ['fts5', 'exact'], source_key: 'srd-5.2.1', page: 23 },
  { id: 'demo-2', title: 'Moving Around Other Creatures', content: 'Movement, reach, visibility, and forced movement determine whether a reaction window exists. Missing spatial facts require a GM ruling before settlement.', score: 0.82, retrieval: ['lexical'], source_key: 'dnd5e.core.2024', page: 18 },
];

export default function RuleBrowser() {
  const [sources, setSources] = useState<RuleSource[]>([]);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<Hit[]>([]);
  const [edition, setEdition] = useState('all');
  const [loading, setLoading] = useState(false);
  const [demo, setDemo] = useState(false);

  useEffect(() => { listRules('dnd5e').then((items) => { setSources(items); emitRuntimeStatus(true); }).catch(() => { setSources(MOCK_RULES); setDemo(true); emitRuntimeStatus(false); }); }, []);
  const editions = useMemo(() => [...new Set(sources.map((source) => source.edition))], [sources]);
  const visibleSources = edition === 'all' ? sources : sources.filter((source) => source.edition === edition);

  const runSearch = async () => {
    if (!query.trim()) return;
    setLoading(true);
    try {
      const result = await searchRules(query, 'dnd5e', 10);
      setHits(Array.isArray(result) ? result : result?.hits || []);
    } catch {
      setHits(DEMO_HITS); setDemo(true); emitRuntimeStatus(false);
    } finally { setLoading(false); }
  };

  return (
    <div className="page">
      <div className="page-heading">
        <div><div className="eyebrow">RULE EVIDENCE / SOURCE-AWARE</div><h1>规则与来源</h1><p>检索提供候选证据；战役锁定的 core/extension rule packs 与结算 receipt 才决定实际使用的规则版本。</p></div>
        <div className="heading-actions"><a className="btn btn-ghost" href="/">返回桌面</a><a className="btn btn-primary" href="https://github.com/SagaSmithAI/SagaSmith-dnd-mcp#规则与扩展书" target="_blank" rel="noreferrer">导入流程 ↗</a></div>
      </div>
      {demo && <div className="demo-notice"><strong>DEMO DATA</strong><span>未连接 compatible gateway；来源与搜索结果为演示内容。</span></div>}

      <section className="rule-search card">
        <div><span>SEARCH THE ACTIVE RULE CORPUS</span><h2>先找到证据，再做裁决。</h2></div>
        <div className="rule-search-form"><input className="form-input" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && runSearch()} placeholder="例如：机会攻击何时触发？" /><button className="btn btn-primary" onClick={runSearch} disabled={loading}>{loading ? '检索中…' : '搜索规则'}</button></div>
        <footer><span>exact</span><span>FTS5 / BM25</span><span>dense optional</span><b>SEARCH ≠ AUTHORITATIVE STATE</b></footer>
      </section>

      {hits.length > 0 && <section className="rule-results"><div className="result-heading"><span>SEARCH RESULTS</span><b>{hits.length} CANDIDATES</b></div>{hits.map((hit, index) => <article className="rule-hit" key={hit.id || index}><span className="hit-rank">{String(index + 1).padStart(2, '0')}</span><div><div className="hit-source">{hit.source_key || 'RULE SOURCE'} {hit.page ? `· P.${hit.page}` : ''}</div><h3>{hit.title || 'Untitled rule section'}</h3><p>{hit.content || hit.snippet || '没有可用摘要。'}</p><footer>{(hit.retrieval || ['lexical']).map((mode) => <span className="tag" key={mode}>{mode}</span>)}</footer></div><b>{hit.score != null ? hit.score.toFixed(2) : '—'}</b></article>)}</section>}

      <section className="source-section">
        <div className="result-heading"><span>INSTALLED SOURCES</span><div className="filter-row"> <button className={`btn btn-sm ${edition === 'all' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setEdition('all')}>全部</button>{editions.map((item) => <button className={`btn btn-sm ${edition === item ? 'btn-primary' : 'btn-ghost'}`} key={item} onClick={() => setEdition(item)}>{item}</button>)}</div></div>
        <div className="source-grid">{visibleSources.map((source) => <article key={source.id}><header><span>{source.authority.toUpperCase()}</span><b>{source.version}</b></header><h3>{source.title}</h3><p>{source.source_key}</p><footer><span>EDITION {source.edition}</span><span>{source.locale.toUpperCase()}</span></footer></article>)}</div>
      </section>
    </div>
  );
}
