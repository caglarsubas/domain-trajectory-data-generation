"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

function Evidence({ items }) {
  return (
    <ul className="fact-evidence">
      {items.map((item, index) => (
        <li key={index}>
          <b>{item.document}</b> “{item.sentence}”
        </li>
      ))}
    </ul>
  );
}

// What a study's documents state, what they only imply, and what runs take from defaults.
export default function FactsPanel({ projectId, subDomains, jurisdiction, language, refreshKey }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    if (!projectId) return;
    const params = new URLSearchParams({ sub_domains: subDomains.join(","), jurisdiction, language: (language || "en").split("-")[0] });
    try {
      setReport(await api(`/projects/${projectId}/facts?${params}`));
    } catch (err) {
      setError(err.message);
    }
  }, [projectId, subDomains, jurisdiction, language]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  async function review(key, decision) {
    setBusy(key);
    setError("");
    try {
      await api(`/projects/${projectId}/facts/review`, { method: "POST", body: JSON.stringify({ key, decision }) });
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  if (!projectId || !report) return null;
  const { counts } = report;
  return (
    <div className="facts-panel">
      <div className="panel-head">
        <h3>What the documents say</h3>
        <small>
          {counts.steering} {counts.steering === 1 ? "fact steers" : "facts steer"} the next run
          {counts.awaiting_review ? `; ${counts.awaiting_review} ${counts.awaiting_review === 1 ? "waits" : "wait"} for your review` : ""}.
        </small>
      </div>
      {error ? <div className="error">{error}</div> : null}
      {report.implied.length ? (
        <>
          <h4>Needs your review</h4>
          <p className="lede">The text points at these but does not state them. Accepted ones steer the next run; the rest do not.</p>
          {report.implied.map((fact) => (
            <div className="fact" key={fact.key} data-review={fact.review || "open"}>
              <div className="fact-head">
                <span>{fact.statement}</span>
                <small>{Math.round(fact.confidence * 100)}% · {fact.mentions} {fact.mentions === 1 ? "mention" : "mentions"}</small>
                <span className="fact-actions">
                  <button className={fact.review === "accepted" ? "primary" : "ghost"} type="button" disabled={busy === fact.key} onClick={() => review(fact.key, fact.review === "accepted" ? "clear" : "accepted")}>
                    {fact.review === "accepted" ? "Accepted" : "Accept"}
                  </button>
                  <button className="ghost" type="button" disabled={busy === fact.key} onClick={() => review(fact.key, fact.review === "rejected" ? "clear" : "rejected")}>
                    {fact.review === "rejected" ? "Rejected" : "Reject"}
                  </button>
                </span>
              </div>
              <Evidence items={fact.evidence} />
            </div>
          ))}
        </>
      ) : null}
      {report.explicit.length ? (
        <>
          <h4>Stated</h4>
          {report.explicit.map((fact) => (
            <div className="fact" key={fact.key} data-review={fact.review || "open"}>
              <div className="fact-head">
                <span>{fact.statement}</span>
                <small>{fact.steers ? "steers" : "rejected"} · {fact.mentions} {fact.mentions === 1 ? "mention" : "mentions"}</small>
                <span className="fact-actions">
                  <button className="ghost" type="button" disabled={busy === fact.key} onClick={() => review(fact.key, fact.review === "rejected" ? "clear" : "rejected")}>
                    {fact.review === "rejected" ? "Use it again" : "Don't use"}
                  </button>
                </span>
              </div>
              <Evidence items={fact.evidence} />
            </div>
          ))}
        </>
      ) : null}
      {report.unsupported.length ? (
        <>
          <h4>Taken from defaults</h4>
          <ul className="fact-defaults">
            {report.unsupported.map((item, index) => <li key={index}>{item.statement}</li>)}
          </ul>
        </>
      ) : null}
    </div>
  );
}
