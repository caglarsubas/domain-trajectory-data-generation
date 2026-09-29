"use client";

import { useEffect, useState } from "react";
import { api, download } from "../lib/api";

const pct = (value) => (value == null ? "—" : `${Math.round(value * 100)}%`);

// What a mapped log would change in a run, before one is made: the steps it moves most and what the data cannot see.
function Preview({ preview }) {
  if (!preview) return null;
  const { moves = [], unseen = [], divergence = {} } = preview;
  return (
    <div className="preview">
      <small>
        Before a run: {preview.journeys.toLocaleString()} journeys over every sub-domain, with and without this source.
        {divergence.calibrated != null
          ? ` Next-step shares sit ${divergence.calibrated} from the data with it, against ${divergence.uncalibrated} without (weighted by how often each step is taken; 0 is a match).`
          : ""}
      </small>
      {moves.length ? (
        <table className="target-table">
          <thead>
            <tr>
              <th>After</th>
              <th>Next</th>
              <th>Pack</th>
              <th>Calibrated</th>
              <th>Data</th>
            </tr>
          </thead>
          <tbody>
            {moves.map((move) => (
              <tr key={`${move.after}|${move.next}`}>
                <td>{move.after}</td>
                <td>{move.next}</td>
                <td>{pct(move.pack)}</td>
                <td>{pct(move.calibrated)}</td>
                <td>{pct(move.data)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <small>It moves no step by five points or more.</small>
      )}
      {unseen.length ? <small>The data cannot see {unseen.join(", ")}; those keep the pack's weights.</small> : null}
    </div>
  );
}

function status(doc) {
  const calibration = doc.calibration;
  if (doc.ingest?.status === "pending") return "Downloading";
  if (doc.ingest?.status === "failed") return `Not downloaded: ${doc.ingest.detail}`;
  if (!calibration) return "Being read";
  if (calibration.status === "failed") return `Not read: ${calibration.reason}`;
  const parts = [`${calibration.format}`, `${(calibration.cases || 0).toLocaleString()} cases`];
  if (calibration.events && calibration.events !== calibration.cases) parts.push(`${calibration.events.toLocaleString()} events`);
  if (calibration.mapped_share != null && calibration.mapped_share < 1) parts.push(`${Math.round(calibration.mapped_share * 100)}% of events mapped`);
  if (calibration.outcomes) {
    parts.push(
      Object.entries(calibration.outcomes)
        .filter(([, value]) => value != null)
        .map(([name, value]) => `${name.replaceAll("_", " ")} ${Math.round(value * 100)}%`)
        .join(", ")
    );
  }
  return parts.join(" · ");
}

// A study's data sources: add a catalogue source, see what each calibrates, and correct an activity mapping.
export default function DataSources({ projectId, sector, docs, events, onChange }) {
  const [catalogue, setCatalogue] = useState([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState("");
  const [previewing, setPreviewing] = useState("");
  const [draft, setDraft] = useState({});
  const sources = docs.filter((doc) => doc.kind === "data_source");
  // Poll while a source downloads or is being read; a failed one says so and stops the polling.
  const working = sources.some((doc) => doc.ingest?.status === "pending" || (!doc.calibration && doc.unreadable_reason === "The data source is being read."));

  useEffect(() => {
    api(`/catalogue?sector=${sector}`).then((data) => setCatalogue(data.data)).catch(() => setCatalogue([]));
  }, [sector]);

  useEffect(() => {
    if (!working) return undefined;
    const timer = setInterval(onChange, 2000);
    return () => clearInterval(timer);
  }, [working, onChange]);

  async function add(entry) {
    setBusy(entry.id);
    setError("");
    try {
      await api(`/projects/${projectId}/catalogue`, { method: "POST", body: JSON.stringify({ entry: entry.id }) });
      await onChange();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function saveMapping(doc) {
    setBusy(doc.id);
    setError("");
    try {
      await api(`/projects/${projectId}/corpus/${doc.id}/mapping`, { method: "PUT", body: JSON.stringify({ mapping: draft }) });
      setEditing("");
      await onChange();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  if (!projectId) return null;
  const added = new Set(sources.map((doc) => doc.ingest?.catalogue).filter(Boolean));
  return (
    <div className="data-sources">
      <div className="panel-head">
        <h3>Data sources</h3>
        <small>Event logs calibrate how often each legal step follows another and how long it takes. Upload CSV, Parquet, XES, or OCEL 2.0 as a data source, or add a public one.</small>
      </div>
      {error ? <div className="error">{error}</div> : null}
      <p className="lede">
        Bringing your own journeys? Export them with one row per event, as <code>case_id</code>, <code>activity</code>, and{" "}
        <code>timestamp</code>, naming each activity as one of this pack's events, and they map without guessing.{" "}
        <button className="link" type="button" onClick={() => download(`/sectors/${sector}/log-template/events.csv`, `${sector}-events.csv`).catch((err) => setError(err.message))}>
          The pack's events
        </button>{" "}
        ·{" "}
        <button className="link" type="button" onClick={() => download(`/sectors/${sector}/log-template/example.csv`, `${sector}-example.csv`).catch((err) => setError(err.message))}>
          An example log
        </button>
      </p>
      {sources.map((doc) => (
        <div className="source" key={doc.id}>
          <div className="source-head">
            <span>{doc.name}</span>
            <small>{status(doc)}</small>
            {doc.calibration?.preview ? (
              <button className="ghost" type="button" onClick={() => setPreviewing(previewing === doc.id ? "" : doc.id)}>
                {previewing === doc.id ? "Hide preview" : "Preview"}
              </button>
            ) : null}
            {doc.calibration?.status === "ready" && Object.keys(doc.calibration.mapping || {}).length ? (
              <button className="ghost" type="button" onClick={() => { setEditing(editing === doc.id ? "" : doc.id); setDraft({}); }}>
                {editing === doc.id ? "Close" : "Mapping"}
              </button>
            ) : null}
          </div>
          {doc.ingest?.licence ? <small className="licence">{doc.ingest.licence}{doc.ingest.snapshot_date ? ` · fetched ${doc.ingest.snapshot_date}` : ""}{doc.ingest.doi ? ` · doi:${doc.ingest.doi}` : ""}</small> : null}
          {previewing === doc.id ? <Preview preview={doc.calibration.preview} /> : null}
          {editing === doc.id ? (
            <div className="mapping">
              <table className="target-table">
                <thead>
                  <tr>
                    <th>Activity</th>
                    <th>Events</th>
                    <th>Stands for</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(doc.calibration.activities || {}).map(([activity, count]) => {
                    const current = activity in draft ? draft[activity] : doc.calibration.mapping[activity];
                    return (
                      <tr key={activity}>
                        <td>{activity}</td>
                        <td>{count.toLocaleString()}</td>
                        <td>
                          <select value={current || ""} onChange={(e) => setDraft((value) => ({ ...value, [activity]: e.target.value || null }))}>
                            <option value="">Not mapped</option>
                            {events.map((name) => <option key={name} value={name}>{name}</option>)}
                          </select>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <div className="actions">
                <button className="primary" type="button" disabled={busy === doc.id || !Object.keys(draft).length} onClick={() => saveMapping(doc)}>
                  {busy === doc.id ? "Calibrating" : "Save and calibrate again"}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ))}
      <h4>Public sources</h4>
      <div className="catalogue">
        {catalogue.map((entry) => (
          <div className="catalogue-entry" key={entry.id} data-availability={entry.availability}>
            <div>
              <b>{entry.name}</b>
              {entry.licence ? <small> · {entry.licence}</small> : null}
              {entry.bytes ? <small> · {(entry.bytes / 1_000_000).toFixed(entry.bytes < 5_000_000 ? 1 : 0)} MB</small> : null}
            </div>
            {entry.describes ? <small>{entry.describes}</small> : null}
            {entry.calibrates ? <small>Calibrates: {entry.calibrates}</small> : null}
            {entry.availability === "download" ? (
              <button className="ghost" type="button" disabled={Boolean(busy) || added.has(entry.id)} onClick={() => add(entry)}>
                {added.has(entry.id) ? "Added" : busy === entry.id ? "Adding" : "Add to this study"}
              </button>
            ) : entry.availability === "upload" ? (
              <small className="muted">Upload its export as a data source. {entry.reason}</small>
            ) : entry.availability === "reviewed" ? (
              <small className="muted">Reviewed, not added: {entry.reason}</small>
            ) : (
              <small className="muted">Planned: {entry.reason}</small>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
