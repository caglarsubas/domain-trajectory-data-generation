"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import Shell from "../../../components/Shell";
import { api } from "../../../lib/api";

const REWARDS = [
  ["binary_outcome", "Binary outcome", "The journey scores when it reaches the terminal state you care about."],
  ["groupwise_reward_synthesis", "Groupwise reward synthesis", "Test success is scaled by solution quality and how the agent behaved."],
  ["groupwise_advantage_redistribution", "Advantage redistribution", "Passing trajectories are ranked so the cleaner path gets more of the credit."],
  ["group_relative_length_penalty", "Length penalty", "Longer successful journeys are discounted against the group."],
  ["segment_penalty", "Segment penalty", "Format and tool errors reduce the signal on the turn that caused them."],
];
const SIGNALS = [
  ["outcome", "Outcome", "One score for the finished journey."],
  ["solution_rubric", "Solution rubric", "Judges the resulting banking state, not only the final label."],
  ["behavior_rubric", "Behavior rubric", "Judges how the path was built: evidence, checks, and order."],
  ["process_conformance", "Process conformance", "Compares the path with allowed banking transitions."],
  ["decision_score", "Decision score", "Scores the choice at a branch, for later decision evaluation."],
];

const EMPTY = {
  name: "Retail onboarding",
  start_mode: "warm",
  cold_start_acknowledged: false,
  sub_domains: ["onboarding_and_kyc", "deposits"],
  language: "en",
  min_events: 8,
  max_events: 24,
  max_assistant_turns: 12,
  target_trajectory_count: 40,
  event_budget: 800,
  reward_mechanism: "binary_outcome",
  signal_mechanism: "outcome",
  consumer: "post_training",
  target_family: "llm",
  max_cycles: 2,
  credential_id: "",
  thresholds: { helpfulness: 3, correctness: 0.5, safety: 1, pairwise_quality: 0.5 },
};

function Composer() {
  const router = useRouter();
  const params = useSearchParams();
  const from = params.get("from");
  const noteParam = params.get("notes") || "";
  const [step, setStep] = useState(0);
  const [form, setForm] = useState(EMPTY);
  const [sectors, setSectors] = useState([]);
  const [keys, setKeys] = useState([]);
  const [files, setFiles] = useState([]);
  const [links, setLinks] = useState([]);
  const [linkDraft, setLinkDraft] = useState({ kind: "paper", name: "", uri: "" });
  const [existingDocs, setExistingDocs] = useState([]);
  const [inherited, setInherited] = useState([]);
  const [parent, setParent] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api("/sectors").then((data) => setSectors(data.data)).catch((err) => setError(err.message));
    api("/credentials").then((data) => {
      const ready = data.data.filter((item) => item.ready);
      setKeys(ready);
      setForm((current) => ({ ...current, credential_id: current.credential_id || ready[0]?.id || "" }));
    }).catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!from) return;
    api(`/runs/${from}`).then(async (run) => {
      setParent(run);
      setForm((current) => ({
        ...current,
        ...run.config,
        thresholds: { ...EMPTY.thresholds, ...(run.config.thresholds || {}) },
      }));
      const projects = await api("/projects");
      const project = projects.data.find((item) => item.id === run.project_id);
      setExistingDocs(project?.corpus || []);
      const wanted = new Set(noteParam.split(",").filter(Boolean));
      setInherited((run.feedback || []).filter((note) => wanted.has(note.id)));
    }).catch((err) => setError(err.message));
  }, [from, noteParam]);

  const subDomains = sectors[0]?.sub_domains || [];
  const reward = REWARDS.find((item) => item[0] === form.reward_mechanism);
  const signal = SIGNALS.find((item) => item[0] === form.signal_mechanism);
  const docCount = existingDocs.length + files.length + links.length;
  const slots = useMemo(() => Array.from({ length: Math.min(form.max_events, 32) }, (_, i) => i < form.min_events), [form.max_events, form.min_events]);

  function patch(partial) {
    setForm((current) => ({ ...current, ...partial }));
  }

  function toggleDomain(name) {
    const has = form.sub_domains.includes(name);
    const next = has ? form.sub_domains.filter((item) => item !== name) : [...form.sub_domains, name];
    patch({ sub_domains: next });
  }

  async function confirm() {
    setBusy(true);
    setError("");
    try {
      if (from) {
        const child = await api(`/runs/${from}/rerun`, {
          method: "POST",
          body: JSON.stringify({
            feedback_ids: inherited.map((note) => note.id),
            ...form,
            project_id: undefined,
            name: undefined,
          }),
        });
        router.push(`/studio/runs/${child.id}`);
        return;
      }
      const project = await api("/projects", { method: "POST", body: JSON.stringify({ name: form.name, sector: "banking" }) });
      for (const file of files) {
        const body = new FormData();
        body.append("kind", file.kind);
        body.append("upload", file.file);
        await api(`/projects/${project.id}/corpus`, { method: "POST", body });
      }
      for (const link of links) {
        await api(`/projects/${project.id}/corpus/link`, { method: "POST", body: JSON.stringify(link) });
      }
      const run = await api("/runs", {
        method: "POST",
        body: JSON.stringify({ ...form, project_id: project.id, sector: "banking" }),
      });
      router.push(`/studio/runs/${run.id}`);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <Shell>
      <div className="composer">
        <aside className="steps">
          {["Corpus", "Shape", "Signals", "Review"].map((label, index) => (
            <button key={label} type="button" data-on={step === index} onClick={() => setStep(index)}>
              <span>0{index + 1}</span>
              {label}
            </button>
          ))}
        </aside>
        <section className="panel">
          {inherited.length ? (
            <div className="warn">
              <strong>This pass inherits {inherited.length} note{inherited.length === 1 ? "" : "s"}.</strong>
              <ul>
                {inherited.map((note) => (
                  <li key={note.id}>{note.stance}: {note.comment}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {error ? <div className="error">{error}</div> : null}
          {step === 0 ? (
            <>
              <h1 className="word">What should the study know?</h1>
              <p className="lede">A warm start gives the judge a reference. A cold start is allowed, and it will be marked as a weak reference.</p>
              <div className="choice">
                <button type="button" data-on={form.start_mode === "warm"} onClick={() => patch({ start_mode: "warm" })}>
                  <strong>Warm start</strong>
                  <p>Papers, deep-search notes, repos, data sources.</p>
                </button>
                <button type="button" data-on={form.start_mode === "cold"} onClick={() => patch({ start_mode: "cold" })}>
                  <strong>Cold start</strong>
                  <p>Possible, with a weaker correctness check.</p>
                </button>
              </div>
              {form.start_mode === "cold" ? (
                <div className="warn">
                  Warm-start documents produce more representative banking trajectories. Continue only if you accept a weaker reference.
                  <label>
                    <input
                      type="checkbox"
                      checked={form.cold_start_acknowledged}
                      onChange={(e) => patch({ cold_start_acknowledged: e.target.checked })}
                    />{" "}
                    I understand the cold start will be less representative
                  </label>
                </div>
              ) : (
                <div className="drop">
                  <strong>Drop documents</strong>
                  <p className="lede">Text from these documents is read for currency, channel, and product terms. Those terms steer the generator. Provider deep search stays off.</p>
                  <input
                    type="file"
                    multiple
                    onChange={(e) => {
                      const next = Array.from(e.target.files || []).map((file) => ({ file, kind: "paper" }));
                      setFiles((current) => [...current, ...next]);
                    }}
                  />
                  <div className="row">
                    <select value={linkDraft.kind} onChange={(e) => setLinkDraft({ ...linkDraft, kind: e.target.value })}>
                      <option value="paper">Paper</option>
                      <option value="deep_search">Deep search</option>
                      <option value="repo">Repository</option>
                      <option value="data_source">Data source</option>
                    </select>
                    <input placeholder="Name" value={linkDraft.name} onChange={(e) => setLinkDraft({ ...linkDraft, name: e.target.value })} />
                    <input placeholder="https://" value={linkDraft.uri} onChange={(e) => setLinkDraft({ ...linkDraft, uri: e.target.value })} />
                  </div>
                  <button
                    className="ghost"
                    type="button"
                    onClick={() => {
                      if (!linkDraft.name || !linkDraft.uri) return;
                      setLinks((current) => [...current, linkDraft]);
                      setLinkDraft({ kind: "paper", name: "", uri: "" });
                    }}
                  >
                    Add link
                  </button>
                  <div className="docs">
                    {existingDocs.map((doc) => (
                      <div className="doc" key={doc.id}><span>{doc.name}</span><small>{doc.kind} · {doc.content_hash.slice(0, 8)}</small></div>
                    ))}
                    {files.map((item, index) => (
                      <div className="doc" key={item.file.name + index}><span>{item.file.name}</span><small>file</small></div>
                    ))}
                    {links.map((item) => (
                      <div className="doc" key={item.uri}><span>{item.name}</span><small>{item.kind}</small></div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : null}
          {step === 1 ? (
            <>
              <h1 className="word">How long is the journey?</h1>
              <label>Study name</label>
              <input value={form.name} onChange={(e) => patch({ name: e.target.value })} disabled={Boolean(from)} />
              <label>Sub-domain</label>
              <div className="chips">
                {subDomains.map((name) => (
                  <button key={name} type="button" className="chip" data-on={form.sub_domains.includes(name)} onClick={() => toggleDomain(name)}>
                    {name.replaceAll("_", " ")}
                  </button>
                ))}
              </div>
              <div className="row">
                <div>
                  <label>Language</label>
                  <input value={form.language} onChange={(e) => patch({ language: e.target.value })} />
                </div>
                <div>
                  <label>Trajectories</label>
                  <input type="number" min="1" value={form.target_trajectory_count} onChange={(e) => patch({ target_trajectory_count: Number(e.target.value) })} />
                </div>
              </div>
              <div className="row">
                <div>
                  <label>Minimum events</label>
                  <input type="number" min="1" value={form.min_events} onChange={(e) => patch({ min_events: Number(e.target.value) })} />
                </div>
                <div>
                  <label>Maximum events</label>
                  <input type="number" min="1" value={form.max_events} onChange={(e) => patch({ max_events: Number(e.target.value) })} />
                </div>
                <div>
                  <label>Assistant turns</label>
                  <input type="number" min="1" value={form.max_assistant_turns} onChange={(e) => patch({ max_assistant_turns: Number(e.target.value) })} />
                </div>
              </div>
            </>
          ) : null}
          {step === 2 ? (
            <>
              <h1 className="word">What should the score mean?</h1>
              <label>Reward</label>
              <select value={form.reward_mechanism} onChange={(e) => patch({ reward_mechanism: e.target.value })}>
                {REWARDS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
              <p className="lede">{reward?.[2]}</p>
              <label>Signal</label>
              <select value={form.signal_mechanism} onChange={(e) => patch({ signal_mechanism: e.target.value })}>
                {SIGNALS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
              <p className="lede">{signal?.[2]}</p>
              <div className="row">
                <div>
                  <label>Consumer</label>
                  <select value={form.consumer} onChange={(e) => patch({ consumer: e.target.value })}>
                    <option value="post_training">Post-training</option>
                    <option value="decision_scoring">Decision scoring</option>
                    <option value="evaluation">Evaluation</option>
                  </select>
                </div>
                <div>
                  <label>Target family</label>
                  <select value={form.target_family} onChange={(e) => patch({ target_family: e.target.value })}>
                    <option value="llm">LLM</option>
                    <option value="jev">Jev-type</option>
                  </select>
                </div>
                <div>
                  <label>Judge cycles</label>
                  <input type="number" min="1" max="8" value={form.max_cycles} onChange={(e) => patch({ max_cycles: Number(e.target.value) })} />
                </div>
              </div>
              <label>Provider key</label>
              <select value={form.credential_id} onChange={(e) => patch({ credential_id: e.target.value })}>
                <option value="">Select a key</option>
                {keys.map((item) => (
                  <option key={item.id} value={item.id}>{item.label} · {item.provider} · {item.fingerprint}</option>
                ))}
              </select>
              {keys.length === 0 ? <p className="lede">Add a bring-your-own key under Keys before confirming.</p> : null}
            </>
          ) : null}
          {step === 3 ? (
            <>
              <h1 className="word">Review the study</h1>
              <p className="lede">Confirming generates synthetic banking journeys for this configuration. The investigation view opens on the first one.</p>
              <div className="actions">
                <button className="primary" type="button" disabled={busy || !form.credential_id || form.sub_domains.length === 0} onClick={confirm}>
                  {busy ? "Generating" : from ? "Run again" : "Generate journeys"}
                </button>
              </div>
            </>
          ) : null}
          <div className="actions">
            {step > 0 ? <button className="ghost" type="button" onClick={() => setStep(step - 1)}>Back</button> : null}
            {step < 3 ? <button className="primary" type="button" onClick={() => setStep(step + 1)}>Continue</button> : null}
          </div>
        </section>
        <aside className="rail">
          <h3>Banking</h3>
          <dl>
            <dt>Start</dt>
            <dd>{form.start_mode === "warm" ? `Warm · ${docCount} document${docCount === 1 ? "" : "s"}` : form.cold_start_acknowledged ? "Cold · acknowledged" : "Cold · needs acknowledgment"}</dd>
            <dt>Scope</dt>
            <dd>{form.sub_domains.map((item) => item.replaceAll("_", " ")).join(", ") || "None selected"}</dd>
            <dt>Language</dt>
            <dd>{form.language}</dd>
            <dt>Size</dt>
            <dd>{form.target_trajectory_count} trajectories</dd>
            <dt>Length</dt>
            <dd>{form.min_events}–{form.max_events} events</dd>
          </dl>
          <div className="slots" aria-hidden="true">
            {slots.map((on, index) => <i key={index} className={on ? "on" : ""} />)}
          </div>
          <dl>
            <dt>Reward</dt>
            <dd>{reward?.[1]}</dd>
            <dt>Signal</dt>
            <dd>{signal?.[1]}</dd>
          </dl>
          {parent ? <p className="lede">Continues {parent.id.slice(0, 8)}</p> : null}
        </aside>
      </div>
    </Shell>
  );
}

export default function ComposePage() {
  return (
    <Suspense fallback={<Shell><p>Loading the composer.</p></Shell>}>
      <Composer />
    </Suspense>
  );
}
