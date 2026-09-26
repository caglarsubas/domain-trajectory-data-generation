"use client";

const RUBRICS = ["helpfulness", "correctness", "safety", "pairwise_quality"];
const SCALE = { helpfulness: 5 };

function label(name) {
  return name.replaceAll("_", " ");
}

function shown(value) {
  if (value == null) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

// A pairwise verdict scores 1 when the first response shown wins; name the winner instead.
function winner(item) {
  if (item.score === 0.5) return "tie";
  const firstWon = item.score >= 0.5;
  return (item.order === "ab") === firstWon ? "this journey" : "alternative";
}

function explain(flag) {
  switch (flag.kind) {
    case "likely_false_positive":
      return `${flag.model} passed the control journey, whose events were put out of order. Its correctness verdicts may not see broken journeys.`;
    case "likely_false_negative":
      return `${flag.model} called a journey incorrect that replays legally through the pack's machines.`;
    case "models_disagree":
      return `The two judges disagree on ${label(flag.rubric)}.`;
    case "order_flip":
      return `${flag.model} preferred whichever journey it read first.`;
    case "unreadable":
      return `${flag.model} returned no readable verdict for ${label(flag.rubric)}.`;
    default:
      return flag.kind;
  }
}

// Score meters for a cycle: the primary judge's score per rubric, with the second opinion beneath.
export function ScoreMeters({ cycle }) {
  const scores = cycle?.scores || {};
  const models = cycle?.models || [];
  const legacy = cycle && !models.length;
  const rows = RUBRICS.map((rubric) => {
    if (legacy) {
      const verdict = (cycle.verdicts || []).find((item) => item.rubric === rubric);
      return { rubric, primary: verdict?.score ?? null, second: null };
    }
    return { rubric, primary: scores[rubric]?.[models[0]] ?? null, second: models[1] ? scores[rubric]?.[models[1]] ?? null : null };
  });
  return (
    <div className="scores">
      {rows.map((row) => (
        <div className="meter" key={row.rubric}>
          <span>{label(row.rubric)}</span>
          <strong>{shown(row.primary)}</strong>
          <div className="bar"><i style={{ width: `${row.primary == null ? 0 : Math.min(100, (row.primary / (SCALE[row.rubric] || 1)) * 100)}%` }} /></div>
          {models[1] ? <small className="second">{models[1]}: {shown(row.second)}</small> : null}
        </div>
      ))}
    </div>
  );
}

export default function JudgePanel({ cycle, onPick }) {
  if (!cycle || !cycle.models?.length || !cycle.sample?.length) return null;
  const [primary, second] = cycle.models;
  const agreement = cycle.agreement?.by_rubric || {};
  const order = cycle.agreement?.order_consistency || {};
  const grouped = {};
  for (const flag of cycle.flags || []) {
    const key = `${flag.kind}|${flag.model}|${flag.rubric}`;
    grouped[key] = grouped[key] || { ...flag, journeys: new Set() };
    grouped[key].journeys.add(flag.trajectory_id);
  }
  const flags = Object.values(grouped);
  const verdictsFor = (trajectoryId) => cycle.verdicts.filter((item) => item.trajectory_id === trajectoryId && !item.canary);
  return (
    <div className="judge-panel">
      <div className="panel-head">
        <h3>Judge</h3>
        <small>
          {cycle.sample.length} journeys judged by {primary}{second ? ` and ${second}` : ""}
          {cycle.sample.some((entry) => entry.truncated) ? "; some were shortened to fit the prompt budget" : ""}.
        </small>
      </div>
      <table className="target-table">
        <thead>
          <tr>
            <th>Rubric</th>
            <th>{primary}</th>
            {second ? <th>{second}</th> : null}
            {second ? <th>Agreement</th> : null}
          </tr>
        </thead>
        <tbody>
          {RUBRICS.filter((rubric) => cycle.scores?.[rubric]).map((rubric) => (
            <tr key={rubric}>
              <td>{label(rubric)}</td>
              <td>{shown(cycle.scores[rubric][primary])}</td>
              {second ? <td>{shown(cycle.scores[rubric][second])}</td> : null}
              {second ? (
                <td>{agreement[rubric]?.journeys ? `${agreement[rubric].agree} of ${agreement[rubric].journeys} journeys` : "—"}</td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
      {Object.values(order).some((item) => item.journeys) ? (
        <p className="lede">
          Pairwise, asked in both orders:{" "}
          {Object.entries(order)
            .filter(([, item]) => item.journeys)
            .map(([model, item]) => `${model} kept its choice in ${item.consistent} of ${item.journeys}`)
            .join("; ")}
          .
        </p>
      ) : null}
      {cycle.canary ? (
        <p className="lede">
          Control journey (events put out of order, which the pack's replay rejects):{" "}
          {Object.entries(cycle.canary.results)
            .map(([model, score]) => `${model} ${score == null ? "gave no verdict" : score >= 0.5 ? "passed it" : "caught it"}`)
            .join("; ")}
          .
        </p>
      ) : null}
      {flags.length ? (
        <ul className="judge-flags">
          {flags.map((flag) => (
            <li key={`${flag.kind}|${flag.model}|${flag.rubric}`} data-kind={flag.kind}>
              {explain(flag)}
              {flag.kind !== "likely_false_positive" ? ` ${flag.journeys.size} ${flag.journeys.size === 1 ? "journey" : "journeys"}.` : ""}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="judge-journeys">
        {cycle.sample.map((entry) => (
          <details key={entry.trajectory_id}>
            <summary>
              <span>{entry.trajectory_type}</span>
              <small>
                {entry.events} events{entry.outcome ? ` · ${entry.outcome === "pass" ? "reached its goal" : "missed its goal"}` : ""}
                {entry.truncated ? " · shortened" : ""}
              </small>
              <button className="ghost" type="button" onClick={(event) => { event.preventDefault(); onPick?.(entry.trajectory_id); }}>
                Open
              </button>
            </summary>
            <table className="target-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Rubric</th>
                  <th>Score</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {verdictsFor(entry.trajectory_id).map((item, index) => (
                  <tr key={index}>
                    <td>{item.judge_model}</td>
                    <td>{label(item.rubric)}{item.order ? ` (${item.order === "ab" ? "this journey first" : "alternative first"})` : ""}</td>
                    <td>{!item.readable ? "unreadable" : item.order ? winner(item) : shown(item.score)}</td>
                    <td>{item.parsed?.justification || item.parsed?.reason || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        ))}
      </div>
    </div>
  );
}
