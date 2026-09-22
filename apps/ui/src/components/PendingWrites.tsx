import { useEffect, useState } from 'react';
import { DEMO_MODE, listPendingWrites, recoverPendingWrite } from '../lib/api';
import type { PendingWrite } from '../lib/pendingWrites';

function label(path: string) {
  if (path.endsWith('/combat/move')) return '战斗移动';
  if (path.endsWith('/actors/from-preset')) return '从预设创建角色';
  if (path.endsWith('/content-packs/import')) return '导入内容包';
  return '内容包操作';
}

export default function PendingWrites() {
  const [requests, setRequests] = useState<PendingWrite[]>([]);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (DEMO_MODE) return;
    const refresh = () => { void listPendingWrites().then(setRequests).catch(() => undefined); };
    refresh();
    window.addEventListener('sagasmith:pending-writes', refresh);
    return () => window.removeEventListener('sagasmith:pending-writes', refresh);
  }, []);
  if (!requests.length && !message) return null;
  return <section className="content-alert" aria-live="polite">
    <strong>写入结果确认</strong>
    {requests.length > 0 && <p>以下操作尚未确认完成。恢复会使用原请求，不会创建新的操作。</p>}
    {requests.map(request => <div key={request.id}>
      <span>{label(request.path)}</span>{' '}
      <button disabled={Boolean(busy)} onClick={async () => {
        setBusy(request.id); setMessage('');
        try { await recoverPendingWrite(request.id); setMessage('原请求已确认完成，请刷新页面查看最新状态。'); }
        catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
        finally { setBusy(''); }
      }}>恢复原请求</button>
    </div>)}
    {message && <p>{message}</p>}
  </section>;
}
