"use client";

const RUBRICS = ["helpfulness", "correctness", "safety", "pairwise_quality"];
// Pairwise compares a journey with its own alternative; it is reported and does not decide acceptance.
const REPORTED = new Set(["pairwise_quality"]);
const COMPARED = ["process_conformance", "decision_score"];
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
    case "repeats_disagree":
      return `${flag.model}'s repeated verdicts on ${label(flag.rubric)} fell on both sides of its threshold.`;
    case "code_disagrees":
      return `${flag.model} and the code scorer disagree on ${label(flag.rubric)}.`;
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
          {REPORTED.has(row.rubric) ? <small className="second">Reported only; does not decide acceptance.</small> : null}
        </div>
      ))}
    </div>
  );
}

function stability(item) {
  return item?.calls ? `${item.stable} of ${item.calls} stable` : "—";
}

// One row per call: its repeats' scores side by side, and the first reason given.
function calls(verdicts) {
  const rows = [];
  const byKey = {};
  for (const item of verdicts) {
    const key = `${item.judge_model}|${item.rubric}|${item.order || ""}`;
    if (!byKey[key]) {
      byKey[key] = { ...item, repeats: [] };
      rows.push(byKey[key]);
    }
    byKey[key].repeats.push(item);
  }
  return rows;
}

function repeatScores(row) {
  return row.repeats.map((item) => (!item.readable ? "unreadable" : item.order ? winner(item) : shown(item.score))).join(" · ");
}

export default function JudgePanel({ cycle, onPick }) {
  if (!cycle || !cycle.models?.length || !cycle.sample?.length) return null;
  const [primary, second] = cycle.models;
  const agreement = cycle.agreement?.by_rubric || {};
  const order = cycle.agreement?.order_consistency || {};
  const repeats = cycle.agreement?.repeats || {};
  const code = cycle.agreement?.code || {};
  const judging = cycle.judging;
  const repeated = Object.keys(repeats).length > 0;
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
          {judging?.repeats > 1 ? ` Each rubric was asked ${judging.repeats} times per model at temperature ${judging.temperature}.` : ""}
        </small>
      </div>
      {(judging?.notes || []).map((note) => (
        <p className="lede" key={note}>{note}</p>
      ))}
      {cycle.reference?.length ? (
        <p className="lede">
          The brief carried {cycle.reference.length} warm-start {cycle.reference.length === 1 ? "passage" : "passages"} chosen for this study, from{" "}
          {[...new Set(cycle.reference.map((item) => item.source))].join(", ")}
          {cycle.reference.every((item) => !item.score) ? "; none matched the study's terms, so each document's opening was used" : ""}.
        </p>
      ) : null}
      <table className="target-table">
        <thead>
          <tr>
            <th>Rubric</th>
            <th>{primary}</th>
            {second ? <th>{second}</th> : null}
            {second ? <th>Across models</th> : null}
            {repeated ? <th>Across repeats</th> : null}
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
              {repeated ? (
                <td>
                  <div>{stability(repeats[rubric]?.[primary])}</div>
                  {second ? <small className="second">{second}: {stability(repeats[rubric]?.[second])}</small> : null}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
      {Object.keys(code).length ? (
        <>
          <p className="lede">
            The judge also scored what code already scores. The code's verdict stays the signal; this measures the judge and does not
            decide acceptance.
          </p>
          <table className="target-table">
            <thead>
              <tr>
                <th>Judge against code</th>
                <th>Code</th>
                <th>{primary}</th>
                {second ? <th>{second}</th> : null}
                {repeated ? <th>Across repeats</th> : null}
              </tr>
            </thead>
            <tbody>
              {COMPARED.filter((rubric) => code[rubric]).map((rubric) => {
                const found = code[rubric];
                const judge = (model) => {
                  const item = found.models?.[model];
                  return item?.journeys ? `${shown(item.mean)}; agrees on ${item.agree} of ${item.journeys}` : "—";
                };
                return (
                  <tr key={rubric}>
                    <td>{label(rubric)}</td>
                    <td>{found.code.passed} of {found.code.journeys} pass; mean {shown(found.code.mean)}</td>
                    <td>{judge(primary)}</td>
                    {second ? <td>{judge(second)}</td> : null}
                    {repeated ? <td>{stability(repeats[rubric]?.[primary])}</td> : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      ) : null}
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
                  <th>{repeated ? "Scores by repeat" : "Score"}</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {calls(verdictsFor(entry.trajectory_id)).map((row, index) => (
                  <tr key={index}>
                    <td>{row.judge_model}</td>
                    <td>{label(row.rubric)}{row.order ? ` (${row.order === "ab" ? "this journey first" : "alternative first"})` : ""}</td>
                    <td>{repeatScores(row)}</td>
                    <td>{row.repeats.map((item) => item.parsed?.justification || item.parsed?.reason || "").find(Boolean) || ""}</td>
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
