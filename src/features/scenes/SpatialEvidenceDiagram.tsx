import type { SceneSpatial } from '../../types';

export default function SpatialEvidenceDiagram({ spatial }: { spatial?: SceneSpatial }) {
  const locations = spatial?.locations || [];
  const connections = spatial?.connections || [];
  const positions = new Map(locations.map((location, index) => [
    location.key,
    { x: 92 + (index % 3) * 176, y: 68 + Math.floor(index / 3) * 116 },
  ]));
  const height = Math.max(210, 126 + Math.ceil(locations.length / 3) * 116);

  if (!locations.length) {
    return <div className="spatial-empty"><strong>NO SPATIAL EVIDENCE</strong><span>模组没有提供可验证的位置条目；系统不会猜测房间、墙体或路线。</span></div>;
  }

  return (
    <div className="spatial-evidence">
      <header>
        <div><span>SCENE SPATIAL / EVIDENCE ONLY</span><strong>位置与连接证据</strong></div>
        <em>NOT A BATTLE MAP</em>
      </header>
      <svg viewBox={`0 0 560 ${height}`} role="img" aria-label="Scene spatial evidence graph">
        <defs>
          <pattern id="evidence-grid" width="24" height="24" patternUnits="userSpaceOnUse">
            <path d="M 24 0 L 0 0 0 24" fill="none" stroke="currentColor" strokeOpacity=".08" />
          </pattern>
        </defs>
        <rect width="560" height={height} fill="url(#evidence-grid)" />
        {connections.map((connection, index) => {
          const from = positions.get(connection.from);
          const to = positions.get(connection.to);
          if (!from || !to) return null;
          return <g key={`${connection.from}-${connection.to}-${index}`} className={`evidence-edge ${connection.confidence === 'derived' ? 'derived' : ''}`}>
            <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} />
            {connection.label && <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 7}>{connection.label}</text>}
          </g>;
        })}
        {locations.map((location) => {
          const point = positions.get(location.key)!;
          const dimensions = location.dimensions_ft;
          return <g key={location.key} className={`evidence-node ${location.confidence === 'derived' ? 'derived' : ''}`} transform={`translate(${point.x} ${point.y})`}>
            <rect x="-68" y="-29" width="136" height="58" rx="2" />
            <text className="node-kind" x="-56" y="-9">{(location.kind || 'LOCATION').toUpperCase()}</text>
            <text className="node-title" x="-56" y="11">{location.title.slice(0, 20)}</text>
            <text className="node-meta" x="-56" y="25">{dimensions?.width && dimensions?.height ? `${dimensions.width} × ${dimensions.height} FT` : location.confidence || 'UNKNOWN SCALE'}</text>
          </g>;
        })}
      </svg>
      <footer><span>实线：显式连接</span><span>虚线：解析得出的证据</span><span>{locations.length} LOCATIONS / {connections.length} CONNECTIONS</span></footer>
    </div>
  );
}
