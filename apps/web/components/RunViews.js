"use client";

// Views of one run: quality scorecard, variant explorer, process map, and a time axis for one journey.
// Plain SVG on the studio's design tokens; the pack's /sectors entry supplies event kinds and lanes.

export function shortLabel(eventType) {
  const [, rest] = eventType.split(".");
  return (rest || eventType).replaceAll("_", " ");
}

export function laneLabel(lane) {
  return (lane.object_type || lane.kind).replaceAll("_", " ");
}

export function journeysOf(bundle) {
  const events = Object.fromEntries(bundle.events.map((event) => [event.event_id, event]));
  return bundle.trajectories
    .filter((item) => !item.parent_trajectory_id)
    .map((item) => ({ trajectory: item, types: item.event_ids.map((id) => events[id]?.event_type).filter(Boolean) }));
}

export function variantsOf(journeys) {
  const groups = new Map();
  journeys.forEach((journey, index) => {
    const key = journey.types.join(">");
    if (!groups.has(key)) groups.set(key, { key, types: journey.types, members: [], first: index });
    groups.get(key).members.push(journey.trajectory);
  });
  return [...groups.values()].sort((a, b) => b.members.length - a.members.length || a.first - b.first);
}

const pct = (value) => `${Math.round(value * 100)}%`;

export function QualityCard({ quality }) {
  if (!quality) return null;
  const { complete, comprehensive, representative, qualitative } = quality;
  const coverage = Object.entries(comprehensive.event_type_coverage || {});
  return (
    <div className="quality">
      <div className="quality-tile" data-state={complete.passed ? "good" : "bad"}>
        <span>Complete</span>
        <strong>{complete.passed ? "Passed" : `${complete.hard_check_violations} violation${complete.hard_check_violations === 1 ? "" : "s"}`}</strong>
        <small>
          {complete.referential_integrity ? "References intact" : "Broken references"} · {complete.filler_runs ? `${complete.filler_runs} filler runs` : "no filler"} · {pct(complete.terminal_event_share)} end on an outcome
        </small>
      </div>
      <div className="quality-tile">
        <span>Comprehensive</span>
        <strong>{comprehensive.distinct_sequences} of {comprehensive.journeys}</strong>
        <small>distinct journeys · {pct(comprehensive.rare_path_share)} take a rare path · {comprehensive.distinct_transitions} transitions</small>
        <div className="coverage">
          {coverage.map(([domain, value]) => (
            <div key={domain} title={`${domain.replaceAll("_", " ")}: ${pct(value)} of its events appear`}>
              <em>{domain.replaceAll("_", " ")}</em>
              <div className="bar"><i style={{ width: pct(value) }} /></div>
            </div>
          ))}
        </div>
      </div>
      <div className="quality-tile" data-state="pending">
        <span>Representative</span>
        <strong>{representative.status === "unreferenced" ? "Unreferenced" : "Not measured"}</strong>
        <small>{representative.reason}</small>
      </div>
      <div className="quality-tile" data-state="pending">
        <span>Qualitative</span>
        <strong>Not measured</strong>
        <small>{qualitative.reason}</small>
      </div>
    </div>
  );
}

export function VariantList({ variants, total, active, onPick }) {
  const top = variants.slice(0, 12);
  const most = top[0]?.members.length || 1;
  return (
    <div className="variants">
      <div className="panel-head">
        <h3>Variants</h3>
        <small>{variants.length} distinct in {total} journeys{active ? " · filtered" : ""}</small>
      </div>
      <ol>
        {top.map((variant) => (
          <li key={variant.key}>
            <button type="button" data-on={active === variant.key} onClick={() => onPick(active === variant.key ? "" : variant.key)}>
              <span className="variant-count">{variant.members.length}</span>
              <span className="variant-body">
                <b>{variant.members[0].trajectory_type.replaceAll("_", " ")} · {variant.types.length} events</b>
                <span className="variant-bar"><i style={{ width: `${(100 * variant.members.length) / most}%` }} /></span>
                <small>{variant.types.map(shortLabel).join(" → ")}</small>
              </span>
            </button>
          </li>
        ))}
      </ol>
      {variants.length > top.length ? <small className="muted">{variants.length - top.length} more variants appear once or twice.</small> : null}
    </div>
  );
}

const NODE_H = 30;
const LANE_H = 58;
const LEFT = 96;
const nodeWidth = (type) => 40 + shortLabel(type).length * 6.8;

export function ProcessMap({ journeys, highlight, eventKinds, lanes }) {
  if (!journeys.length) return null;
  const counts = new Map();
  const position = new Map();
  const edges = new Map();
  for (const { types } of journeys) {
    types.forEach((type, index) => {
      counts.set(type, (counts.get(type) || 0) + 1);
      const at = types.length > 1 ? index / (types.length - 1) : 0;
      const entry = position.get(type) || { sum: 0, n: 0 };
      position.set(type, { sum: entry.sum + at, n: entry.n + 1 });
      if (index > 0) {
        const key = `${types[index - 1]}|${type}`;
        edges.set(key, (edges.get(key) || 0) + 1);
      }
    });
  }
  const kindOf = (type) => eventKinds[type] || "party";
  const laneKinds = lanes.map((lane) => lane.kind).filter((kind) => [...counts.keys()].some((type) => kindOf(type) === kind));
  // Place each event by its average position in the journeys, then push it right until it clears its lane neighbour.
  const width = 1180;
  const span = width - LEFT - 170;
  const nodes = new Map();
  laneKinds.forEach((kind, laneIndex) => {
    const inLane = [...counts.keys()]
      .filter((type) => kindOf(type) === kind)
      .map((type) => ({ type, x: LEFT + span * (position.get(type).sum / position.get(type).n) }))
      .sort((a, b) => a.x - b.x);
    let edge = -Infinity;
    for (const node of inLane) {
      const x = Math.max(node.x, edge + 14);
      edge = x + nodeWidth(node.type);
      nodes.set(node.type, { x, y: 16 + laneIndex * LANE_H, w: nodeWidth(node.type) });
    }
  });
  const right = Math.max(width, ...[...nodes.values()].map((node) => node.x + node.w + 24));
  const height = 22 + laneKinds.length * LANE_H;
  const most = Math.max(...edges.values(), 1);
  const path = new Set();
  if (highlight) {
    highlight.forEach((type, index) => {
      if (index > 0) path.add(`${highlight[index - 1]}|${type}`);
    });
  }
  const onPath = (type) => !highlight || highlight.includes(type);
  return (
    <div className="process-map">
      <div className="panel-head">
        <h3>Process map</h3>
        <small>Directly-follows transitions across every journey; thicker is more common, loops are repeats{highlight ? " · the chosen variant is highlighted" : ""}</small>
      </div>
      <div className="scroll-x">
        <svg width={right} height={height} role="img" aria-label="Process map of the run">
          {laneKinds.map((kind, laneIndex) => {
            const lane = lanes.find((item) => item.kind === kind);
            return (
              <g key={kind}>
                <line x1={0} x2={right} y1={laneIndex * LANE_H + 8} y2={laneIndex * LANE_H + 8} className="lane-rule" />
                <text x={4} y={16 + laneIndex * LANE_H + NODE_H / 2 + 4} className="lane-name">{laneLabel(lane || { kind })}</text>
              </g>
            );
          })}
          {[...edges.entries()].map(([key, count]) => {
            const [from, to] = key.split("|");
            const a = nodes.get(from);
            const b = nodes.get(to);
            if (!a || !b) return null;
            const lit = !highlight || path.has(key);
            const style = { strokeWidth: 1 + (5 * count) / most, opacity: lit ? 0.3 + (0.6 * count) / most : 0.06 };
            if (from === to) {
              const cx = a.x + a.w - 14;
              return (
                <path key={key} d={`M${cx - 8},${a.y} C${cx - 10},${a.y - 18} ${cx + 10},${a.y - 18} ${cx + 8},${a.y}`} className="flow" data-on={highlight ? lit : undefined} style={style}>
                  <title>{`${from} repeated: ${count}`}</title>
                </path>
              );
            }
            const forward = b.x > a.x + a.w / 2;
            const x1 = forward ? a.x + a.w : a.x + a.w / 2;
            const y1 = forward ? a.y + NODE_H / 2 : a.y + NODE_H;
            const x2 = forward ? b.x : b.x + b.w / 2;
            const y2 = forward ? b.y + NODE_H / 2 : b.y + NODE_H;
            const bend = forward ? Math.max(24, (x2 - x1) / 2) : 0;
            const d = forward
              ? `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`
              : `M${x1},${y1} C${x1},${y1 + 22} ${x2},${y2 + 22} ${x2},${y2}`;
            return (
              <path key={key} d={d} className="flow" data-on={highlight ? lit : undefined} style={style}>
                <title>{`${from} → ${to}: ${count}`}</title>
              </path>
            );
          })}
          {[...nodes.entries()].map(([type, node]) => (
            <g key={type} className="pm-node" data-on={highlight ? onPath(type) : undefined} transform={`translate(${node.x},${node.y})`}>
              <title>{`${type}: in ${counts.get(type)} journeys`}</title>
              <rect width={node.w} height={NODE_H} rx={9} />
              <text x={9} y={19}>{shortLabel(type)}</text>
              <text x={node.w - 8} y={19} textAnchor="end" className="pm-count">{counts.get(type)}</text>
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
}

const TICKS = [
  [0, "start"],
  [1, "1 h"],
  [24, "1 d"],
  [24 * 7, "1 wk"],
  [24 * 30, "1 mo"],
  [24 * 182, "6 mo"],
  [24 * 365, "1 yr"],
];

export function TimeAxis({ parent, alt, events, eventKinds, lanes, selected, onSelect }) {
  const altOnly = alt ? alt.event_ids.filter((id) => !parent.event_ids.includes(id)) : [];
  const ids = [...parent.event_ids, ...altOnly];
  const start = new Date(events[parent.event_ids[0]].event_time).getTime();
  const hours = (id) => Math.max(0, (new Date(events[id].event_time).getTime() - start) / 3600000);
  const total = Math.max(1, ...ids.map(hours));
  const used = lanes.filter((lane) => ids.some((id) => (eventKinds[events[id].event_type] || "party") === lane.kind));
  const width = 900;
  const plot = width - LEFT - 40;
  const laneH = 64;
  const top = 34;
  const x = (h) => LEFT + (plot * Math.log1p(h)) / Math.log1p(total);
  const y = (id) => top + used.findIndex((lane) => lane.kind === (eventKinds[events[id].event_type] || "party")) * laneH + laneH / 2;
  const line = (list) => list.map((id) => `${x(hours(id))},${y(id)}`).join(" ");
  const branchAt = alt ? parent.event_ids.indexOf(alt.branch_event_id) : -1;
  // Greedy labels per lane: above if it clears the previous label above, else below, else only on hover.
  const placed = new Map();
  const ends = new Map();
  [...ids]
    .sort((a, b) => hours(a) - hours(b))
    .forEach((id) => {
      const lane = eventKinds[events[id].event_type] || "party";
      const cx = x(hours(id));
      const half = (shortLabel(events[id].event_type).length * 6.2) / 2;
      const slot = ends.get(lane) || { above: -Infinity, below: -Infinity };
      if (cx - half > slot.above + 6) {
        placed.set(id, -12);
        slot.above = cx + half;
      } else if (cx - half > slot.below + 6) {
        placed.set(id, 21);
        slot.below = cx + half;
      }
      ends.set(lane, slot);
    });
  const height = top + used.length * laneH + 12;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="axis" role="img" aria-label="Journey on a time axis">
      {TICKS.filter(([h]) => h <= total).map(([h, label]) => (
        <g key={label}>
          <line x1={x(h)} x2={x(h)} y1={top - 8} y2={top + used.length * laneH} className="tick" />
          <text x={x(h)} y={top - 14} textAnchor="middle" className="tick-label">{label}</text>
        </g>
      ))}
      {used.map((lane, index) => (
        <g key={lane.kind}>
          <rect x={LEFT - 6} y={top + index * laneH + 6} width={plot + 12} height={laneH - 12} rx={10} className="lane-band" />
          <text x={4} y={top + index * laneH + laneH / 2 + 4} className="lane-name">{laneLabel(lane)}</text>
        </g>
      ))}
      <polyline points={line(parent.event_ids)} className="axis-path" />
      {alt && branchAt >= 0 ? <polyline points={line([parent.event_ids[branchAt], ...altOnly])} className="axis-path alt" /> : null}
      {ids.map((id, index) => {
        const isAlt = index >= parent.event_ids.length;
        const cx = x(hours(id));
        const cy = y(id);
        const offset = selected === id ? (placed.get(id) ?? -12) : placed.get(id);
        return (
          <g key={id} className="axis-mark" data-on={selected === id} data-alt={isAlt} onClick={() => onSelect(id)} role="button" tabIndex={0}
            aria-label={`${events[id].event_type}${isAlt ? ", simulated alternative" : ""}`}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(id)}>
            <title>{`${events[id].event_type} · ${new Date(events[id].event_time).toLocaleString()}${isAlt ? " · simulated alternative" : ""}`}</title>
            <circle cx={cx} cy={cy} r={selected === id ? 8 : 6} />
            {offset != null ? <text x={cx} y={cy + offset} textAnchor="middle">{shortLabel(events[id].event_type)}</text> : null}
          </g>
        );
      })}
    </svg>
  );
}

const signed = (value) => (value == null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}`);

export function GroupViewer({ sample, trajectories, events, active, onPick }) {
  if (!sample || sample.sequences.length < 2) return null;
  const byId = Object.fromEntries(trajectories.map((item) => [item.trajectory_id, item]));
  const widest = Math.max(...sample.sequences.map((sequence) => Math.abs(sequence.advantage || 0)), 0.01);
  const status = sample.group_accepted == null ? "no group signal" : sample.group_accepted ? "accepted" : "filtered: all pass or all fail";
  return (
    <div className="group-viewer">
      <div className="panel-head">
        <h3>Group of {sample.sequences.length}</h3>
        <small>One prompt, {sample.sequences.length} rollouts · {Math.round((sample.group_pass_rate || 0) * 100)}% pass · {status}</small>
      </div>
      <div className="rollouts">
        {sample.sequences.map((sequence, index) => {
          const trajectory = byId[sequence.trajectory_id];
          const types = trajectory ? trajectory.event_ids.map((id) => events[id]?.event_type).filter(Boolean) : [];
          const flagged = sequence.contexts.flatMap((context) => context.segments).filter((segment) => segment.flagged_reason);
          return (
            <button key={sequence.sequence_id} type="button" className="rollout" data-on={active === sequence.trajectory_id} data-outcome={sequence.outcome}
              onClick={() => onPick(sequence.trajectory_id)}>
              <span className="rollout-head">
                <b>{index === 0 ? "Primary" : `Rollout ${index + 1}`}</b>
                <em>{sequence.outcome || "—"}</em>
              </span>
              <span className="kv"><i>Reward</i><strong>{sequence.reward == null ? "—" : sequence.reward.toFixed(2)}</strong></span>
              <span className="kv"><i>Advantage</i><strong>{signed(sequence.advantage)}</strong></span>
              <span className="advantage-bar" data-sign={(sequence.advantage || 0) >= 0 ? "pos" : "neg"}>
                <i style={{ width: `${(50 * Math.abs(sequence.advantage || 0)) / widest}%` }} />
              </span>
              <span className="kv"><i>Quality</i><strong>{sequence.quality_factor == null ? "—" : sequence.quality_factor.toFixed(2)}</strong></span>
              <span className="kv"><i>Tokens</i><strong>{sequence.token_estimate ?? "—"}</strong></span>
              <small>{trajectory ? trajectory.trajectory_type.replaceAll("_", " ") : ""} · {types.length} events{flagged.length ? ` · ${flagged.length} flagged (recorded)` : ""}</small>
              <small className="rollout-tail">…{types.slice(-3).map(shortLabel).join(" → ")}</small>
            </button>
          );
        })}
      </div>
    </div>
  );
}
