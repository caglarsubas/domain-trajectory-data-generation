"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Shell from "../../components/Shell";
import { api } from "../../lib/api";

export default function Studio() {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/runs").then((data) => setRuns(data.data)).catch((err) => setError(err.message));
  }, []);

  return (
    <Shell>
      <p className="lede">Banking studies, newest last. Open one to inspect the journey.</p>
      <h1 className="word" style={{ fontSize: 56, margin: "0 0 8px" }}>The studio</h1>
      {error ? <div className="error">{error}</div> : null}
      {runs && runs.length === 0 ? (
        <div className="note">
          No runs yet. A warm start — papers, a deep-search note, a repo, or a data source — gives the judge a stronger reference.
          <div className="actions">
            <Link className="primary" href="/studio/compose" style={{ display: "inline-block" }}>Compose a run</Link>
          </div>
        </div>
      ) : null}
      <ol className="timeline">
        {(runs || []).map((run, index) => (
          <li key={run.id}>
            <Link href={`/studio/runs/${run.id}`}>
              <p style={{ margin: 0 }}>{new Date(run.created_at).toLocaleString()} · {run.status}</p>
              <h2 className="word" style={{ fontSize: index === runs.length - 1 ? 42 : 28 }}>
                {(run.config.sub_domains || []).join(" · ")}
              </h2>
              <p>
                {run.config.language} · {run.generation && run.generation.primary_trajectories !== run.config.target_trajectory_count
                  ? `${run.generation.primary_trajectories} of ${run.config.target_trajectory_count} trajectories`
                  : `${run.config.target_trajectory_count} trajectories`} · {run.config.min_events}–{run.config.max_events} events
                {run.parent_run_id ? " · continues a previous run" : ""}
              </p>
              {run.headline_score != null ? <span className="score-pill">Judge {run.headline_score}</span> : null}
            </Link>
          </li>
        ))}
      </ol>
    </Shell>
  );
}
