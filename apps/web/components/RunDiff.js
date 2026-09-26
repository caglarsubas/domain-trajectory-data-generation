"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

function shown(value) {
  if (value == null) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(value < 1 ? 3 : 1);
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function delta(before, after) {
  if (typeof before !== "number" || typeof after !== "number" || before === after) return "";
  const change = after - before;
  return `${change > 0 ? "+" : ""}${Number.isInteger(change) ? change.toLocaleString() : change.toFixed(Math.abs(change) < 1 ? 3 : 1)}`;
}

// What changed from the previous run: the notes and what they did, the configuration, the data, and the scores.
export default function RunDiff({ run }) {
  const [diff, setDiff] = useState(null);
  const [error, setError] = useState("");
  const ready = ["generated", "evaluated"].includes(run.status);
  const cycles = run.cycles.length;

  useEffect(() => {
    if (!ready) return;
    api(`/runs/${run.id}/diff`).then(setDiff).catch((err) => setError(err.message));
  }, [run.id, ready, cycles]);

  if (!ready) return null;
  if (error) return <div className="error">{error}</div>;
  if (!diff) return null;
  const notes = [...(diff.notes.revisions || []).map((item) => ({ text: item.note, effect: item.effect, kind: "Judge" })),
    ...(diff.notes.feedback || []).map((item) => ({ text: `${item.stance} ${item.target_type}${item.comment ? `: ${item.comment}` : ""}`, effect: item.effect, kind: "You" }))];
  const peak = Math.max(1, ...diff.event_shifts.flatMap((item) => [item.before, item.after]));
  const { before, after } = diff.scores;
  return (
    <details className="run-diff" open>
      <summary>
        <h3>What changed</h3>
        <small>
          From <Link href={`/studio/runs/${diff.parent_run_id}`}>the previous run</Link>
          {diff.regeneration ? `, regenerated from the judge's notes as round ${diff.regeneration.round}` : ""}.
        </small>
      </summary>
      {before || after ? (
        <p className="lede">
          Judge: {before ? `${before.headline ?? "—"} ${before.accepted ? "accepted" : "not accepted"}` : "not judged"} →{" "}
          {after ? `${after.headline ?? "—"} ${after.accepted ? "accepted" : "not accepted"}` : "not judged yet"}.
        </p>
      ) : null}
      {notes.length ? (
        <>
          <h4>Notes applied</h4>
          <ul className="diff-notes">
            {notes.map((item, index) => (
              <li key={index}>
                <span className="who">{item.kind}</span> {item.text}
                <small>{item.effect}</small>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="lede">No notes were carried into this run.</p>
      )}
      {diff.config.length ? (
        <>
          <h4>Configuration</h4>
          <table className="target-table">
            <tbody>
              {diff.config.map((item) => (
                <tr key={item.key}>
                  <td>{item.key.replaceAll("_", " ")}</td>
                  <td>{shown(item.before)}</td>
                  <td>{shown(item.after)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
      <h4>Data</h4>
      <table className="target-table">
        <thead>
          <tr>
            <th>Measure</th>
            <th>Before</th>
            <th>After</th>
            <th>Change</th>
          </tr>
        </thead>
        <tbody>
          {diff.metrics.map((item) => (
            <tr key={item.name}>
              <td>{item.name}</td>
              <td>{shown(item.before)}</td>
              <td>{shown(item.after)}</td>
              <td>{delta(item.before, item.after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {diff.event_shifts.length ? (
        <>
          <h4>Events per 100 journeys</h4>
          <div className="shifts">
            {diff.event_shifts.map((item) => (
              <div className="shift" key={item.type}>
                <span>{item.type}</span>
                <div className="pair">
                  <i className="before" style={{ width: `${(item.before / peak) * 100}%` }} title={`before ${item.before}`} />
                  <i className="after" style={{ width: `${(item.after / peak) * 100}%` }} title={`after ${item.after}`} />
                </div>
                <small>{item.before} → {item.after}</small>
              </div>
            ))}
          </div>
        </>
      ) : null}
      {diff.variants.appeared.length || diff.variants.vanished.length ? (
        <div className="variant-changes">
          {[["New paths", diff.variants.appeared], ["Paths no longer drawn", diff.variants.vanished]].map(([title, items]) =>
            items.length ? (
              <div key={title}>
                <h4>{title}</h4>
                <ul>
                  {items.map((item) => (
                    <li key={item.id}>
                      <b>{item.kind}</b> · {item.count} · <small>{item.types.join(" → ")}</small>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null
          )}
        </div>
      ) : null}
    </details>
  );
}
