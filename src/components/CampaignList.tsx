import { useEffect, useState } from 'react';
import { DEMO_MODE, MOCK_CAMPAIGNS, emitRuntimeStatus, listCampaigns } from '../lib/api';
import type { Campaign } from '../types';

export default function CampaignList() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    listCampaigns()
      .then((items) => { setCampaigns(items); emitRuntimeStatus(true); })
      .catch((reason) => {
        emitRuntimeStatus(false);
        if (DEMO_MODE) { setCampaigns(MOCK_CAMPAIGNS); setDemo(true); return; }
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  return (
    <div className="page">
      <div className="page-heading">
        <div><div className="eyebrow">CAMPAIGN ARCHIVE / BRANCH-AWARE</div><h1>战役档案</h1><p>查看系统版本、当前阶段、revision 与活动状态。进入战役后继续检查角色、场景、模组和 Snapshot lineage。</p></div>
        <div className="heading-actions"><a href="/" className="btn btn-ghost">返回桌面</a><a href="https://github.com/SagaSmithAI/SagaSmith-dnd-mcp" className="btn btn-primary" target="_blank" rel="noreferrer">通过 MCP 建团 ↗</a></div>
      </div>
      {demo && <div className="demo-notice"><strong>DEMO DATA</strong><span>未连接 compatible gateway；以下战役用于展示信息结构。</span></div>}
      {error && <div className="demo-notice"><strong>RUNTIME OFFLINE</strong><span>{error}</span></div>}
      <div className="campaign-grid">
        {campaigns.map((campaign, index) => {
          const phase = String(campaign.state?.game_phase || 'lobby');
          return (
            <a key={campaign.id} href={`/campaigns/detail?id=${encodeURIComponent(campaign.id)}`} className="campaign-card">
              <header><span>0{index + 1}</span><span className={`badge ${campaign.status === 'active' ? 'badge-green' : 'badge-gray'}`}>{campaign.status}</span></header>
              <div className="campaign-edition">D&D 5E · {campaign.edition} · {campaign.locale.toUpperCase()}</div>
              <h2>{campaign.name}</h2>
              <p>{campaign.description || '暂无战役摘要。'}</p>
              <div className="campaign-phase"><span>PHASE</span><b>{phase.toUpperCase()}</b><span>REVISION</span><b>{campaign.revision}</b></div>
              <footer><span>{campaign.slug}</span><b>OPEN DOSSIER →</b></footer>
            </a>
          );
        })}
      </div>
    </div>
  );
}
