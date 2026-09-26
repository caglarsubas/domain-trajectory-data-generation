"use client";

// Views of one run: quality scorecard, variant explorer, and a process map with one journey traced on it.
// Plain SVG on the studio's design tokens; the pack's /sectors entry supplies event kinds and lanes.

import { useEffect, useMemo, useRef, useState } from "react";

export function shortLabel(eventType) {
  const [, rest] = eventType.split(".");
  return (rest || eventType).replaceAll("_", " ");
}

export function laneLabel(lane) {
  return (lane.object_type || lane.kind).replaceAll("_", " ");
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
      {representative.status === "measured" ? (
        <div className="quality-tile" data-state={representative.fitness >= 0.7 && representative.precision >= 0.7 ? "good" : "pending"} title={representative.explanation}>
          <span>Representative</span>
          <strong>{pct(representative.fitness)} fitness · {pct(representative.precision)} precision</strong>
          <small>
            Against {(representative.sources || []).join(", ")}
            {representative.next_step_divergence != null ? ` · next-step divergence ${representative.next_step_divergence}` : ""}
          </small>
        </div>
      ) : (
        <div className="quality-tile" data-state="pending">
          <span>Representative</span>
          <strong>{representative.status === "unreferenced" ? "Unreferenced" : "Not measured"}</strong>
          <small>{representative.reason}</small>
        </div>
      )}
      <div className="quality-tile" data-state="pending">
        <span>Qualitative</span>
        <strong>Not measured</strong>
        <small>{qualitative.reason}</small>
      </div>
    </div>
  );
}

export function VariantList({ variants, total, distinct, active, onPick }) {
  const top = variants.slice(0, 12);
  const most = top[0]?.count || 1;
  return (
    <div className="variants">
      <div className="panel-head">
        <h3>Variants</h3>
        <small>{distinct} distinct in {total.toLocaleString()} journeys{active ? " · filtered" : ""}</small>
      </div>
      <ol>
        {top.map((variant) => (
          <li key={variant.id}>
            <button type="button" data-on={active === variant.id} onClick={() => onPick(active === variant.id ? "" : variant.id)}>
              <span className="variant-count">{variant.count}</span>
              <span className="variant-body">
                <b>{variant.kind.replaceAll("_", " ")} · {variant.types.length} events</b>
                <span className="variant-bar"><i style={{ width: `${(100 * variant.count) / most}%` }} /></span>
                <small>{variant.types.map(shortLabel).join(" → ")}</small>
              </span>
            </button>
          </li>
        ))}
      </ol>
      {distinct > top.length ? <small className="muted">{distinct - top.length} more variants are less common.</small> : null}
    </div>
  );
}

const NODE_H = 30;
const ROW_H = 58;
const LEFT = 104;
const TOP = 34;
// A node may slide this far right of its axis position before it takes a new row in its lane.
const SLIDE = 48;
const nodeWidth = (type, count) => 30 + shortLabel(type).length * 6.8 + (count ? String(count).length * 7.5 : 0);

const TICKS = [
  [0, "start"],
  [1 / 6, "10 min"],
  [1, "1 h"],
  [24, "1 d"],
  [24 * 7, "1 wk"],
  [24 * 30, "1 mo"],
  [24 * 182, "6 mo"],
  [24 * 365, "1 yr"],
];

const tenths = (value) => String(Number(value.toFixed(1)));

export function formatHours(hours) {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 24) return `${hours < 10 ? tenths(hours) : Math.round(hours)} h`;
  const days = hours / 24;
  if (days < 14) return `${days < 10 ? tenths(days) : Math.round(days)} d`;
  if (days < 60) return `${Math.round(days / 7)} wk`;
  if (days < 730) return `${Math.round(days / 30)} mo`;
  return `${tenths(days / 365)} yr`;
}

// The focused journey as steps: its own events in order, then the alternative branch's, each with its elapsed time.
export function traceOf(journey) {
  if (!journey) return null;
  const { parent, alt, events } = journey;
  const start = new Date(events[parent.event_ids[0]].event_time).getTime();
  const step = (id, index, isAlt) => ({
    id,
    type: events[id].event_type,
    step: index + 1,
    hours: Math.max(0, (new Date(events[id].event_time).getTime() - start) / 3600000),
    alt: isAlt,
  });
  const steps = parent.event_ids.map((id, index) => step(id, index, false));
  const altSteps = alt ? alt.event_ids.filter((id) => !parent.event_ids.includes(id)).map((id) => step(id, alt.event_ids.indexOf(id), true)) : [];
  const branchAt = alt ? parent.event_ids.indexOf(alt.branch_event_id) : -1;
  const pairs = (list) => list.slice(1).map((item, index) => [list[index], item]);
  return { steps, altSteps, main: pairs(steps), branch: pairs(branchAt >= 0 ? [steps[branchAt], ...altSteps] : altSteps) };
}

// A run without a stored overview, such as the banking sample, gets one from the journey on screen.
export function journeyOverview(journey) {
  const trace = traceOf(journey);
  if (!trace) return null;
  const nodes = new Map();
  for (const item of trace.steps) {
    const node = nodes.get(item.type) || { type: item.type, count: 0, hours: item.hours, step: item.step, position: 0 };
    node.count += 1;
    nodes.set(item.type, node);
  }
  const edges = new Map();
  for (const [a, b] of trace.main) {
    const key = `${a.type}|${b.type}`;
    const edge = edges.get(key) || { from: a.type, to: b.type, count: 0, hours: b.hours - a.hours };
    edge.count += 1;
    edges.set(key, edge);
  }
  return { journeys: 1, distinct_variants: 1, variants: [], nodes: [...nodes.values()], edges: [...edges.values()] };
}

export function typeSummary(overview, type) {
  const node = overview?.nodes.find((item) => item.type === type) || null;
  const edges = (overview?.edges || []).filter((edge) => edge.from !== edge.to);
  const top = (list) => list.sort((a, b) => b.count - a.count).slice(0, 3);
  return {
    node,
    repeats: (overview?.edges || []).find((edge) => edge.from === type && edge.to === type)?.count || 0,
    next: top(edges.filter((edge) => edge.from === type)),
    prev: top(edges.filter((edge) => edge.to === type)),
  };
}

function stepTicks(max) {
  const every = max <= 12 ? 1 : max <= 30 ? 5 : 10;
  const ticks = [1];
  for (let s = every === 1 ? 2 : every; s <= max; s += every) ticks.push(s);
  return ticks;
}

function layoutMap({ overview, eventKinds, lanes, trace, mode, width }) {
  const kindOf = (type) => eventKinds[type] || "party";
  const nodes = new Map((overview?.nodes || []).map((node) => [node.type, { ...node, ghost: false }]));
  // A type only the simulated branch reaches, such as a declined application, joins the map as a hollow node.
  for (const item of [...(trace?.steps || []), ...(trace?.altSteps || [])]) {
    if (!nodes.has(item.type)) nodes.set(item.type, { type: item.type, count: 0, hours: item.hours, step: item.step, position: null, ghost: true });
  }
  if (!nodes.size) return null;
  const timed = mode === "time";
  const variants = overview?.variants || [];
  const weight = variants.reduce((sum, item) => sum + item.count, 0);
  const typical = weight ? variants.reduce((sum, item) => sum + item.count * item.types.length, 0) / weight : 8;
  const at = (node) => (timed ? node.hours ?? 0 : node.step ?? 1 + (node.position || 0) * (typical - 1));
  const widest = Math.max(...[...nodes.values()].map((node) => nodeWidth(node.type, node.count)));
  const plotLeft = LEFT + 6;
  const plotRight = Math.max(plotLeft + 240, width - 16 - widest);
  const max = Math.max(timed ? 1 : 2, ...[...nodes.values()].map(at));
  // Log time in five-minute units, so the first hour of an onboarding is not squeezed against the start.
  const scale = (hours) => Math.log1p(hours * 12);
  const x = timed
    ? (hours) => plotLeft + ((plotRight - plotLeft) * scale(hours)) / scale(max)
    : (step) => plotLeft + ((plotRight - plotLeft) * (step - 1)) / (max - 1);
  const ticks = timed
    ? TICKS.filter(([hours]) => hours <= max).map(([hours, label]) => ({ x: x(hours), label }))
    : stepTicks(max).map((step) => ({ x: x(step), label: step === 1 ? "step 1" : String(step) }));
  // Close the axis with the latest typical time when the fixed ticks stop well short of it.
  if (timed && plotRight - ticks.at(-1).x > 80) ticks.push({ x: plotRight, label: formatHours(max) });

  const kinds = lanes.map((lane) => lane.kind);
  for (const type of nodes.keys()) if (!kinds.includes(kindOf(type))) kinds.push(kindOf(type));
  const placed = new Map();
  const bands = [];
  let cursor = TOP;
  for (const kind of kinds) {
    const inLane = [...nodes.values()]
      .filter((node) => kindOf(node.type) === kind)
      .map((node) => ({ node, x: x(at(node)), w: nodeWidth(node.type, node.count) }))
      .sort((a, b) => a.x - b.x);
    if (!inLane.length) continue;
    // Keep each node on its axis position: slide it a little past its lane neighbour, or give it a row of its own.
    const ends = [];
    for (const item of inLane) {
      let row = ends.findIndex((end) => Math.max(item.x, end + 10) - item.x <= SLIDE);
      if (row < 0) {
        row = ends.length;
        ends.push(-Infinity);
      }
      const left = Math.max(item.x, ends[row] + 10);
      ends[row] = left + item.w;
      placed.set(item.node.type, { ...item.node, x: left, w: item.w, y: cursor + 10 + row * ROW_H });
    }
    const lane = lanes.find((entry) => entry.kind === kind) || { kind };
    bands.push({ kind, label: laneLabel(lane), y: cursor, height: 10 + ends.length * ROW_H });
    cursor += 10 + ends.length * ROW_H + 6;
  }
  const right = Math.max(width, ...[...placed.values()].map((node) => node.x + node.w + 16));
  return { nodes: placed, bands, ticks, right, height: cursor + 6, timed };
}

// Where a flow runs between two nodes, and the midpoint its label sits on.
// A forward flow that would run through other nodes on its row arcs over them instead.
function route(a, b, nodes) {
  if (a === b) {
    const cx = a.x + a.w - 16;
    return { d: `M${cx - 8},${a.y} C${cx - 10},${a.y - 18} ${cx + 10},${a.y - 18} ${cx + 8},${a.y}`, loop: true };
  }
  let p0;
  let p1;
  let p2;
  let p3;
  const over = a.y === b.y && b.x > a.x + a.w && [...nodes.values()].some((node) => node !== a && node !== b && node.y === a.y && node.x < b.x && node.x + node.w > a.x + a.w);
  if (over) {
    p0 = [a.x + a.w - 12, a.y];
    p3 = [b.x + 12, b.y];
    p1 = [p0[0] + 20, a.y - 30];
    p2 = [p3[0] - 20, b.y - 30];
  } else if (b.x >= a.x + a.w - 6) {
    p0 = [a.x + a.w, a.y + NODE_H / 2];
    p3 = [b.x, b.y + NODE_H / 2];
    const bend = Math.max(24, (p3[0] - p0[0]) / 2);
    p1 = [p0[0] + bend, p0[1]];
    p2 = [p3[0] - bend, p3[1]];
  } else if (a.y !== b.y) {
    const down = b.y > a.y;
    p0 = [a.x + a.w * 0.6, down ? a.y + NODE_H : a.y];
    p3 = [b.x + b.w * 0.4, down ? b.y : b.y + NODE_H];
    const bend = Math.max(18, Math.abs(p3[1] - p0[1]) / 2) * (down ? 1 : -1);
    p1 = [p0[0], p0[1] + bend];
    p2 = [p3[0], p3[1] - bend];
  } else {
    p0 = [a.x + a.w / 2, a.y + NODE_H];
    p3 = [b.x + b.w / 2, b.y + NODE_H];
    p1 = [p0[0], p0[1] + 24];
    p2 = [p3[0], p3[1] + 24];
  }
  const mid = [0, 1].map((i) => (p0[i] + 3 * p1[i] + 3 * p2[i] + p3[i]) / 8);
  return { d: `M${p0} C${p1} ${p2} ${p3}`, mid, loop: false };
}

function activate(handler) {
  return {
    role: "button",
    tabIndex: 0,
    onClick: (e) => {
      e.stopPropagation();
      handler();
    },
    onKeyDown: (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      handler();
    },
  };
}

// The run's process map with one journey traced on it. Nodes are event types placed by typical time or step;
// the journey's own events are the numbered steps under them, and clicking either selects it for the inspector.
export function ProcessMap({ overview, eventKinds, lanes, journey, mode, selection, onSelect, notesByType = {} }) {
  const wrap = useRef(null);
  const [width, setWidth] = useState(900);
  useEffect(() => {
    const element = wrap.current;
    if (!element || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(720, Math.floor(entry.contentRect.width))));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const trace = useMemo(() => traceOf(journey), [journey]);
  const map = useMemo(() => layoutMap({ overview, eventKinds, lanes, trace, mode, width }), [overview, eventKinds, lanes, trace, mode, width]);

  const stepsOf = new Map();
  for (const item of [...(trace?.steps || []), ...(trace?.altSteps || [])]) {
    stepsOf.set(item.type, [...(stepsOf.get(item.type) || []), item]);
  }
  // This journey's own wait on each transition it takes, the first time it takes it.
  const waits = new Map();
  for (const [a, b] of trace?.main || []) {
    const key = `${a.type}|${b.type}`;
    if (!waits.has(key)) waits.set(key, b.hours - a.hours);
  }
  const edges = (overview?.edges || []).map((edge) => ({ ...edge, key: `${edge.from}|${edge.to}` }));
  const known = new Set(edges.map((edge) => edge.key));
  // Transitions this journey takes that the overview has not counted, for example on a sample run.
  for (const [key, hours] of waits) {
    if (!known.has(key)) edges.push({ from: key.split("|")[0], to: key.split("|")[1], count: 0, key, hours });
  }
  const most = Math.max(1, ...edges.map((edge) => edge.count));
  const pick = (type) => {
    const own = stepsOf.get(type) || [];
    if (!own.length) return onSelect({ type, event: null });
    // Clicking a node again moves through its repeats in this journey.
    const current = own.findIndex((item) => item.id === selection?.event);
    return onSelect({ type, event: own[(current + 1) % own.length].id });
  };
  const between = (from, to) => {
    const a = map.nodes.get(from);
    const b = map.nodes.get(to);
    return a && b ? route(a, b, map.nodes) : null;
  };

  return (
    <div className="map-wrap" ref={wrap}>
      {map ? (
        <svg width={map.right} height={map.height} className="map" role="group" aria-label={`Process map by ${map.timed ? "time from the journey's start" : "step"}`}>
          {map.ticks.map((tick) => (
            <g key={tick.label}>
              <line x1={tick.x} x2={tick.x} y1={TOP - 8} y2={map.height - 6} className="tick" />
              <text x={tick.x} y={TOP - 14} textAnchor="middle" className="tick-label">{tick.label}</text>
            </g>
          ))}
          {map.bands.map((band) => (
            <g key={band.kind}>
              <rect x={LEFT - 6} y={band.y} width={map.right - LEFT} height={band.height} rx={12} className="lane-band" />
              <text x={4} y={band.y + 10 + NODE_H / 2 + 4} className="lane-name">{band.label}</text>
            </g>
          ))}
          {edges.map((edge) => {
            const path = between(edge.from, edge.to);
            if (!path) return null;
            const wait = waits.get(edge.key);
            const lit = wait !== undefined;
            return (
              <path key={edge.key} d={path.d} className="flow" data-lit={lit}
                style={{ strokeWidth: Math.max(lit ? 2 : 1, 1 + (5 * edge.count) / most), opacity: lit ? 0.85 : 0.1 + (0.3 * edge.count) / most }}>
                <title>
                  {`${edge.from} → ${edge.to}${edge.count ? `: ${edge.count} across the run` : ""}${edge.count && edge.hours != null ? `, typically ${formatHours(edge.hours)}` : ""}${lit ? ` · this journey waited ${formatHours(wait)}` : ""}`}
                </title>
              </path>
            );
          })}
          {(trace?.branch || []).map(([a, b], index) => {
            const path = between(a.type, b.type);
            return path ? (
              <path key={`branch-${index}`} d={path.d} className="flow branch">
                <title>{`${a.type} → ${b.type} in the simulated alternative, after ${formatHours(b.hours - a.hours)}`}</title>
              </path>
            ) : null;
          })}
          {map.timed
            ? [...waits].map(([key, hours]) => {
                const path = between(...key.split("|"));
                return path && !path.loop ? (
                  <text key={`wait-${key}`} x={path.mid[0]} y={path.mid[1] - 5} textAnchor="middle" className="wait">+{formatHours(hours)}</text>
                ) : null;
              })
            : null}
          {[...map.nodes.values()].map((node) => {
            const own = stepsOf.get(node.type) || [];
            const notes = notesByType[node.type] || 0;
            return (
              <g key={node.type}>
                <g
                  className="pm-node"
                  data-on={own.some((item) => !item.alt)}
                  data-ghost={node.ghost}
                  data-picked={selection?.type === node.type}
                  transform={`translate(${node.x},${node.y})`}
                  aria-label={`${node.type}${node.count ? `, ${node.count} across the run` : ", only in the simulated alternative"}${own.length ? `, ${own.length} in this journey` : ""}`}
                  {...activate(() => pick(node.type))}
                >
                  <title>{`${node.type}${node.count ? ` · ${node.count} across the run` : " · simulated alternative only"}${node.hours != null ? ` · typically ${formatHours(node.hours)} from the start` : ""}${notes ? ` · ${notes} note${notes === 1 ? "" : "s"}` : ""}`}</title>
                  <rect width={node.w} height={NODE_H} rx={9} />
                  <text x={9} y={19}>{shortLabel(node.type)}</text>
                  {node.count ? <text x={node.w - 8} y={19} textAnchor="end" className="pm-count">{node.count}</text> : null}
                  {notes ? (
                  <g className="pm-note" transform={`translate(${node.w - 2},1)`}>
                    <circle r={7} />
                    <text y={3} textAnchor="middle">{notes}</text>
                  </g>
                ) : null}
                </g>
                {own.map((item, index) => (
                  <g
                    key={item.id}
                    className="pm-step"
                    data-alt={item.alt}
                    data-on={selection?.event === item.id}
                    transform={`translate(${node.x + 9 + index * 17},${node.y + NODE_H + 11})`}
                    aria-label={`${item.type}, ${item.alt ? "simulated alternative " : ""}step ${item.step}`}
                    {...activate(() => onSelect({ type: item.type, event: item.id }))}
                  >
                    <title>{`${item.alt ? "Simulated alternative, " : ""}step ${item.step} · ${item.type} · ${formatHours(item.hours)} from the start`}</title>
                    <circle r={7.5} />
                    <text y={3.5} textAnchor="middle">{item.step}</text>
                  </g>
                ))}
              </g>
            );
          })}
        </svg>
      ) : null}
    </div>
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
