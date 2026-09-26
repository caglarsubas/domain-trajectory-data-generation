"use client";

import { useState } from "react";

const POLICY = {
  reference: "The step the journey took",
  alternative: "A legal alternative",
  "perturbed:illegal": "An operation the state forbids",
  "perturbed:wrong_object": "Another case's object",
};

function shown(value) {
  return typeof value === "number" ? (Number.isInteger(value) ? String(value) : value.toFixed(2)) : "—";
}

// A decision of this journey as an agent task: the mock bank's state and operations, the task, and scored rollouts.
export default function EpisodeViewer({ episode }) {
  const [open, setOpen] = useState(0);
  if (!episode) return null;
  const rollout = episode.rollouts[open] || episode.rollouts[0];
  const call = rollout.turns.find((turn) => turn.tool_call)?.tool_call;
  const result = rollout.turns.find((turn) => turn.tool_result)?.tool_result;
  const final = rollout.turns.at(-1)?.text;
  return (
    <div className="episode">
      <div className="panel-head">
        <h3>Episode</h3>
        <small>After {episode.decision_index} steps, the agent takes the next one against a mock bank. Rubric items are checked by code; reward is verification times the rubric score.</small>
      </div>
      <p className="episode-task">{episode.task}</p>
      <div className="episode-grid">
        <div>
          <h4>State</h4>
          <ul className="episode-state">
            {Object.entries(episode.state).map(([key, value]) => (
              <li key={key}><code>{key}</code> {value}</li>
            ))}
          </ul>
          <h4>Operations</h4>
          <ul className="episode-tools">
            {episode.tools.map((tool) => (
              <li key={tool.name} data-legal={episode.skeleton.legal_tools.includes(tool.name)}>
                <code>{tool.name}</code>({Object.keys(tool.parameters.properties).join(", ")})
                {tool.http ? <small> · {tool.http.method} {tool.http.path}</small> : null}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h4>Rollouts</h4>
          <div className="episode-rollouts">
            {episode.rollouts.map((item, index) => (
              <button key={item.rollout_id} type="button" className="episode-rollout" data-on={index === open} data-outcome={item.legal && item.outcome === "pass" ? "pass" : "fail"} onClick={() => setOpen(index)}>
                <b>{POLICY[item.policy] || item.policy}</b>
                <small>reward {shown(item.reward)} · advantage {shown(item.advantage)}</small>
              </button>
            ))}
          </div>
          <div className="episode-call">
            <code>{call?.name}({JSON.stringify(call?.arguments || {})})</code>
            <pre>{JSON.stringify(result, null, 1)}</pre>
            <p>{final}</p>
            <table className="target-table">
              <tbody>
                {episode.rubric.map((item) => (
                  <tr key={item.item_id}>
                    <td>{item.text}</td>
                    <td>{rollout.rubric_scores[item.item_id] ? "yes" : "no"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
