"use client";

import { useState } from "react";
import { apiText, download } from "../lib/api";

const PARTS = [
  ["samples.jsonl", "Samples", "One line per prompt: the group of sequences, turns, rewards, advantages, and split."],
  ["domain.jsonl", "Domain records", "Objects, relationships, events, event-object links, state changes, and trajectories."],
  ["ocel.json", "OCEL 2.0", "The domain layer for process-mining tools such as PM4Py."],
  ["manifest.json", "Manifest", "Configuration, counts, split, judge cycles, quality, data card, and file checksums."],
];

export default function DownloadPanel({ run }) {
  const [heldOut, setHeldOut] = useState("");
  const [card, setCard] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const query = heldOut ? `?held_out=${encodeURIComponent(heldOut)}` : "";
  const prefix = `run-${run.id.slice(0, 8)}${heldOut ? `-heldout-${heldOut}` : ""}`;

  async function save(part) {
    setError("");
    setBusy(part);
    try {
      await download(`/runs/${run.id}/export/${part}${query}`, `${prefix}-${part}`);
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
        <small>Every record is synthetic. The split is fixed by the run and sample ids, so a re-export gives the same partition.</small>
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
        <div>
          <button className="ghost" type="button" onClick={preview} disabled={Boolean(busy)}>{busy === "preview" ? "Reading" : "Preview data card"}</button>
        </div>
      </div>
      {error ? <div className="error">{error}</div> : null}
      <div className="parts">
        {PARTS.map(([part, label, detail]) => (
          <button key={part} type="button" className="part" onClick={() => save(part)} disabled={Boolean(busy)}>
            <b>{busy === part ? "Preparing" : label}</b>
            <code>{part}</code>
            <small>{detail}</small>
          </button>
        ))}
      </div>
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
