"use client";

import { useState } from "react";
import { api } from "../lib/api";

const KIND = { solution: "Solution", behavior: "Behavior" };
const STATUS = { proposed: "Proposed; review it", approved: "Approved; asked in the study's cycles", retired: "Retired" };

function Editor({ rubric, onSave, onCancel }) {
  const [title, setTitle] = useState(rubric.title);
  const [description, setDescription] = useState(rubric.description);
  const [criteria, setCriteria] = useState(rubric.criteria.join("\n"));
  const [anchors, setAnchors] = useState({ ...rubric.anchors });
  return (
    <form
      className="rubric-editor"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({ title, description, criteria: criteria.split("\n"), anchors });
      }}
    >
      <label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={80} /></label>
      <label>Description<input value={description} onChange={(event) => setDescription(event.target.value)} maxLength={300} /></label>
      <label>Criteria, one per line<textarea value={criteria} onChange={(event) => setCriteria(event.target.value)} /></label>
      {["5", "3", "1"].map((score) => (
        <label key={score}>
          A journey scoring {score}
          <input value={anchors[score] || ""} onChange={(event) => setAnchors({ ...anchors, [score]: event.target.value })} maxLength={300} />
        </label>
      ))}
      <div className="rubric-actions">
        <button className="primary" type="submit">Save</button>
        <button className="ghost" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function Card({ rubric, projectId, onChange, setError }) {
  const [editing, setEditing] = useState(false);
  const source = rubric.source || {};
  async function act(path, options) {
    setError("");
    try {
      await api(`/projects/${projectId}/rubrics/${rubric.id}${path}`, options);
      setEditing(false);
      await onChange();
    } catch (err) {
      setError(err.message);
    }
  }
  return (
    <div className="rubric-card" data-status={rubric.status}>
      <div className="panel-head">
        <strong>{KIND[rubric.kind] || rubric.kind}: {rubric.title}</strong>
        <small>{STATUS[rubric.status] || rubric.status}{rubric.edited ? " · edited" : ""}</small>
      </div>
      {editing ? (
        <Editor rubric={rubric} onCancel={() => setEditing(false)} onSave={(fields) => act("", { method: "PATCH", body: JSON.stringify(fields) })} />
      ) : (
        <>
          {rubric.description ? <p className="lede tight">{rubric.description}</p> : null}
          <ul>
            {rubric.criteria.map((criterion) => <li key={criterion}>{criterion}</li>)}
          </ul>
          <p className="lede tight">
            {["5", "3", "1"].map((score) => `${score}: ${rubric.anchors[score]}`).join(" · ")}
          </p>
          <small>
            {source.trajectory_ids?.length
              ? `Proposed by ${source.judge_model} from ${source.trajectory_ids.length} journeys${source.group ? " of one group" : ""}`
              : "Proposed by the judge"}
            {source.reference?.length ? `, with passages from ${source.reference.join(", ")}` : ""}
            {source.fit ? `; it rated how clearly they differ at ${source.fit} of 5` : ""}.
            {rubric.status === "approved" ? ` Registered as ${rubric.engine_name}.` : ""}
          </small>
          {rubric.status !== "retired" ? (
            <div className="rubric-actions">
              {rubric.status === "proposed" ? (
                <button className="primary" type="button" onClick={() => act("/approve", { method: "POST" })}>Approve</button>
              ) : null}
              <button className="ghost" type="button" onClick={() => setEditing(true)}>Edit</button>
              <button className="ghost" type="button" onClick={() => act("", { method: "DELETE" })}>Delete</button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

// The judge proposes a solution and a behavior rubric for the study from a group of this run's journeys; the owner reviews them.
export default function StudyRubrics({ run, onChange }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showRetired, setShowRetired] = useState(false);
  const job = run.proposal_job;
  const proposing = ["queued", "running"].includes(job?.status);
  const rubrics = run.study_rubrics || [];
  const shown = rubrics.filter((item) => showRetired || item.status !== "retired");
  const retired = rubrics.length - rubrics.filter((item) => item.status !== "retired").length;

  async function propose() {
    setBusy(true);
    setError("");
    try {
      await api(`/runs/${run.id}/rubric-proposals`, { method: "POST" });
      await onChange();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="judge-panel study-rubrics">
      <div className="panel-head">
        <h3>Study rubrics</h3>
        <button className="ghost" type="button" onClick={propose} disabled={busy || proposing}>
          {proposing ? "Proposing" : rubrics.length ? "Propose again" : "Propose from this run"}
        </button>
      </div>
      <p className="lede tight">
        The judge reads a group of this run&apos;s journeys with the warm-start passages and proposes a solution and a behavior rubric for
        this study. Edit them as you need; an approved rubric is registered with the engine and asked of every journey in the study&apos;s
        next cycles, and compared with the code&apos;s rubric of its kind. Study rubrics are reported and do not decide acceptance.
      </p>
      {proposing ? <p className="lede tight">{job.message || "Waiting for the judge."}</p> : null}
      {job?.status === "failed" ? <div className="error">The judge did not propose rubrics: {job.error || job.message}</div> : null}
      {error ? <div className="error">{error}</div> : null}
      {shown.map((rubric) => (
        <Card key={rubric.id} rubric={rubric} projectId={run.project_id} onChange={onChange} setError={setError} />
      ))}
      {retired ? (
        <button className="ghost" type="button" onClick={() => setShowRetired(!showRetired)}>
          {showRetired ? "Hide retired" : `Show ${retired} retired`}
        </button>
      ) : null}
    </div>
  );
}
