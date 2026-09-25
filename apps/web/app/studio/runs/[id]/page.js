"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import Shell from "../../../../components/Shell";
import { api } from "../../../../lib/api";

function markClass(type) {
  if (type === "party") return "party";
  if (type === "account") return "account";
  if (type === "application") return "application";
  if (type === "card") return "card";
  if (type === "loan") return "loan";
  if (type === "complaint") return "complaint";
  if (type === "quote") return "quote";
  if (type === "policy") return "policy";
  if (type === "claim") return "claim";
  return "kyc_case";
}

export default function RunPage() {
  const params = useParams();
  const [run, setRun] = useState(null);
  const [family, setFamily] = useState([]);
  const [selected, setSelected] = useState(null);
  const [stance, setStance] = useState("revise");
  const [comment, setComment] = useState("");
  const [picked, setPicked] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [focus, setFocus] = useState("");

  async function load(id) {
    const [current, all] = await Promise.all([api(`/runs/${id}`), api("/runs")]);
    setRun(current);
    const byId = Object.fromEntries(all.data.map((item) => [item.id, item]));
    let root = current;
    const guard = new Set();
    while (root.parent_run_id && byId[root.parent_run_id] && !guard.has(root.id)) {
      guard.add(root.id);
      root = byId[root.parent_run_id];
    }
    const ids = [];
    function walk(nodeId) {
      ids.push(nodeId);
      all.data.filter((item) => item.parent_run_id === nodeId).forEach((item) => walk(item.id));
    }
    walk(root.id);
    setFamily(ids.map((item) => byId[item]).filter(Boolean));
    setPicked((current.feedback || []).filter((note) => note.stance !== "keep").map((note) => note.id));
  }

  useEffect(() => {
    setFocus("");
    setSelected(null);
    load(params.id).catch((err) => setError(err.message));
  }, [params.id]);

  const layout = useMemo(() => {
    if (!run) return null;
    const events = Object.fromEntries(run.bundle.events.map((event) => [event.event_id, event]));
    const links = {};
    for (const link of run.bundle.event_objects) {
      links[link.event_id] = links[link.event_id] || [];
      const object = run.bundle.objects.find((item) => item.object_id === link.object_id);
      links[link.event_id].push({ ...link, object_type: object?.object_type || "party" });
    }
    const primaries = run.bundle.trajectories.filter((item) => !item.parent_trajectory_id);
    const parent = primaries.find((item) => item.trajectory_id === focus) || primaries[0];
    if (!parent) return null;
    const alt = run.bundle.trajectories.find((item) => item.parent_trajectory_id === parent.trajectory_id);
    const altOnly = alt ? alt.event_ids.filter((id) => !parent.event_ids.includes(id)) : [];
    const branchAt = alt ? parent.event_ids.indexOf(alt.branch_event_id) : -1;
    const columns = Math.max(parent.event_ids.length, branchAt + 1 + altOnly.length, 1);
    return { events, links, parent, alt, altOnly, branchAt, columns, primaries };
  }, [run, focus]);

  if (!run || !layout) {
    return <Shell>{error ? <div className="error">{error}</div> : <p>Opening the journey.</p>}</Shell>;
  }

  const event = selected ? layout.events[selected] : null;
  const transitions = event ? run.bundle.state_transitions.filter((item) => item.event_id === event.event_id) : [];
  const cycle = run.cycles.at(-1);
  const notesFor = (id) => (run.feedback || []).filter((note) => note.target_id === id);

  async function saveFeedback(targetType, targetId) {
    setError("");
    try {
      await api(`/runs/${run.id}/feedback`, {
        method: "POST",
        body: JSON.stringify({ target_type: targetType, target_id: targetId, stance, comment }),
      });
      setComment("");
      await load(run.id);
    } catch (err) {
      setError(err.message);
    }
  }

  async function evaluate() {
    setBusy(true);
    setError("");
    try {
      const next = await api(`/runs/${run.id}/evaluate`, { method: "POST", body: JSON.stringify({}) });
      setRun(next);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const rerunHref = `/studio/compose?from=${run.id}&notes=${picked.join(",")}`;

  return (
    <Shell>
      <div className="spine">
        {family.map((item, index) => (
          <Link key={item.id} href={`/studio/runs/${item.id}`} data-on={item.id === run.id}>
            <small>Run {index + 1}</small>
            <strong>{item.status}</strong>
            <small>{item.headline_score != null ? `Score ${item.headline_score}` : "Not judged"}</small>
          </Link>
        ))}
      </div>
      <div className="actions">
        <button className="primary" type="button" onClick={evaluate} disabled={busy || run.cycle_count >= run.config.max_cycles}>
          {busy ? "Judging" : "Ask the judge"}
        </button>
        <Link className="ghost" href={rerunHref} style={{ display: "inline-block" }}>Run again</Link>
      </div>
      {error ? <div className="error">{error.includes("INFERENCE_ENGINE_API_KEY") ? "The platform judge is not configured on this server yet." : error}</div> : null}
      {run.bundle_source === "fixture" ? (
        <p className="note">This is the banking sample. Generation is not running yet, so the canvas stays filled while you practice the loop.</p>
      ) : null}
      {run.generation ? (
        <p className="note">
          Generated {run.generation.primary_trajectories} synthetic {run.generation.primary_trajectories === 1 ? "journey" : "journeys"} ({run.generation.event_count} events).
          {run.generation.limited_by === "event_budget"
            ? ` ${run.generation.requested_trajectories} were requested; the event budget stored fewer.`
            : null}
          {run.generation.limited_by === "studio_cap"
            ? ` ${run.generation.requested_trajectories} were requested; this view stores ${run.generation.primary_trajectories}.`
            : null}
        </p>
      ) : null}
      {run.inherited_feedback_ids?.length ? (
        <p className="warn">This iteration inherited {run.inherited_feedback_ids.length} note{run.inherited_feedback_ids.length === 1 ? "" : "s"} from the previous run.</p>
      ) : null}
      {cycle && !cycle.hard_check_passed ? (
        <div className="error">{cycle.hard_check_errors.join(" ")}</div>
      ) : null}
      <div className="scores">
        {(cycle?.verdicts || ["helpfulness", "correctness", "safety", "pairwise_quality"].map((rubric) => ({ rubric, score: null }))).map((verdict) => (
          <div className="meter" key={verdict.rubric}>
            <span>{verdict.rubric.replaceAll("_", " ")}</span>
            <strong>{verdict.score == null ? "—" : verdict.score}</strong>
            <div className="bar"><i style={{ width: `${verdict.score == null ? 0 : Math.min(100, verdict.rubric === "helpfulness" ? verdict.score * 20 : verdict.score * 100)}%` }} /></div>
          </div>
        ))}
      </div>
      {cycle?.revision_notes?.length ? <p className="warn">{cycle.revision_notes.join(" ")}</p> : null}
      <div className="stage">
        <div className="canvas-wrap">
          {layout.primaries.length > 1 ? (
            <div className="journey-picker">
              <label htmlFor="journey">Journey</label>
              <select
                id="journey"
                value={layout.parent.trajectory_id}
                onChange={(e) => {
                  setFocus(e.target.value);
                  setSelected(null);
                }}
              >
                {layout.primaries.map((item, index) => (
                  <option key={item.trajectory_id} value={item.trajectory_id}>
                    {index + 1}. {item.trajectory_type.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          {layout.alt ? <p className="lede">The lower row is a simulated alternative branch, not a causal counterfactual.</p> : null}
          <div className="canvas" style={{ gridTemplateColumns: `repeat(${layout.columns}, minmax(108px, 1fr))` }}>
            {layout.parent.event_ids.map((id, index) => (
              <EventNode
                key={id}
                event={layout.events[id]}
                links={layout.links[id] || []}
                selected={selected === id}
                alt={false}
                column={index + 1}
                notes={notesFor(id)}
                onSelect={() => setSelected(id)}
              />
            ))}
            {layout.altOnly.map((id, index) => (
              <EventNode
                key={id}
                event={layout.events[id]}
                links={layout.links[id] || []}
                selected={selected === id}
                alt
                column={layout.branchAt + 1 + index + 1}
                notes={notesFor(id)}
                onSelect={() => setSelected(id)}
              />
            ))}
          </div>
        </div>
        <aside className="inspector">
          {event ? (
            <>
              <h2 className="word" style={{ marginTop: 0 }}>{event.event_type}</h2>
              <div className="kvs">
                <span>Event</span><b>{event.event_id}</b>
                <span>When</span><b>{new Date(event.event_time).toLocaleString()}</b>
                <span>Effective</span><b>{event.effective_time ? new Date(event.effective_time).toLocaleString() : "—"}</b>
                <span>Recorded</span><b>{event.recorded_at ? new Date(event.recorded_at).toLocaleString() : "—"}</b>
                <span>Status</span><b>{event.observation_status}</b>
                <span>Channel</span><b>{event.channel_id || "—"}</b>
                <span>Amount</span><b>{event.amount != null ? `${event.amount} ${event.currency || ""}`.trim() : "—"}</b>
              </div>
              {transitions.map((item) => (
                <p key={item.state_dimension}>{item.state_dimension}: {item.state_before || "none"} → {item.state_after}</p>
              ))}
              <ul className="feedback-list">
                {notesFor(event.event_id).map((note) => <li key={note.id}><strong>{note.stance}</strong> {note.comment}</li>)}
              </ul>
              <FeedbackBox stance={stance} setStance={setStance} comment={comment} setComment={setComment} onSave={() => saveFeedback("event", event.event_id)} />
            </>
          ) : (
            <>
              <h2 className="word" style={{ marginTop: 0 }}>Whole run</h2>
              <p className="lede">Select an event to see its objects and state change. Notes can also sit on the run itself.</p>
              <ul className="feedback-list">
                {(run.feedback || []).filter((note) => note.target_type === "run").map((note) => (
                  <li key={note.id}>
                    <label>
                      <input
                        type="checkbox"
                        checked={picked.includes(note.id)}
                        onChange={(e) => setPicked((current) => e.target.checked ? [...current, note.id] : current.filter((id) => id !== note.id))}
                      />{" "}
                      <strong>{note.stance}</strong> {note.comment}
                    </label>
                  </li>
                ))}
              </ul>
              <FeedbackBox stance={stance} setStance={setStance} comment={comment} setComment={setComment} onSave={() => saveFeedback("run", run.id)} />
            </>
          )}
          <div className="docs" style={{ marginTop: 16 }}>
            {(run.feedback || []).filter((note) => note.target_type !== "run").map((note) => (
              <label key={note.id} className="doc">
                <span>
                  <input
                    type="checkbox"
                    checked={picked.includes(note.id)}
                    onChange={(e) => setPicked((current) => e.target.checked ? [...current, note.id] : current.filter((id) => id !== note.id))}
                  />{" "}
                  {note.target_id}: {note.comment}
                </span>
                <small>{note.stance}</small>
              </label>
            ))}
          </div>
        </aside>
      </div>
    </Shell>
  );
}

function EventNode({ event, links, selected, alt, column, notes, onSelect }) {
  return (
    <button type="button" className="node" data-on={selected} data-alt={alt} style={{ gridColumn: column, gridRow: alt ? 2 : 1 }} onClick={onSelect}>
      <div className="marks">
        {links.map((link) => <i key={link.object_id + link.object_role} className={`mark ${markClass(link.object_type)}`} title={link.object_type} />)}
      </div>
      <b>{event.event_type}</b>
      <small>{alt ? "branch · " : ""}{event.event_id}{notes.length ? ` · ${notes.length} note` : ""}</small>
    </button>
  );
}

function FeedbackBox({ stance, setStance, comment, setComment, onSave }) {
  return (
    <>
      <div className="chips">
        {["keep", "revise", "drop"].map((item) => (
          <button key={item} type="button" className="stance" data-on={stance === item} onClick={() => setStance(item)}>{item}</button>
        ))}
      </div>
      <textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="What should the next pass keep or change?" />
      <div className="actions">
        <button className="primary" type="button" disabled={!comment.trim()} onClick={onSave}>Leave note</button>
      </div>
    </>
  );
}
