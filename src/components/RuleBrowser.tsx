import { useState, useEffect } from 'react';
import { listRules, searchRules } from '../lib/api';
import type { RuleSource } from '../types';

export default function RuleBrowser() {
  const [sources, setSources] = useState<RuleSource[]>([]);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [selectedEdition, setSelectedEdition] = useState('all');
  const [loading, setLoading] = useState(false);

  useEffect(() => { listRules('dnd5e').then(setSources).catch(() => {}); }, []);

  const editions = [...new Set(sources.map(s => s.edition))];
  const filtered = selectedEdition === 'all' ? sources : sources.filter(s => s.edition === selectedEdition);

  const handleSearch = async () => {
    if (!query.trim()) return;
    setLoading(true);
    try {
      const data = await searchRules(query, 'dnd5e', 10);
      setResults(data?.hits || []);
    } catch {
      setResults([]);
    }
    setLoading(false);
  };

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto', padding: '24px 16px' }}>
      <div className="page-header">
        <a href="/" className="btn btn-ghost btn-sm">←</a>
        <h1>规则书</h1>
      </div>

      {/* Edition filter */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        <button className={`btn btn-sm ${selectedEdition === 'all' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setSelectedEdition('all')}>全部</button>
        {editions.map(e => (
          <button key={e} className={`btn btn-sm ${selectedEdition === e ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setSelectedEdition(e)}>
            SRD {e}
          </button>
        ))}
      </div>

      {/* Search */}
      <div className="card" style={{ marginBottom: 16, padding: '12px 16px', display: 'flex', gap: 8 }}>
        <input
          placeholder="搜索规则..."
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          style={{ flex: 1, padding: '8px 12px', borderRadius: 6, border: '1px solid #e5e7eb', fontSize: '.85rem' }}
        />
        <button className="btn btn-primary" onClick={handleSearch} disabled={loading}>搜索</button>
      </div>

      {/* Results */}
      {results.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {results.map((r: any, i) => (
            <div key={r.id || i} className="card" style={{ cursor: 'pointer' }}
              onClick={() => alert(`完整内容:\n\n${r.content || r.snippet || '(无内容)'}`)}>
              <div style={{ fontWeight: 600, fontSize: '.9rem', marginBottom: 4 }}>{r.title}</div>
              <div style={{ fontSize: '.8rem', color: '#6b7280' }}>
                得分: {r.score?.toFixed(2)} · {r.retrieval?.join(', ') || 'lexical'}
              </div>
              <div style={{ fontSize: '.82rem', marginTop: 6, color: '#374151' }}>
                {(r.content || r.snippet || '').substring(0, 250)}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {/* Source list */}
      {!query && (
        <div>
          <div className="card-header" style={{ marginBottom: 8 }}>规则集</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {filtered.map(s => (
              <div key={s.id} className="card" style={{ cursor: 'pointer' }}
                onClick={() => alert(`规则集: ${s.title}\n版本: ${s.edition} ${s.locale}\n权限: ${s.authority}`)}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <div style={{ fontWeight: 500, fontSize: '.9rem' }}>{s.title}</div>
                    <div style={{ fontSize: '.78rem', color: '#6b7280' }}>{s.edition} · {s.locale} · {s.source_key}</div>
                  </div>
                  <span className="badge badge-blue" style={{ fontSize: '.7rem' }}>{s.version}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
