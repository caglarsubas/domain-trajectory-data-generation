"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiText, download } from "../lib/api";

const PARTS = [
  ["samples.jsonl", "Samples", "One line per prompt: the group of sequences, turns, rewards, advantages, and split."],
  ["episodes.jsonl", "Episodes", "Each decision as an agent task: state, operations, task, rubric, skeleton, and scored rollouts."],
  ["episodes-openai.jsonl", "Episodes · chat tools", "Every rollout as chat messages with tool calls, for training."],
  ["episodes-anthropic.jsonl", "Episodes · tool blocks", "Every rollout as content blocks with tool use and results, for training."],
  ["episodes-react.jsonl", "Episodes · ReAct", "Every rollout as Thought, Action, and Observation text, held out to measure generalization."],
  ["domain.jsonl", "Domain records", "Objects, relationships, events, event-object links, state changes, and trajectories."],
  ["ocel.json", "OCEL 2.0", "The domain layer for process-mining tools such as PM4Py."],
  ["manifest.json", "Manifest", "Configuration, counts, split, judge cycles, quality, data card, and file checksums."],
];

function bytes(value) {
  if (value == null) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export default function DownloadPanel({ run, paged = false }) {
  const [heldOut, setHeldOut] = useState("");
  const [card, setCard] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [exports, setExports] = useState([]);
  const [allowUnaccepted, setAllowUnaccepted] = useState(false);
  // Export follows the judge: a run it has not accepted exports only when asked, and the manifest says so.
  const latest = run.cycles?.at(-1);
  const accepted = Boolean(latest?.accepted);
  const open = accepted || allowUnaccepted;
  const params = new URLSearchParams();
  if (heldOut) params.set("held_out", heldOut);
  if (!accepted && allowUnaccepted) params.set("allow_unaccepted", "true");
  const query = params.toString() ? `?${params}` : "";
  const prefix = `run-${run.id.slice(0, 8)}${heldOut ? `-heldout-${heldOut}` : ""}${accepted ? "" : "-unaccepted"}`;
  const current = exports.find((item) => (item.held_out || "") === heldOut && Boolean(item.unaccepted) === !accepted);
  const working = exports.some((item) => ["queued", "running"].includes(item.job.status));

  const refresh = useCallback(async () => {
    try {
      setExports((await api(`/runs/${run.id}/exports`)).data);
    } catch (err) {
      setError(err.message);
    }
  }, [run.id]);

  useEffect(() => {
    if (paged && run.generation) refresh();
  }, [paged, run.generation, refresh]);

  useEffect(() => {
    if (!working) return undefined;
    const timer = setInterval(refresh, 1500);
    return () => clearInterval(timer);
  }, [working, refresh]);

  async function prepare() {
    setError("");
    setBusy("prepare");
    try {
      setExports(
        (await api(`/runs/${run.id}/exports`, { method: "POST", body: JSON.stringify({ held_out: heldOut || null, allow_unaccepted: !accepted && allowUnaccepted }) })).data
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function save(part) {
    setError("");
    setBusy(part);
    try {
      const gzipped = paged && part !== "manifest.json";
      await download(`/runs/${run.id}/export/${part}${query}`, `${prefix}-${part}${gzipped ? ".gz" : ""}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function preview() {
    setError("");
    setBusy("preview");
    try {
      setCard(JSON.parse(await apiText(`/runs/${run.id}/export/manifest.json${query}`)));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  if (!run.generation) return null;
  return (
    <div className="download-panel">
      <div className="panel-head">
        <h3>Export</h3>
        <small>
          Every record is synthetic. The split is fixed by the run and sample ids, so a re-export gives the same partition.
          {paged ? ` This run is stored in ${run.generation.storage?.batches} batches, so its export is prepared as files first and the data parts download gzipped.` : ""}
        </small>
      </div>
      <div className="row" style={{ alignItems: "end" }}>
        <div>
          <label htmlFor="held-out">Held-out sub-domain</label>
          <select
            id="held-out"
            value={heldOut}
            onChange={(e) => {
              setHeldOut(e.target.value);
              setCard(null);
            }}
          >
            <option value="">None: train, validation, and test only</option>
            {(run.config.sub_domains || []).map((name) => (
              <option key={name} value={name}>{name.replaceAll("_", " ")}</option>
            ))}
          </select>
        </div>
        {paged && !current?.ready ? (
          <div>
            <button className="primary" type="button" onClick={prepare} disabled={!open || Boolean(busy) || ["queued", "running"].includes(current?.job.status)}>
              {busy === "prepare" ? "Queueing" : current?.job.status === "failed" ? "Prepare again" : "Prepare export"}
            </button>
          </div>
        ) : (
          <div>
            <button className="ghost" type="button" onClick={preview} disabled={!open || Boolean(busy)}>{busy === "preview" ? "Reading" : "Preview data card"}</button>
          </div>
        )}
      </div>
      {accepted ? (
        <p className="note">The judge accepted this run in cycle {latest.cycle_index}.</p>
      ) : (
        <div className="warn">
          {latest ? "The judge did not accept this run." : "The judge has not read this run yet."} Accepted runs export as they are.
          <label className="check" style={{ marginTop: 8 }}>
            <input type="checkbox" checked={allowUnaccepted} onChange={(e) => { setAllowUnaccepted(e.target.checked); setCard(null); }} />
            Export it anyway; the manifest will say it was not accepted
          </label>
        </div>
      )}
      {error ? <div className="error">{error}</div> : null}
      {paged && current && !current.ready ? (
        <div className="export-status">
          {["queued", "running"].includes(current.job.status) ? (
            <>
              <div className="bar"><i style={{ width: `${Math.round((current.job.progress || 0) * 100)}%` }} /></div>
              <small>{current.job.message}</small>
            </>
          ) : (
            <small className={current.job.status === "failed" ? "error" : ""}>{current.job.error || current.job.message}</small>
          )}
        </div>
      ) : null}
      {paged && !current?.ready ? null : <div className="parts">
        {PARTS.map(([part, label, detail]) => (
          <button key={part} type="button" className="part" onClick={() => save(part)} disabled={!open || Boolean(busy)}>
            <b>{busy === part ? "Preparing" : label}</b>
            <code>{paged && part !== "manifest.json" ? `${part}.gz` : part}</code>
            <small>{detail}</small>
            {current?.download_sizes?.[part] != null ? (
              <small className="size">
                {bytes(current.download_sizes[part])}
                {part !== "manifest.json" && current.sizes?.[part] != null ? ` · ${bytes(current.sizes[part])} unpacked` : ""}
              </small>
            ) : null}
          </button>
        ))}
      </div>}
      {card ? (
        <div className="data-card">
          <div className="kvs">
            <span>Scope</span><b>{card.data_card.scope.sector} · {(card.data_card.scope.sub_domains || []).map((item) => item.replaceAll("_", " ")).join(", ")} · {card.data_card.scope.language}</b>
            <span>Intended use</span><b>{card.data_card.intended_use}</b>
            <span>Reference</span><b>{card.data_card.reference}</b>
            <span>Jurisdiction</span><b>{card.data_card.jurisdiction_profile}</b>
            <span>Size</span><b>{card.counts.samples} samples · {card.counts.sequences} sequences · {card.counts.events} events</b>
            <span>Split</span><b>{Object.entries(card.split.counts).filter(([, count]) => count).map(([name, count]) => `${name} ${count}`).join(" · ")}</b>
            <span>Generator</span><b>{card.generator_id} · {card.pack_version}</b>
          </div>
          <p className="lede" style={{ margin: "10px 0 4px" }}>Known limitations</p>
          <ul className="limitations">
            {card.data_card.known_limitations.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
