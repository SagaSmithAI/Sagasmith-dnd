import { useEffect, useRef, useState } from 'react';
import type { BattleMap, CombatantView, GridPosition } from '../../types';

interface DragState {
  actorId: string;
  origin: GridPosition;
  destination: GridPosition;
}

interface Props {
  battleMap: BattleMap;
  combatants: CombatantView[];
  selectedActorId?: string;
  onSelect: (actorId: string) => void;
  onMove: (actorId: string, destination: GridPosition, distance: number) => void;
}

export default function CombatMapCanvas({ battleMap, combatants, selectedActorId, onSelect, onMove }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const bounds = battleMap.bounds;

  const geometry = () => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const cell = Math.min(rect.width / bounds.width_cells, rect.height / bounds.height_cells);
    const width = cell * bounds.width_cells;
    const height = cell * bounds.height_cells;
    return { rect, cell, left: (rect.width - width) / 2, top: (rect.height - height) / 2, width, height };
  };

  const eventCell = (event: React.PointerEvent<HTMLCanvasElement>): GridPosition | null => {
    const layout = geometry();
    if (!layout) return null;
    const x = Math.floor((event.clientX - layout.rect.left - layout.left) / layout.cell);
    const y = Math.floor((event.clientY - layout.rect.top - layout.top) / layout.cell);
    return x >= 0 && y >= 0 && x < bounds.width_cells && y < bounds.height_cells ? { x, y } : null;
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => {
      const layout = geometry();
      if (!layout) return;
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(layout.rect.width * ratio);
      canvas.height = Math.round(layout.rect.height * ratio);
      const context = canvas.getContext('2d');
      if (!context) return;
      context.scale(ratio, ratio);
      context.clearRect(0, 0, layout.rect.width, layout.rect.height);
      context.fillStyle = 'rgba(24, 27, 23, .78)';
      context.fillRect(layout.left, layout.top, layout.width, layout.height);

      const difficult = new Set(battleMap.difficult_cells || []);
      const blocked = new Set(battleMap.blocked_cells || []);
      for (let y = 0; y < bounds.height_cells; y += 1) {
        for (let x = 0; x < bounds.width_cells; x += 1) {
          const key = `${x},${y}`;
          if (difficult.has(key)) {
            context.fillStyle = 'rgba(201, 140, 64, .28)';
            context.fillRect(layout.left + x * layout.cell, layout.top + y * layout.cell, layout.cell, layout.cell);
          }
          if (blocked.has(key)) {
            context.fillStyle = 'rgba(12, 13, 12, .72)';
            context.fillRect(layout.left + x * layout.cell, layout.top + y * layout.cell, layout.cell, layout.cell);
            context.strokeStyle = 'rgba(225, 83, 43, .55)';
            context.beginPath();
            context.moveTo(layout.left + x * layout.cell + 4, layout.top + y * layout.cell + 4);
            context.lineTo(layout.left + (x + 1) * layout.cell - 4, layout.top + (y + 1) * layout.cell - 4);
            context.stroke();
          }
        }
      }

      context.strokeStyle = 'rgba(245, 239, 221, .16)';
      context.lineWidth = 1;
      for (let x = 0; x <= bounds.width_cells; x += 1) {
        context.beginPath();
        context.moveTo(layout.left + x * layout.cell, layout.top);
        context.lineTo(layout.left + x * layout.cell, layout.top + layout.height);
        context.stroke();
      }
      for (let y = 0; y <= bounds.height_cells; y += 1) {
        context.beginPath();
        context.moveTo(layout.left, layout.top + y * layout.cell);
        context.lineTo(layout.left + layout.width, layout.top + y * layout.cell);
        context.stroke();
      }

      if (drag) {
        const startX = layout.left + (drag.origin.x + .5) * layout.cell;
        const startY = layout.top + (drag.origin.y + .5) * layout.cell;
        const endX = layout.left + (drag.destination.x + .5) * layout.cell;
        const endY = layout.top + (drag.destination.y + .5) * layout.cell;
        context.strokeStyle = '#f07850';
        context.lineWidth = 3;
        context.setLineDash([6, 5]);
        context.beginPath();
        context.moveTo(startX, startY);
        context.lineTo(endX, endY);
        context.stroke();
        context.setLineDash([]);
      }

      for (const combatant of combatants) {
        const position = drag?.actorId === combatant.actor_id ? drag.destination : combatant.position;
        if (!position) continue;
        const cx = layout.left + (position.x + .5) * layout.cell;
        const cy = layout.top + (position.y + .5) * layout.cell;
        const radius = Math.max(8, layout.cell * .31);
        context.beginPath();
        context.arc(cx, cy, radius, 0, Math.PI * 2);
        context.fillStyle = combatant.disposition === 'hostile' ? '#b64732' : combatant.disposition === 'neutral' ? '#b08c4e' : '#637b64';
        context.fill();
        context.lineWidth = selectedActorId === combatant.actor_id ? 3 : 1.5;
        context.strokeStyle = selectedActorId === combatant.actor_id ? '#fff2dc' : 'rgba(255,255,255,.58)';
        context.stroke();
        context.fillStyle = '#fff8e9';
        context.font = `700 ${Math.max(8, layout.cell * .22)}px ui-monospace`;
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.fillText(combatant.name.slice(0, 2), cx, cy);
      }
    };
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    draw();
    return () => observer.disconnect();
  }, [battleMap, bounds.height_cells, bounds.width_cells, combatants, drag, selectedActorId]);

  const pointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const cell = eventCell(event);
    if (!cell) return;
    const actor = combatants.find((item) => item.position?.x === cell.x && item.position?.y === cell.y);
    if (!actor?.position) return;
    onSelect(actor.actor_id);
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({ actorId: actor.actor_id, origin: actor.position, destination: actor.position });
  };

  const pointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drag) return;
    const cell = eventCell(event);
    if (cell) setDrag({ ...drag, destination: cell });
  };

  const pointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drag) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    const distance = Math.max(Math.abs(drag.destination.x - drag.origin.x), Math.abs(drag.destination.y - drag.origin.y)) * 5;
    if (distance > 0) onMove(drag.actorId, drag.destination, distance);
    setDrag(null);
  };

  return <canvas ref={canvasRef} className="combat-map-canvas" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={() => setDrag(null)} />;
}
