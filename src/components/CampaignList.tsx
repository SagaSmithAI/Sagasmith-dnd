import { useState, useEffect } from 'react';
import { listCampaigns, MOCK_CAMPAIGNS } from '../lib/api';
import type { Campaign } from '../types';

export default function CampaignList() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);

  useEffect(() => {
    listCampaigns().then(setCampaigns).catch(() => setCampaigns(MOCK_CAMPAIGNS));
  }, []);

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: '24px 16px' }}>
      <div className="page-header">
        <a href="/" className="btn btn-ghost btn-sm">← Dashboard</a>
        <h1>战役</h1>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {campaigns.map(c => (
          <a key={c.id} href={`/campaigns/${c.id}`} className="card"
            style={{ display: 'block', textDecoration: 'none', color: 'inherit', transition: 'box-shadow .2s' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: '1rem' }}>{c.name}</div>
                <div style={{ fontSize: '.82rem', color: '#6b7280' }}>
                  D&D 5e {c.edition} · {c.locale === 'zh' ? '中文' : 'English'} · Rev {c.revision}
                  {c.description ? ` · ${c.description}` : ''}
                </div>
              </div>
              <span className={`badge ${c.status === 'active' ? 'badge-green' : 'badge-gray'}`}>
                {c.status === 'active' ? '进行中' : c.status}
              </span>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
