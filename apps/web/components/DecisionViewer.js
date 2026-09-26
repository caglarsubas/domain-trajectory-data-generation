"use client";

import { useState } from "react";

function share(value) {
  return `${Math.round(value * 100)}%`;
}

// The journey's outcome decisions as a decision-scoring model sees them: what was known, what was open, and the targets.
export default function DecisionViewer({ decisions }) {
  const [open, setOpen] = useState(0);
  if (!decisions?.length) return null;
  const point = decisions[open] || decisions[0];
  return (
    <div className="episode decision">
      <div className="panel-head">
        <h3>Decisions</h3>
        <small>
          Each outcome decision in this journey. The policy share is the generator&apos;s own choice rule; the value is the share of {point.value_samples} simulated
          continuations under it that reach the goal. Neither is an observed rate, and no counterfactual here is causal.
        </small>
      </div>
      <div className="episode-rollouts">
        {decisions.map((item, index) => (
          <button key={item.decision_id} type="button" className="episode-rollout" data-on={index === open} onClick={() => setOpen(index)}>
            <b>{item.group.replaceAll("_", " ")}</b>
            <small>after {item.decision_index} steps</small>
          </button>
        ))}
      </div>
      <div className="episode-grid">
        <div>
          <h4>State</h4>
          <ul className="episode-state">
            {Object.entries(point.state).map(([key, value]) => (
              <li key={key}><code>{key}</code> {value}</li>
            ))}
          </ul>
          <h4>Facts</h4>
          <ul className="episode-state">
            {Object.entries(point.facts).map(([key, value]) => (
              <li key={key}><code>{key}</code> {String(value)}</li>
            ))}
          </ul>
        </div>
        <div>
          <h4>Options</h4>
          <table className="target-table decision-options">
            <thead>
              <tr><th>Outcome</th><th>Policy share</th><th>Reaches the goal</th></tr>
            </thead>
            <tbody>
              {point.options.map((option) => (
                <tr key={option.option_id} data-taken={option.option_id === point.taken}>
                  <td>
                    {option.label}
                    {option.option_id === point.taken ? <small> · taken</small> : null}
                    <small className="decision-requires">{option.requires.join(" and ") || "no precondition"}</small>
                  </td>
                  <td><span className="decision-bar"><i style={{ width: share(option.probability) }} /></span>{share(option.probability)}</td>
                  <td><span className="decision-bar"><i style={{ width: share(option.value) }} /></span>{share(option.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {point.distractor ? (
            <p className="episode-checks">
              True or false: “{point.distractor.label}” is not allowed here, because {point.distractor.unmet}.
            </p>
          ) : null}
          <p className="episode-checks">
            The journey went on for {point.outcome.steps_after} more {point.outcome.steps_after === 1 ? "step" : "steps"} to {point.outcome.final_event} and
            {point.outcome.journey_success ? " reached" : " did not reach"} the goal. {point.goal}
          </p>
        </div>
      </div>
    </div>
  );
}
