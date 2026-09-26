"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import Shell from "../../../../components/Shell";
import { api } from "../../../../lib/api";
import DownloadPanel from "../../../../components/DownloadPanel";
import EpisodeViewer from "../../../../components/EpisodeViewer";
import DecisionViewer from "../../../../components/DecisionViewer";
import SignalTable from "../../../../components/SignalTable";
import JudgePanel, { ScoreMeters } from "../../../../components/JudgePanel";
import RunDiff from "../../../../components/RunDiff";
import { GroupViewer, ProcessMap, QualityCard, VariantList, formatHours, journeyOverview, laneLabel, shortLabel, typeSummary } from "../../../../components/RunViews";

const PAGE = 100;

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

function decisionNote(decisions) {
  const groups = Object.entries(decisions.groups || {}).map(([name, count]) => `${count} ${name.replaceAll("_", " ")}`).join(", ");
  const value = decisions.mean_taken_value != null ? `; the step each journey took reaches the goal in ${Math.round(decisions.mean_taken_value * 100)}% of simulated continuations on average` : "";
  return `Recorded ${decisions.points} outcome ${decisions.points === 1 ? "decision" : "decisions"} (${groups}) as ${decisions.records.toLocaleString()} typed records${value}.`;
}

function modelNote(models) {
  const rows = Object.entries(models || {}).map(([policy, found]) => {
    const scores = Object.entries(found.pass_at_k || {}).map(([k, value]) => `pass@${k} ${Math.round(value * 100)}%`).join(", ");
    return scores ? `${policy.replace("provider:", "")}: ${scores} over ${found.episodes} ${found.episodes === 1 ? "episode" : "episodes"}` : null;
  }).filter(Boolean);
  return rows.length ? ` ${rows.join("; ")}.` : "";
}

function providerNote(provider) {
  if (!provider.rollouts && !provider.errors && !provider.skipped_rollouts) return "No group in this run parted at a decision, so no provider calls were made.";
  const rollouts = `${provider.rollouts} provider ${provider.rollouts === 1 ? "rollout" : "rollouts"} on your key used ${provider.calls} of ${provider.limit} calls`;
  const checked = provider.rollouts
    ? `; ${provider.checks.legal} took a legal step, ${provider.checks.grounded} named the case's own objects, ${provider.checks.no_call} made no call`
    : "";
  const skipped = provider.skipped_rollouts
    ? ` ${provider.skipped_rollouts} more ${provider.skipped_rollouts === 1 ? "was" : "were"} skipped ${provider.stopped_by === "errors" ? "after repeated provider errors" : "when the call budget ran out"}.`
    : "";
  const failed = provider.errors ? ` ${provider.errors} ${provider.errors === 1 ? "call" : "calls"} failed${provider.last_error ? `: ${provider.last_error}` : ""}.` : "";
  return `${rollouts}${checked}.${skipped}${failed}`;
}

export default function RunPage() {
  const params = useParams();
  const [run, setRun] = useState(null);
  const [family, setFamily] = useState([]);
  // What the map and inspector have in hand: an event type, and one of this journey's events of that type if it has one.
  const [selection, setSelection] = useState(null);
  const [stance, setStance] = useState("revise");
  const [comment, setComment] = useState("");
  const [journeyStance, setJourneyStance] = useState("keep");
  const [journeyComment, setJourneyComment] = useState("");
  const [picked, setPicked] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const router = useRouter();
  const [focus, setFocus] = useState("");
  const [sectors, setSectors] = useState([]);
  const [variant, setVariant] = useState("");
  const [view, setView] = useState("time");
  const [rollout, setRollout] = useState("");
  const [entries, setEntries] = useState([]);
  const [total, setTotal] = useState(0);
  const [detail, setDetail] = useState(null);

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
    setSelection(null);
    setVariant("");
    setRollout("");
    load(params.id).catch((err) => setError(err.message));
  }, [params.id]);

  useEffect(() => {
    api("/sectors").then((data) => setSectors(data.data)).catch(() => setSectors([]));
  }, []);

  const pending = run && ["queued", "generating"].includes(run.status);
  const judging = ["queued", "running"].includes(run?.judge_job?.status);
  useEffect(() => {
    if (!pending && !judging) return undefined;
    const timer = setTimeout(() => load(params.id).catch((err) => setError(err.message)), 1000);
    return () => clearTimeout(timer);
  }, [pending, judging, run, params.id]);

  const ready = ["generated", "evaluated"].includes(run?.status);

  // Journeys come a page at a time, for small and large runs alike; a variant narrows the list.
  useEffect(() => {
    if (!ready) return;
    const query = `limit=${PAGE}${variant ? `&variant=${variant}` : ""}`;
    api(`/runs/${run.id}/journeys?${query}`)
      .then((page) => {
        setEntries(page.data);
        setTotal(page.total);
        setFocus((current) => (page.data.some((entry) => entry.trajectory_id === current) ? current : page.data[0]?.trajectory_id || ""));
      })
      .catch((err) => setError(err.message));
  }, [ready, run?.id, variant]);

  useEffect(() => {
    if (!ready || !focus) return;
    setDetail(null);
    api(`/runs/${run.id}/journeys/${focus}`).then(setDetail).catch((err) => setError(err.message));
  }, [ready, run?.id, focus]);

  async function loadMore() {
    const query = `offset=${entries.length}&limit=${PAGE}${variant ? `&variant=${variant}` : ""}`;
    const page = await api(`/runs/${run.id}/journeys?${query}`);
    setEntries((current) => [...current, ...page.data]);
  }

  const overview = run?.generation?.overview || null;

  const layout = useMemo(() => {
    if (!detail) return null;
    const events = Object.fromEntries(detail.events.map((event) => [event.event_id, event]));
    const objects = Object.fromEntries(detail.objects.map((item) => [item.object_id, item]));
    const links = {};
    for (const link of detail.event_objects) {
      links[link.event_id] = links[link.event_id] || [];
      links[link.event_id].push({ ...link, object_type: objects[link.object_id]?.object_type || "party" });
    }
    const parent = detail.trajectories.find((item) => !item.parent_trajectory_id);
    if (!parent) return null;
    const sample = detail.samples.find((item) => item.sequences.some((sequence) => sequence.trajectory_id === parent.trajectory_id));
    const children = detail.trajectories.filter((item) => item.parent_trajectory_id === parent.trajectory_id);
    const alt = children.find((item) => item.trajectory_id === rollout) || children[0];
    return { events, links, parent, alt, sample, trajectories: detail.trajectories, transitions: detail.state_transitions };
  }, [detail, rollout]);

  if (run && ["queued", "generating", "failed", "cancelled"].includes(run.status)) {
    return (
      <Shell>
        <JobPanel
          run={run}
          error={error}
          onCancel={async () => {
            setError("");
            try {
              setRun(await api(`/runs/${run.id}/cancel`, { method: "POST" }));
            } catch (err) {
              setError(err.message);
            }
          }}
        />
      </Shell>
    );
  }

  if (!run || !layout) {
    return <Shell>{error ? <div className="error">{error}</div> : <p>Opening the journey.</p>}</Shell>;
  }

  const event = selection?.event ? layout.events[selection.event] : null;
  const transitions = event ? layout.transitions.filter((item) => item.event_id === event.event_id) : [];
  const cycle = run.cycles.at(-1);
  // The primary judge's unreadable rubrics; older cycles held one verdict per rubric.
  const unreadable = [
    ...new Set(
      (cycle?.models?.length
        ? (cycle.flags || []).filter((flag) => flag.kind === "unreadable" && flag.model === cycle.models[0])
        : (cycle?.verdicts || []).filter((verdict) => verdict.readable === false)
      ).map((item) => item.rubric.replaceAll("_", " "))
    ),
  ];
  const notesFor = (id) => (run.feedback || []).filter((note) => note.target_id === id);
  const pack = sectors.find((item) => item.id === (run.config.sector || "banking"));
  const eventKinds = pack?.event_kinds || {};
  const lanes = pack?.lanes || [{ kind: "party", object_type: "party" }];
  const mapOverview = overview?.nodes?.length ? overview : journeyOverview(layout);
  // Overviews stored before times were measured can only be laid out by step.
  const timed = mapOverview?.nodes?.[0]?.hours != null;
  const mode = timed ? view : "sequence";
  // A node counts notes on its type and on this journey's events of that type.
  const notesByType = {};
  for (const note of run.feedback || []) {
    if (note.target_type !== "event") continue;
    const type = layout.events[note.target_id]?.event_type || (note.target_id in eventKinds ? note.target_id : null);
    if (type) notesByType[type] = (notesByType[type] || 0) + 1;
  }
  const summary = selection ? typeSummary(mapOverview, selection.type) : null;
  const laneOf = (type) => lanes.find((lane) => lane.kind === (eventKinds[type] || "party")) || { kind: eventKinds[type] || "party" };
  const ownIds = [...layout.parent.event_ids, ...(layout.alt ? layout.alt.event_ids.filter((id) => !layout.parent.event_ids.includes(id)) : [])];
  const selectType = (type) => setSelection({ type, event: ownIds.find((id) => layout.events[id]?.event_type === type) || null });
  const stepOf = (id) => {
    const index = layout.parent.event_ids.indexOf(id);
    if (index >= 0) return `Step ${index + 1} of ${layout.parent.event_ids.length}`;
    return `Simulated alternative · step ${layout.alt.event_ids.indexOf(id) + 1}`;
  };

  async function saveFeedback(targetType, targetId, note = { stance, comment, reset: () => setComment("") }) {
    setError("");
    try {
      await api(`/runs/${run.id}/feedback`, {
        method: "POST",
        body: JSON.stringify({ target_type: targetType, target_id: targetId, stance: note.stance, comment: note.comment }),
      });
      note.reset();
      await load(run.id);
    } catch (err) {
      setError(err.message);
    }
  }

  async function evaluate() {
    setBusy(true);
    setError("");
    try {
      // The judge runs as a job; while it works, the page polls the run for its progress and cycle.
      const next = await api(`/runs/${run.id}/evaluate`, { method: "POST", body: JSON.stringify({}) });
      setRun(next);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function stopJudge() {
    setError("");
    try {
      await api(`/jobs/${run.judge_job.id}/cancel`, { method: "POST" });
      await load(run.id);
    } catch (err) {
      setError(err.message);
    }
  }

  async function regenerate() {
    setBusy(true);
    setError("");
    try {
      // The child is generated from this cycle's revision notes and this run's notes, then judged.
      const child = await api(`/runs/${run.id}/regenerate`, { method: "POST", body: JSON.stringify({}) });
      router.push(`/studio/runs/${child.id}`);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  const round = run.config.regeneration?.round || 1;
  // A cycle is final once the hard checks failed or the primary judge read every rubric; only an unread one is judged again.
  const judgedFully = Boolean(
    cycle && (!cycle.hard_check_passed || !(cycle.models?.length && (cycle.flags || []).some((flag) => flag.kind === "unreadable" && flag.model === cycle.models[0])))
  );
  const canRegenerate = judgedFully && !cycle.accepted && round < run.config.max_cycles;
  const judgeJob = run.judge_job;
  // judge_job is the latest attempt, so a failure there is the last word until the judge is asked again.
  const judgeFailed = judgeJob?.status === "failed";
  const explain = (text) => (text.includes("INFERENCE_ENGINE_API_KEY") ? "The platform judge is not configured on this server yet." : text);

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
        {!judgedFully ? (
          <button className="primary" type="button" onClick={evaluate} disabled={busy || judging || run.cycle_count >= run.config.max_cycles}>
            {busy ? "Asking" : judging ? "Judging" : cycle ? "Judge again" : "Ask the judge"}
          </button>
        ) : canRegenerate ? (
          <button className="primary" type="button" onClick={regenerate} disabled={busy}>
            {busy ? "Regenerating" : "Regenerate from notes"}
          </button>
        ) : null}
        <Link className="ghost" href={rerunHref} style={{ display: "inline-block" }}>Run again</Link>
        {cycle?.accepted ? <span className="accepted-badge">Accepted by the judge</span> : null}
      </div>
      {judgedFully && !cycle.accepted && !canRegenerate ? (
        <p className="note">This study has been judged {round} {round === 1 ? "time" : "times"}, its limit. Change the configuration and run it again.</p>
      ) : null}
      {canRegenerate ? (
        <p className="note">
          The judge did not accept this run. Regenerating makes round {round + 1} of {run.config.max_cycles}: a new run drawn with this cycle&apos;s
          {" "}{cycle.revision_notes?.length || 0} revision {cycle.revision_notes?.length === 1 ? "note" : "notes"}
          {run.feedback?.length ? ` and your ${run.feedback.length} ${run.feedback.length === 1 ? "note" : "notes"}` : ""}, judged as soon as it is ready.
        </p>
      ) : null}
      {judging ? (
        <div className="judge-status">
          <div className="bar"><i style={{ width: `${Math.round((judgeJob.progress || 0) * 100)}%` }} /></div>
          <small>{judgeJob.message}</small>
          <button className="ghost" type="button" onClick={stopJudge}>Stop</button>
        </div>
      ) : null}
      {judgeFailed ? <div className="error">The judge did not finish: {explain(judgeJob.error || judgeJob.message)}</div> : null}
      {error ? <div className="error">{explain(error)}</div> : null}
      {run.bundle_source === "fixture" ? (
        <p className="note">This is the banking sample. Generation is not running yet, so the canvas stays filled while you practice the loop.</p>
      ) : null}
      {run.generation ? (
        <p className="note">
          {run.generation.group_size > 1
            ? `Generated ${run.generation.primary_trajectories} groups of ${run.generation.group_size} sequences (${run.generation.primary_trajectories + run.generation.alternative_trajectories} journeys, ${run.generation.event_count} events).`
            : `Generated ${run.generation.primary_trajectories} synthetic ${run.generation.primary_trajectories === 1 ? "journey" : "journeys"} (${run.generation.event_count} events).`}
          {run.generation.limited_by === "event_budget"
            ? ` ${run.generation.requested_trajectories} were requested; the event budget stored fewer.`
            : null}
          {run.generation.limited_by === "studio_cap"
            ? ` ${run.generation.requested_trajectories} were requested; this view stores ${run.generation.primary_trajectories}.`
            : null}
          {run.generation.steering?.facts
            ? ` Steered by ${run.generation.steering.facts.steering} ${run.generation.steering.facts.steering === 1 ? "fact" : "facts"} from the documents${run.generation.steering.facts.awaiting_review ? `; ${run.generation.steering.facts.awaiting_review} more ${run.generation.steering.facts.awaiting_review === 1 ? "waits" : "wait"} for review in the composer` : ""}.`
            : null}
          {run.generation.calibration
            ? ` Calibrated from ${run.generation.calibration.sources.join(", ")} (${run.generation.calibration.cases.toLocaleString()} cases, ${(run.generation.calibration.steps_observed || 0).toLocaleString()} observed steps).`
            : null}
          {run.generation.decisions?.points
            ? ` ${decisionNote(run.generation.decisions)}`
            : null}
          {run.generation.episodes?.provider
            ? ` ${providerNote(run.generation.episodes.provider)}${modelNote(run.generation.episodes.models)}`
            : null}
          {run.generation.jurisdiction && run.generation.jurisdiction !== "neutral"
            ? ` Jurisdiction: ${sectors.find((item) => item.id === run.config.sector)?.jurisdictions?.find((item) => item.id === run.generation.jurisdiction)?.label || run.generation.jurisdiction}.`
            : null}
          {run.generation.target?.kind === "accepted_groups"
            ? ` ${run.generation.target.reached} of ${run.generation.target.requested} accepted groups reached.`
            : null}
          {run.generation.limited_by === "acceptance"
            ? " Too few groups were accepted: a part stopped after drawing five times its target."
            : null}
        </p>
      ) : null}
      {run.generation?.target?.buckets?.length > 1 || run.generation?.target?.kind === "accepted_groups" ? (
        <TargetTable target={run.generation.target} />
      ) : null}
      <SignalTable rewards={run.generation?.rewards} />
      {run.inherited_feedback_ids?.length ? (
        <p className="warn">This iteration inherited {run.inherited_feedback_ids.length} note{run.inherited_feedback_ids.length === 1 ? "" : "s"} from the previous run.</p>
      ) : null}
      {cycle && !cycle.hard_check_passed ? (
        <div className="error">{cycle.hard_check_errors.join(" ")}</div>
      ) : null}
      <ScoreMeters cycle={cycle} />
      {unreadable.length ? (
        <p className="warn">
          The judge returned no readable verdict for {unreadable.join(", ")}.
          Those verdicts are left unscored and add no revision notes. Evaluate again, or change the judge model.
        </p>
      ) : null}
      {cycle?.revision_notes?.length ? <p className="warn">{cycle.revision_notes.join(" ")}</p> : null}
      {run.parent_run_id ? <RunDiff run={run} /> : null}
      <JudgePanel
        cycle={cycle}
        onPick={(trajectoryId) => {
          setVariant("");
          setSelection(null);
          setFocus(trajectoryId);
          document.querySelector(".stage")?.scrollIntoView({ behavior: "smooth", block: "start" });
        }}
      />
      <QualityCard quality={run.generation?.quality} />
      {pack && overview && overview.journeys > 1 ? (
        <div className="overview">
          <VariantList
            variants={overview.variants}
            total={overview.journeys}
            distinct={overview.distinct_variants}
            active={variant}
            onPick={(key) => {
              setVariant(key);
              setFocus("");
              setSelection(null);
            }}
          />
        </div>
      ) : null}
      <div className="stage">
        <div className="canvas-wrap">
          <div className="panel-head">
            <h3>Process map</h3>
            <small>
              {mapOverview?.journeys > 1 ? "Every journey's transitions, thicker where more common. " : ""}
              {mode === "time" ? "Event types sit at their typical time from the start." : "Event types sit at their typical step."}
            </small>
          </div>
          <div className="map-controls">
            {total > 1 ? (
              <div className="journey-picker">
                <label htmlFor="journey">Traced journey {variant ? `· ${total} in this variant` : `· ${total.toLocaleString()} in the run`}</label>
                <div className="row">
                  <select
                    id="journey"
                    value={layout.parent.trajectory_id}
                    onChange={(e) => {
                      setFocus(e.target.value);
                      setSelection(null);
                      setRollout("");
                    }}
                  >
                    {entries.map((item, index) => (
                      <option key={item.trajectory_id} value={item.trajectory_id}>
                        {index + 1}. {item.trajectory_type.replaceAll("_", " ")} · {item.events} events{item.sequences > 1 ? ` · group of ${item.sequences}` : ""}
                      </option>
                    ))}
                  </select>
                  {entries.length < total ? (
                    <button className="ghost" type="button" style={{ flex: "0 0 auto" }} onClick={() => loadMore().catch((err) => setError(err.message))}>
                      Load {Math.min(PAGE, total - entries.length)} more
                    </button>
                  ) : null}
                </div>
              </div>
            ) : null}
            <div className="tabs" role="tablist" aria-label="Place event types by">
              {[["time", "Time axis"], ["sequence", "Sequence"]].map(([id, label]) => (
                <button key={id} type="button" role="tab" aria-selected={mode === id} data-on={mode === id} disabled={id === "time" && !timed}
                  title={id === "time" && !timed ? "This run was summarised before event times were measured." : undefined}
                  onClick={() => setView(id)}>{label}</button>
              ))}
            </div>
          </div>
          <GroupViewer
            sample={layout.sample}
            trajectories={layout.trajectories}
            events={layout.events}
            active={layout.alt?.trajectory_id}
            onPick={(id) => {
              if (id !== layout.parent.trajectory_id) setRollout(id);
              setSelection(null);
            }}
          />
          <p className="lede map-lede">
            Copper is the traced journey; numbers are its steps{mode === "time" ? " and labels its waits" : ""}.
            {layout.alt
              ? ` Hollow steps and the dashed line are a simulated alternative branch${layout.alt.probability != null ? `, chosen with probability ${layout.alt.probability} at the branch point` : ""}, not a causal counterfactual.`
              : ""}
            {" "}Select a node or a step to inspect it and leave a note.
          </p>
          <EpisodeViewer key={detail?.episodes?.[0]?.episode_id} episode={detail?.episodes?.[0]} />
          <DecisionViewer key={detail?.decisions?.[0]?.decision_id} decisions={detail?.decisions} />
          <ProcessMap
            overview={mapOverview}
            eventKinds={eventKinds}
            lanes={lanes}
            journey={layout}
            mode={mode}
            selection={selection}
            onSelect={setSelection}
            notesByType={notesByType}
          />
        </div>
        <aside className="inspector">
          {selection ? (
            <button type="button" className="text-btn back" onClick={() => setSelection(null)}>← This journey</button>
          ) : null}
          {event ? (
            <>
              <h2 className="word" style={{ marginTop: 0 }}>{event.event_type}</h2>
              <p className="lede tight">{stepOf(event.event_id)} · {formatHours(Math.max(0, (new Date(event.event_time) - new Date(layout.events[layout.parent.event_ids[0]].event_time)) / 3600000))} from the start</p>
              <div className="kvs">
                <span>Event</span><b>{event.event_id}</b>
                <span>When</span><b>{new Date(event.event_time).toLocaleString()}</b>
                <span>Effective</span><b>{event.effective_time ? new Date(event.effective_time).toLocaleString() : "—"}</b>
                <span>Recorded</span><b>{event.recorded_at ? new Date(event.recorded_at).toLocaleString() : "—"}</b>
                <span>Status</span><b>{event.observation_status}</b>
                <span>Channel</span><b>{event.channel_id || "—"}</b>
                <span>Amount</span><b>{event.amount != null ? `${event.direction === "debit" ? "−" : event.direction === "credit" ? "+" : ""}${event.amount} ${event.currency || ""}${event.amount_role ? ` · ${event.amount_role.replaceAll("_", " ")}` : ""}`.trim() : "—"}</b>
              </div>
              {transitions.map((item) => (
                <p key={item.state_dimension}>{item.state_dimension}: {item.state_before || "none"} → {item.state_after}</p>
              ))}
              {(layout.links[event.event_id] || []).length ? (
                <ul className="objects">
                  {layout.links[event.event_id].map((link) => (
                    <li key={link.object_id + link.object_role}>
                      <i className={`mark ${markClass(link.object_type)}`} />
                      <span>{link.object_type.replaceAll("_", " ")}</span>
                      <small>{link.object_role ? `${link.object_role.replaceAll("_", " ")} · ` : ""}{link.object_id}</small>
                    </li>
                  ))}
                </ul>
              ) : null}
              <TypeContext summary={summary} onPick={selectType} />
              <ul className="feedback-list">
                {notesFor(event.event_id).map((note) => <li key={note.id}><strong>{note.stance}</strong> {note.comment}</li>)}
                {notesFor(event.event_type).map((note) => <li key={note.id}><strong>{note.stance}</strong> every {shortLabel(event.event_type)}: {note.comment}</li>)}
              </ul>
              <FeedbackBox stance={stance} setStance={setStance} comment={comment} setComment={setComment} onSave={() => saveFeedback("event", event.event_id)} />
            </>
          ) : selection ? (
            <>
              <h2 className="word" style={{ marginTop: 0 }}>{selection.type}</h2>
              <p className="lede tight">Not in this journey</p>
              <div className="kvs">
                <span>Lane</span><b>{laneLabel(laneOf(selection.type))}</b>
                <span>Across the run</span><b>{summary.node ? `${summary.node.count.toLocaleString()} times` : "—"}</b>
                <span>Typically at</span><b>{summary.node?.hours != null ? `${formatHours(summary.node.hours)} from the start` : "—"}</b>
                <span>Typical step</span><b>{summary.node?.step ?? "—"}</b>
                <span>Repeats</span><b>{summary.repeats ? summary.repeats.toLocaleString() : "—"}</b>
              </div>
              <TypeContext summary={summary} onPick={selectType} />
              <ul className="feedback-list">
                {notesFor(selection.type).map((note) => <li key={note.id}><strong>{note.stance}</strong> {note.comment}</li>)}
              </ul>
              <p className="lede">The next run reads a note here for every {shortLabel(selection.type)}: drop leaves it out, keep favours it, revise asks for a change.</p>
              <FeedbackBox stance={stance} setStance={setStance} comment={comment} setComment={setComment} onSave={() => saveFeedback("event", selection.type)} />
            </>
          ) : (
            <>
              <h2 className="word" style={{ marginTop: 0 }}>This journey</h2>
              <p className="lede">
                {layout.parent.trajectory_type.replaceAll("_", " ")} · {layout.parent.event_ids.length} events. A drop note on the journey keeps this kind out of the next run; keep asks for more like it.
              </p>
              <ul className="feedback-list">
                {notesFor(layout.parent.trajectory_id).map((note) => <li key={note.id}><strong>{note.stance}</strong> {note.comment}</li>)}
              </ul>
              <FeedbackBox
                stance={journeyStance}
                setStance={setJourneyStance}
                comment={journeyComment}
                setComment={setJourneyComment}
                onSave={() => saveFeedback("trajectory", layout.parent.trajectory_id, { stance: journeyStance, comment: journeyComment, reset: () => setJourneyComment("") })}
              />
              <h2 className="word">Whole run</h2>
              <p className="lede">Select a node or a numbered step on the map to see its objects, state change, and typical flow. Notes can also sit on the run itself.</p>
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
      <DownloadPanel run={run} paged={run.bundle_source === "paged"} />
    </Shell>
  );
}

// Where an event type sits across the run: what usually comes before and after it. Each neighbour opens in the inspector.
function TypeContext({ summary, onPick }) {
  if (!summary?.node) return <p className="lede">No primary journey in this run reaches it; only a simulated alternative does.</p>;
  const list = (title, items, side, wait) => (items.length ? (
    <div className="flows">
      <span>{title}</span>
      {items.map((edge) => (
        <button key={edge[side]} type="button" onClick={() => onPick(edge[side])} title={`${edge.count.toLocaleString()} transitions across the run`}>
          <b>{edge[side]}</b>
          <small>{edge.count.toLocaleString()}×{edge.hours != null ? ` · ${wait(formatHours(edge.hours))}` : ""}</small>
        </button>
      ))}
    </div>
  ) : null);
  return (
    <div className="type-context">
      {list("Usually after", summary.prev, "from", (time) => `${time} before`)}
      {list("Usually followed by", summary.next, "to", (time) => `${time} later`)}
    </div>
  );
}

function JobPanel({ run, error, onCancel }) {
  const job = run.job || {};
  const active = ["queued", "generating"].includes(run.status);
  const label = {
    queued: "Waiting for a worker",
    generating: "Generating journeys",
    failed: "Generation failed",
    cancelled: "Generation cancelled",
  }[run.status] || run.status;
  return (
    <div className="job-panel" data-state={run.status}>
      <h1 className="word">{label}</h1>
      <p className="lede">
        {run.config.target_trajectory_count} {run.config.target_kind === "accepted_groups" ? `accepted groups of ${run.config.group_size}` : run.config.group_size > 1 ? `prompts × ${run.config.group_size} sequences` : "journeys"} · {(run.config.sub_domains || []).map((item) => item.replaceAll("_", " ")).join(", ")}
      </p>
      {active ? (
        <>
          <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((job.progress || 0) * 100)}>
            <i style={{ width: `${Math.max(2, Math.round((job.progress || 0) * 100))}%` }} />
          </div>
          <p className="lede">{job.message || "Starting."} This page updates by itself.</p>
          <div className="actions">
            <button className="ghost" type="button" onClick={onCancel}>Cancel</button>
          </div>
        </>
      ) : (
        <>
          {job.error ? <div className="error">{job.error}</div> : <p className="lede">{job.message}</p>}
          <div className="actions">
            <Link className="primary" href="/studio/compose" style={{ display: "inline-block" }}>Compose again</Link>
          </div>
        </>
      )}
      {error ? <div className="error">{error}</div> : null}
    </div>
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

function TargetTable({ target }) {
  const accepted = target.kind === "accepted_groups";
  const reasons = { acceptance: "Acceptance ceiling", event_budget: "Event budget" };
  return (
    <table className="target-table">
      <thead>
        <tr>
          <th>Part</th>
          <th>Target</th>
          <th>{accepted ? "Groups drawn" : "Generated"}</th>
          {accepted ? <th>Accepted</th> : null}
          <th>Stopped by</th>
        </tr>
      </thead>
      <tbody>
        {target.buckets.map((bucket, index) => (
          <tr key={index}>
            <td>{bucket.sub_domains.map((item) => item.replaceAll("_", " ")).join(", ")}</td>
            <td>{bucket.target}</td>
            <td>{bucket.generated}</td>
            {accepted ? <td>{bucket.accepted}{bucket.generated ? ` (${Math.round((bucket.accepted / bucket.generated) * 100)}%)` : ""}</td> : null}
            <td>{reasons[bucket.stopped_by] || "Target reached"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
