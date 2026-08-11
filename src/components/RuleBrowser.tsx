import { useEffect, useMemo, useState } from 'react';
import {
  GatewayRequestError,
  emitRuntimeStatus,
  getRuleContext,
  listCampaigns,
  listRules,
  searchRules,
} from '../lib/api';
import type { Campaign, RuleSource } from '../types';

type Hit = { id?: string; title?: string; content?: string; snippet?: string; score?: number; retrieval?: string[]; source_key?: string; page?: number; chunk_key?: string };

function errorText(error: unknown) {
  if (error instanceof GatewayRequestError) return `${error.category}: ${error.message}`;
  return error instanceof Error ? error.message : String(error);
}

export default function RuleBrowser() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [campaignId, setCampaignId] = useState('');
  const [sources, setSources] = useState<RuleSource[]>([]);
  const [context, setContext] = useState<Record<string, any> | null>(null);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<Hit[]>([]);
  const [edition, setEdition] = useState('all');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get('campaign');
    listCampaigns().then((items) => {
      setCampaigns(items);
      const selected = items.find((item) => item.id === requested)
        || items.find((item) => item.status === 'active') || items[0];
      if (selected) setCampaignId(selected.id);
      emitRuntimeStatus(true);
    }).catch((reason) => {
      setError(errorText(reason)); emitRuntimeStatus(false);
    });
  }, []);

  useEffect(() => {
    if (!campaignId) return;
    setError(''); setSources([]); setContext(null); setHits([]);
    Promise.all([listRules(campaignId), getRuleContext(campaignId)])
      .then(([nextSources, nextContext]) => {
        setSources(nextSources);
        setContext(nextContext.data);
        const params = new URLSearchParams(window.location.search);
        params.set('campaign', campaignId);
        window.history.replaceState({}, '', `${window.location.pathname}?${params}`);
      })
      .catch((reason) => setError(errorText(reason)));
  }, [campaignId]);

  const editions = useMemo(() => [...new Set(sources.map((source) => source.edition).filter(Boolean))], [sources]);
  const visibleSources = edition === 'all' ? sources : sources.filter((source) => source.edition === edition);

  const runSearch = async () => {
    if (!query.trim() || !campaignId) return;
    setLoading(true); setError('');
    try {
      const result = await searchRules(query, campaignId, 10, { edition: edition === 'all' ? undefined : edition });
      setHits(Array.isArray(result) ? result : result?.hits || []);
    } catch (reason) {
      setHits([]); setError(errorText(reason));
    } finally { setLoading(false); }
  };

  return <div className="page">
    <div className="page-heading">
      <div><div className="eyebrow">RULE EVIDENCE / CAMPAIGN LOCKED</div><h1>规则与来源</h1><p>检索当前战役可用的来源证据，并同时显示 branch lock 与 ruleset fingerprint。检索命中只是候选证据，权威结算仍由 MCP 使用精确 Pack 锁完成。</p></div>
      <div className="heading-actions"><a className="btn btn-ghost" href="/">返回桌面</a><a className="btn btn-primary" href={campaignId ? `/library?mode=installed&campaign=${encodeURIComponent(campaignId)}` : '/library'}>管理 Content Pack</a></div>
    </div>
    {error && <div className="content-alert error"><strong>RULE GATEWAY</strong>{error}</div>}

    <section className="rule-context card">
      <label>战役<select value={campaignId} onChange={(event) => setCampaignId(event.target.value)}><option value="">选择战役</option>{campaigns.map((campaign) => <option value={campaign.id} key={campaign.id}>{campaign.name} · {campaign.edition}</option>)}</select></label>
      <div><small>BRANCH</small><strong>{context?.branch_id || '—'}</strong></div>
      <div><small>RULESET FINGERPRINT</small><code>{context?.fingerprint || '—'}</code></div>
      <div><small>CORE LOCK</small><strong>{context?.core_pack ? `${context.core_pack.id}@${context.core_pack.version}` : '—'}</strong></div>
    </section>

    <section className="rule-search card">
      <div><span>SEARCH THE CAMPAIGN RULE CORPUS</span><h2>先找到证据，再做裁决。</h2></div>
      <div className="rule-search-form"><input className="form-input" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && void runSearch()} placeholder="例如：机会攻击何时触发？"/><button className="btn btn-primary" onClick={runSearch} disabled={loading || !campaignId}>{loading ? '检索中…' : '搜索规则'}</button></div>
      <footer><span>exact</span><span>FTS5 / BM25</span><span>dense optional</span><b>SEARCH ≠ AUTHORITATIVE STATE</b></footer>
    </section>

    {hits.length > 0 && <section className="rule-results"><div className="result-heading"><span>SEARCH RESULTS</span><b>{hits.length} CANDIDATES</b></div>{hits.map((hit, index) => <article className="rule-hit" key={hit.id || index}><span className="hit-rank">{String(index + 1).padStart(2, '0')}</span><div><div className="hit-source">{hit.source_key || 'RULE SOURCE'} {hit.page ? `· P.${hit.page}` : ''}</div><h3>{hit.title || 'Untitled rule section'}</h3><p>{hit.content || hit.snippet || '没有可用摘要。'}</p><footer>{(hit.retrieval || ['lexical']).map((mode) => <span className="tag" key={mode}>{mode}</span>)}{hit.chunk_key && <code>{hit.chunk_key}</code>}</footer></div><b>{hit.score != null ? hit.score.toFixed(2) : '—'}</b></article>)}</section>}

    <section className="source-section">
      <div className="result-heading"><span>STORED RULE PACKS</span><div className="filter-row"><button className={`btn btn-sm ${edition === 'all' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setEdition('all')}>全部</button>{editions.map((item) => <button className={`btn btn-sm ${edition === item ? 'btn-primary' : 'btn-ghost'}`} key={item} onClick={() => setEdition(item)}>{item}</button>)}</div></div>
      <div className="source-grid">{visibleSources.map((source) => <article key={`${source.id}:${source.version}`}><header><span>{source.authority.toUpperCase()}</span><b>{source.status?.toUpperCase() || 'STORED'}</b></header><h3>{source.title}</h3><p>{source.source_key}</p><code>{source.checksum || 'NO CHECKSUM'}</code><footer><span>EDITION {source.edition}</span><span>{source.locale.toUpperCase()}</span></footer></article>)}</div>
      {campaignId && sources.length === 0 && !error && <div className="empty card">当前战役没有可见的规则 Pack。</div>}
    </section>
  </div>;
}
