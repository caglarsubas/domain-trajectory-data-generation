"use client";

const LABELS = {
  outcome: "Outcome",
  solution_rubric: "Solution rubric",
  behavior_rubric: "Behavior rubric",
  process_conformance: "Process conformance",
  decision_score: "Decision score",
};

function percent(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

// Every signal's verdict on the run's sequences; the run's own signal decides pass or fail and so the rewards.
export default function SignalTable({ rewards }) {
  const signals = rewards?.signals;
  if (!signals) return null;
  const ks = [...new Set(Object.values(signals).flatMap((found) => Object.keys(found.pass_at_k || {})))].sort((a, b) => Number(a) - Number(b));
  return (
    <table className="target-table signal-table">
      <thead>
        <tr>
          <th>Signal</th>
          <th>Mean score</th>
          <th>Pass rate</th>
          {ks.map((k) => <th key={k} title={`At least one pass in ${k} attempts at a prompt, estimated from its group`}>pass@{k}</th>)}
        </tr>
      </thead>
      <tbody>
        {Object.keys(LABELS).filter((name) => signals[name]).map((name) => (
          <tr key={name} data-primary={name === rewards.signal}>
            <td>{LABELS[name]}{name === rewards.signal ? <small> · decides pass</small> : null}</td>
            <td>{percent(signals[name].mean)}</td>
            <td>{percent(signals[name].pass_rate)}</td>
            {ks.map((k) => <td key={k}>{percent(signals[name].pass_at_k?.[k])}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
