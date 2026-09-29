"use client";

const MARK = { true: "Ready", false: "Failed", null: "Not checked" };

// Whether the judge can run, checked before a cycle is queued: the engine, its rubric registry, and the judge models
// (decision 24). The study's last cycle says whether one model answered for both judges, which the engine does not.
export default function JudgeCheck({ status, checking, onCheck }) {
  if (!status) return null;
  const last = status.last_cycle;
  const oneModel = last?.same_judges && last.same_model?.length ? last : null;
  return (
    <div className="judge-check" data-ready={String(status.ready)}>
      <div className="judge-check-head">
        <strong>{status.ready ? "The judge can run." : "The judge cannot run."}</strong>
        <button className="ghost" type="button" onClick={onCheck} disabled={checking}>
          {checking ? "Checking" : "Check again"}
        </button>
      </div>
      <ul>
        {status.checks.map((item) => (
          <li key={item.name} data-ok={String(item.ok)}>
            <span>{MARK[item.ok]}</span> {item.label}: {item.detail}
          </li>
        ))}
      </ul>
      {status.address ? <small>Checked {status.address}, INFERENCE_ENGINE_BASE_URL, at {new Date(status.checked_at).toLocaleTimeString()}.</small> : null}
      {oneModel ? (
        <p className="warn">
          In this study&apos;s last cycle, {oneModel.same_model.join(" and ")} answered for both {oneModel.models.join(" and ")},
          so their agreement was one model agreeing with itself. The engine chooses which model answers; unless that has
          changed, the next cycle will be the same. A second judge the engine does not serve in place of the first,
          set in INFERENCE_ENGINE_SECOND_JUDGE_MODEL, gives a second opinion.
        </p>
      ) : null}
    </div>
  );
}
