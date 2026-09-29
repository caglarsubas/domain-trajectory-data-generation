"use client";

const RUBRICS = ["helpfulness", "correctness", "safety", "pairwise_quality"];
// Pairwise compares a journey with its own alternative; it is reported and does not decide acceptance.
const REPORTED = new Set(["pairwise_quality"]);
const COMPARED = ["process_conformance", "decision_score"];
const SCALE = { helpfulness: 5 };

// A study rubric is named by a hash of its content; it is shown by its kind and title.
function label(name, names = {}) {
  const study = names[name];
  if (study) return `the study's ${study.kind} rubric "${study.title}"`;
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

function explain(flag, names) {
  switch (flag.kind) {
    case "likely_false_positive":
      return `${flag.model} passed the control journey, whose events were put out of order. Its correctness verdicts may not see broken journeys.`;
    case "likely_false_negative":
      return `${flag.model} called a journey incorrect that replays legally through the pack's machines.`;
    case "models_disagree":
      return `The two judges disagree on ${label(flag.rubric, names)}.`;
    case "order_flip":
      return `${flag.model} preferred whichever journey it read first.`;
    case "unreadable":
      return `${flag.model} returned no readable verdict for ${label(flag.rubric, names)}.`;
    case "repeats_disagree":
      return `${flag.model}'s repeated verdicts on ${label(flag.rubric, names)} fell on both sides of its threshold.`;
    case "blind_to_defect":
      return `${flag.model} scored journeys with a known defect no lower than their originals on ${label(flag.rubric, names)}, so its scores there cannot tell a flawed journey from a sound one.`;
    case "code_disagrees":
      return `${flag.model} and the code scorer disagree on ${label(flag.rubric, names)}.`;
    case "did_not_decide":
      return `${label(flag.rubric, names)} did not decide acceptance: ${flag.model} could not tell its controls from their originals.`;
    case "same_model":
      return `${flag.model} were served by one model, so their agreement is one model agreeing with itself.`;
    case "unfaithful_turn":
      return `${flag.model} found written turns that change or add to the facts they had to state.`;
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

const CONTROLS = { missing_step: "a step removed", worse_choice: "a worse choice", slow_wait: "a wait stretched", reversed: "its events reversed" };

function controlLabel(control) {
  const kind = (control || "").replace(/^pairwise:/, "");
  return CONTROLS[kind] || kind.replaceAll("_", " ");
}

// One row per call: its repeats' scores side by side, and the first reason given.
function calls(verdicts) {
  const rows = [];
  const byKey = {};
  for (const item of verdicts) {
    const key = `${item.judge_model}|${item.rubric}|${item.order || ""}|${item.control || ""}`;
    if (!byKey[key]) {
      byKey[key] = { ...item, repeats: [] };
      rows.push(byKey[key]);
    }
    byKey[key].repeats.push(item);
  }
  return rows;
}

// In the pairwise control the original is A when asked first; name what was picked.
function controlWinner(item) {
  if (item.score === 0.5) return "tie";
  return (item.order === "ab") === (item.score >= 0.5) ? "original" : "flawed copy";
}

function repeatScores(row) {
  const pairwiseControl = (row.control || "").startsWith("pairwise:");
  return row.repeats
    .map((item) => (!item.readable ? "unreadable" : pairwiseControl ? controlWinner(item) : item.order ? winner(item) : shown(item.score)))
    .join(" · ");
}

function discriminationCell(row) {
  if (!row) return "—";
  return `${row.lower} of ${row.controls} scored lower${row.blind ? " · blind" : ""}`;
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
  const discrimination = cycle.agreement?.discrimination || {};
  const pairwiseControl = cycle.agreement?.pairwise_control || {};
  const controlled = Object.keys(discrimination).length > 0 || Object.keys(pairwiseControl).length > 0;
  const faithfulness = cycle.agreement?.faithfulness;
  const deciding = cycle.agreement?.deciding;
  const sameModel = cycle.agreement?.same_model || [];
  const reverted = (faithfulness?.rows || []).filter((row) => row.revert);
  const names = Object.fromEntries(Object.entries(judging?.rubrics || {}).filter(([, info]) => info.source === "study"));
  const againstCode = [...COMPARED.filter((rubric) => code[rubric]), ...Object.keys(code).filter((rubric) => code[rubric].study)];
  const grouped = {};
  for (const flag of cycle.flags || []) {
    const key = `${flag.kind}|${flag.model}|${flag.rubric}`;
    grouped[key] = grouped[key] || { ...flag, journeys: new Set() };
    grouped[key].journeys.add(flag.trajectory_id);
  }
  const flags = Object.values(grouped);
  const verdictsFor = (trajectoryId) =>
    cycle.verdicts.filter((item) => item.trajectory_id === trajectoryId && !item.canary && item.rubric !== "turn_faithfulness");
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
      {deciding?.rubrics && Object.keys(deciding.rubrics).length ? (
        <p className="lede">
          {deciding.code_only
            ? `Neither helpfulness nor correctness decided acceptance: ${deciding.judge} could not tell their controls from the originals, so the run stands on the code's checks and safety alone.`
            : `Acceptance followed ${Object.entries(deciding.rubrics).filter(([, row]) => row.decides).map(([rubric]) => label(rubric, names)).join(" and ")} and safety, because ${deciding.judge} scored their controls lower than the originals.`}{" "}
          {Object.entries(deciding.rubrics).map(([rubric, row]) => `${label(rubric, names)}: ${row.why}.`).join(" ")}
        </p>
      ) : null}
      {sameModel.length ? (
        <p className="lede">
          Both judges were served by {sameModel.join(" and ")}, so agreement across models here is one model agreeing with itself.
        </p>
      ) : null}
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
              {againstCode.map((rubric) => {
                const found = code[rubric];
                const judge = (model) => {
                  const item = found.models?.[model];
                  return item?.journeys ? `${shown(item.mean)}; agrees on ${item.agree} of ${item.journeys}` : "—";
                };
                return (
                  <tr key={rubric}>
                    <td>{found.study ? `Study ${found.kind}: ${found.title}` : label(rubric)}</td>
                    <td>
                      <div>{found.code.passed} of {found.code.journeys} pass; mean {shown(found.code.mean)}</div>
                      {found.study ? <small className="second">the code&apos;s {found.kind} rubric</small> : null}
                    </td>
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
      {controlled ? (
        <>
          <p className="lede">
            Controls are copies of sampled journeys with one known defect, each confirmed by the pack: a step removed, a worse choice at a
            decision, or a wait stretched far past its range. A judge that can tell journeys apart scores them lower than their originals; one
            that does not is blind there, whatever its agreement says. Controls decide nothing.
          </p>
          <table className="target-table">
            <thead>
              <tr>
                <th>Can the judges tell?</th>
                <th>{primary}</th>
                {second ? <th>{second}</th> : null}
              </tr>
            </thead>
            <tbody>
              {Object.keys(discrimination).map((rubric) => (
                <tr key={rubric}>
                  <td>{label(rubric, names)}</td>
                  <td>{discriminationCell(discrimination[rubric][primary])}</td>
                  {second ? <td>{discriminationCell(discrimination[rubric][second])}</td> : null}
                </tr>
              ))}
              {Object.keys(pairwiseControl).length ? (
                <tr>
                  <td>pairwise: original against its flawed copy</td>
                  {[primary, second].filter(Boolean).map((model) => {
                    const row = pairwiseControl[model];
                    return <td key={model}>{row ? `picked the original ${Math.round(row.accuracy * 100)}%${row.blind ? " · blind" : ""}` : "—"}</td>;
                  })}
                </tr>
              ) : null}
            </tbody>
          </table>
        </>
      ) : null}
      {faithfulness?.turns ? (
        <>
          <p className="lede">
            The judges read {faithfulness.turns} provider-written {faithfulness.turns === 1 ? "turn" : "turns"} against the facts each had to
            state: its events in order and the template it replaced. A turn that every readable repeat calls unfaithful goes back to its
            template; {reverted.length ? `${reverted.length} did` : "none did"}.
          </p>
          <table className="target-table">
            <thead>
              <tr>
                <th>Written turns</th>
                <th>{primary}</th>
                {second ? <th>{second}</th> : null}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>turn faithfulness</td>
                {[primary, second].filter(Boolean).map((model) => {
                  const row = faithfulness.by_model?.[model];
                  return <td key={model}>{row?.turns ? `${row.unfaithful} of ${row.turns} unfaithful` : "—"}</td>;
                })}
              </tr>
            </tbody>
          </table>
          {reverted.length ? (
            <ul className="judge-controls">
              {reverted.map((row) => (
                <li key={row.segment_id}>
                  <strong>Back to its template:</strong> &ldquo;{row.text}&rdquo; now reads &ldquo;{row.template}&rdquo;.
                  {row.reason ? ` ${row.reason}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
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
          {cycle.canary.journeys > 1 ? `The ${cycle.canary.journeys} journeys with their events reversed` : "The control journey with its events reversed"}, which
          the pack's replay rejects, on correctness:{" "}
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
              {explain(flag, names)}
              {!["likely_false_positive", "blind_to_defect", "unfaithful_turn", "did_not_decide", "same_model"].includes(flag.kind) ? ` ${flag.journeys.size} ${flag.journeys.size === 1 ? "journey" : "journeys"}.` : ""}
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
                    <td>
                      {label(row.rubric, names)}
                      {row.control
                        ? ` (control: ${controlLabel(row.control)}${row.order ? `, ${row.order === "ab" ? "original first" : "flawed copy first"}` : ""})`
                        : row.order ? ` (${row.order === "ab" ? "this journey first" : "alternative first"})` : ""}
                    </td>
                    <td>{repeatScores(row)}</td>
                    <td>{row.repeats.map((item) => item.parsed?.justification || item.parsed?.reason || "").find(Boolean) || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {entry.controls?.length ? (
              <ul className="judge-controls">
                {entry.controls.map((item) => (
                  <li key={item.kind}>
                    {item.kind.startsWith("pairwise:") ? "Pairwise control" : "Control"} with {controlLabel(item.kind)}: {item.detail}.
                  </li>
                ))}
              </ul>
            ) : null}
          </details>
        ))}
      </div>
    </div>
  );
}
