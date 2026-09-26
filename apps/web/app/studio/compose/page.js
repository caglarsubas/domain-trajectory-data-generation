"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Shell from "../../../components/Shell";
import { api } from "../../../lib/api";
import DataSources from "../../../components/DataSources";
import FactsPanel from "../../../components/FactsPanel";

const REWARDS = [
  ["binary_outcome", "Binary outcome", "The journey scores when it reaches the terminal state you care about."],
  ["groupwise_reward_synthesis", "Groupwise reward synthesis", "Test success is scaled by solution quality and how the agent behaved."],
  ["groupwise_advantage_redistribution", "Advantage redistribution", "Passing trajectories are ranked so the cleaner path gets more of the credit."],
  ["group_relative_length_penalty", "Length penalty", "Longer successful journeys are discounted against the group."],
  ["segment_penalty", "Segment penalty", "Format and tool errors reduce the signal on the turn that caused them."],
];
const SIGNALS = [
  ["outcome", "Outcome", "One score for the finished journey."],
  ["solution_rubric", "Solution rubric", "Judges the resulting state, not only the final label."],
  ["behavior_rubric", "Behavior rubric", "Judges how the path was built: evidence, checks, and order."],
  ["process_conformance", "Process conformance", "Compares the path with the allowed transitions for this sector."],
  ["decision_score", "Decision score", "Scores the choice at a branch, for later decision evaluation."],
];

const EMPTY = {
  name: "Retail onboarding",
  sector: "banking",
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
  group_size: 1,
  target_kind: "prompts",
  domain_shares: null,
  jurisdiction: "neutral",
  calibrate: true,
  credential_id: "",
  provider_rollouts: 0,
  provider_call_budget: null,
  provider_model: null,
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
  const [unchecked, setUnchecked] = useState(0);
  const [files, setFiles] = useState([]);
  const [links, setLinks] = useState([]);
  const [linkDraft, setLinkDraft] = useState({ kind: "paper", name: "", uri: "" });
  const [existingDocs, setExistingDocs] = useState([]);
  const [inherited, setInherited] = useState([]);
  const [parent, setParent] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [projectId, setProjectId] = useState("");
  const [searches, setSearches] = useState([]);
  const [searching, setSearching] = useState(false);
  const [searchNote, setSearchNote] = useState("");
  const [quota, setQuota] = useState(null);
  const [projects, setProjects] = useState([]);

  useEffect(() => {
    api("/sectors").then((data) => setSectors(data.data)).catch((err) => setError(err.message));
    api("/credentials").then((data) => {
      const ready = data.data.filter((item) => item.ready);
      setKeys(ready);
      setUnchecked(data.data.length - ready.length);
      setForm((current) => ({ ...current, credential_id: current.credential_id || ready[0]?.id || "" }));
    }).catch((err) => setError(err.message));
    if (!from) api("/projects").then((data) => setProjects(data.data)).catch(() => setProjects([]));
    api("/quota").then((data) => setQuota(data.demo)).catch(() => setQuota(null));
  }, [from]);

  useEffect(() => {
    if (!from) return;
    api(`/runs/${from}`).then(async (run) => {
      setParent(run);
      setProjectId(run.project_id);
      setForm((current) => ({
        ...current,
        ...run.config,
        credential_id: run.config.credential_id || "",
        thresholds: { ...EMPTY.thresholds, ...(run.config.thresholds || {}) },
      }));
      const projects = await api("/projects");
      const project = projects.data.find((item) => item.id === run.project_id);
      setExistingDocs(project?.corpus || []);
      const wanted = new Set(noteParam.split(",").filter(Boolean));
      setInherited((run.feedback || []).filter((note) => wanted.has(note.id)));
    }).catch((err) => setError(err.message));
  }, [from, noteParam]);

  const sector = sectors.find((item) => item.id === form.sector) || sectors[0];
  const subDomains = sector?.sub_domains || [];
  const sectorLabel = sector?.label || (form.sector === "insurance" ? "Insurance" : "Banking");
  const reward = REWARDS.find((item) => item[0] === form.reward_mechanism);
  const signal = SIGNALS.find((item) => item[0] === form.signal_mechanism);
  const docCount = existingDocs.length + files.length + links.length;
  const readableCount = existingDocs.filter((doc) => doc.readable !== false).length;
  const calibrated = existingDocs.filter((doc) => doc.kind === "data_source" && doc.calibration?.status === "ready");
  const unreadable = existingDocs.filter((doc) => doc.readable === false);
  const languages = sector?.languages || ["en", "tr"];
  const smallRun = sector?.small_run_sequences || 64;
  const maxRun = sector?.max_run_sequences || 100000;
  const groupSize = Math.min(Math.max(Number(form.group_size) || 1, 1), 16);
  const accepted = form.target_kind === "accepted_groups";
  const sequences = form.target_trajectory_count * groupSize;
  const shares = form.domain_shares ? form.sub_domains.map((name) => [name, Number(form.domain_shares[name]) || 0]) : [];
  const shareTotal = shares.reduce((sum, [, value]) => sum + value, 0);
  const unit = accepted ? "accepted groups" : groupSize > 1 ? `prompts × ${groupSize}` : "journeys";
  // An accepted-group target may draw up to five times its size; demo limits count that.
  const drawn = accepted ? sequences * 5 : sequences;
  // Provider rollouts: two calls each, one episode per prompt at most, capped by the owner and at 4,000.
  const episodesOn = form.episodes ?? form.consumer === "post_training";
  const rollouts = Number(form.provider_rollouts) || 0;
  const chosenKey = keys.find((item) => item.id === form.credential_id);
  const callEstimate = form.target_trajectory_count * rollouts * 2;
  const providerCalls = Math.min(Number(form.provider_call_budget) || callEstimate, callEstimate, 4000);
  const blockers = [
    form.sub_domains.length === 0 ? "Pick at least one sub-domain on the Shape step." : null,
    form.start_mode === "cold" && !form.cold_start_acknowledged ? "Acknowledge the cold start on the Corpus step." : null,
    form.start_mode === "warm" && docCount === 0 ? "Add at least one warm-start document, or switch to a cold start." : null,
    form.min_events > form.max_events ? "Minimum events cannot exceed maximum events." : null,
    sequences > maxRun ? `This run asks for ${sequences.toLocaleString()} sequences; the limit is ${maxRun.toLocaleString()}.` : null,
    shares.some(([, value]) => value <= 0) ? "Every sub-domain share must be above zero." : null,
    !languages.includes((form.language || "").split("-")[0]) ? `Choose a language: ${languages.join(" or ")}.` : null,
    quota && drawn > quota.max_sequences
      ? `Demo runs hold at most ${quota.max_sequences.toLocaleString()} sequences; this one may draw ${drawn.toLocaleString()}.`
      : null,
    rollouts && !form.credential_id ? "Provider rollouts run on your own key; choose one on the Signals step, or set rollouts to 0." : null,
    rollouts && !episodesOn ? "Provider rollouts need episodes; use the post-training consumer, or set rollouts to 0." : null,
    rollouts && groupSize < 2 ? "Provider rollouts need at least two sequences per prompt: an episode is the decision where a group's sequences part." : null,
    quota && rollouts && providerCalls > quota.max_provider_calls
      ? `Demo runs make at most ${quota.max_provider_calls} provider calls; this one may make ${providerCalls}.`
      : null,
    quota && quota.daily.runs.used >= quota.daily.runs.limit
      ? `Demo accounts can start ${quota.daily.runs.limit} runs a day${quota.daily.runs.frees_at ? `; the next one is available at ${quota.daily.runs.frees_at.slice(11, 16)} UTC` : ""}.`
      : null,
  ].filter(Boolean);
  const slots = useMemo(() => Array.from({ length: Math.min(form.max_events, 32) }, (_, i) => i < form.min_events), [form.max_events, form.min_events]);

  async function deepSearch() {
    setSearching(true);
    setError("");
    try {
      let id = projectId;
      if (!id) {
        if (from) throw new Error("The previous study is still loading.");
        const project = await api("/projects", { method: "POST", body: JSON.stringify({ name: form.name, sector: form.sector }) });
        id = project.id;
        setProjectId(id);
      }
      const started = await api(`/projects/${id}/deep-search`, {
        method: "POST",
        body: JSON.stringify({
          credential_id: form.credential_id,
          sub_domains: form.sub_domains,
          language: form.language,
        }),
      });
      // The search runs as a job. When it ran inline its corpus item is already here; otherwise wait for it.
      let result = started.id ? started : null;
      let job = started.job;
      while (!result) {
        setSearchNote(job.message || "Waiting for a worker.");
        await new Promise((resolve) => setTimeout(resolve, 1500));
        job = await api(`/jobs/${job.id}`);
        if (job.status === "succeeded") result = job.result;
        else if (job.status === "failed" || job.status === "cancelled") throw new Error(job.error || job.message);
      }
      setSearches((current) => [...current, result]);
      setExistingDocs((current) => [...current, result]);
    } catch (err) {
      setError(err.message);
    } finally {
      setSearching(false);
      setSearchNote("");
      api("/quota").then((data) => setQuota(data.demo)).catch(() => {});
    }
  }

  const reloadDocs = useCallback(async () => {
    if (!projectId) return;
    const data = await api("/projects");
    const project = data.data.find((item) => item.id === projectId);
    if (project) setExistingDocs(project.corpus || []);
  }, [projectId]);

  function patch(partial) {
    setForm((current) => ({ ...current, ...partial }));
  }

  function chooseStudy(project) {
    if (from) return;
    if (!project) {
      setProjectId("");
      setExistingDocs([]);
      return;
    }
    const defaults = project.sector === "insurance"
      ? ["quoting", "underwriting", "policy_administration"]
      : ["onboarding_and_kyc", "deposits"];
    setProjectId(project.id);
    setExistingDocs(project.corpus || []);
    patch({ name: project.name, sector: project.sector, sub_domains: project.sector === form.sector ? form.sub_domains : defaults });
  }

  async function uploadPending(id) {
    for (const file of files) {
      const body = new FormData();
      body.append("kind", file.kind);
      body.append("upload", file.file);
      await api(`/projects/${id}/corpus`, { method: "POST", body });
    }
    for (const link of links) {
      await api(`/projects/${id}/corpus/link`, { method: "POST", body: JSON.stringify(link) });
    }
    setFiles([]);
    setLinks([]);
  }

  function chooseSector(id) {
    if (from || projectId) return;
    const defaults = id === "insurance"
      ? ["quoting", "underwriting", "policy_administration"]
      : ["onboarding_and_kyc", "deposits"];
    patch({ sector: id, sub_domains: defaults });
  }

  function toggleDomain(name) {
    const has = form.sub_domains.includes(name);
    const next = has ? form.sub_domains.filter((item) => item !== name) : [...form.sub_domains, name];
    let domainShares = form.domain_shares;
    if (domainShares) {
      domainShares = Object.fromEntries(next.map((item) => [item, domainShares[item] ?? 1]));
      if (next.length < 2) domainShares = null;
    }
    patch({ sub_domains: next, domain_shares: domainShares });
  }

  function setGroupSize(value) {
    const size = Math.min(16, Math.max(1, Number(value) || 1));
    patch({ group_size: size, target_kind: size < 2 ? "prompts" : form.target_kind });
  }

  // Sent explicitly so a re-run uses the cap and model shown here, not the previous run's.
  const providerFields = { provider_call_budget: rollouts ? providerCalls : null, provider_model: form.provider_model || "" };

  async function confirm() {
    setBusy(true);
    setError("");
    try {
      if (from) {
        // Files and links added while re-running belong to the same study.
        await uploadPending(projectId);
        const child = await api(`/runs/${from}/rerun`, {
          method: "POST",
          body: JSON.stringify({
            feedback_ids: inherited.map((note) => note.id),
            ...form,
            ...providerFields,
            project_id: undefined,
            name: undefined,
          }),
        });
        router.push(`/studio/runs/${child.id}`);
        return;
      }
      let id = projectId;
      if (!id) {
        const project = await api("/projects", { method: "POST", body: JSON.stringify({ name: form.name, sector: form.sector }) });
        id = project.id;
        setProjectId(id);
      }
      await uploadPending(id);
      const run = await api("/runs", {
        method: "POST",
        body: JSON.stringify({ ...form, ...providerFields, project_id: id }),
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
              {!from && projects.length ? (
                <>
                  <label>Study</label>
                  <div className="study-list">
                    <button type="button" data-on={!projectId} onClick={() => chooseStudy(null)}>
                      <strong>New study</strong>
                      <small>Start with no documents</small>
                    </button>
                    {projects.map((project) => (
                      <button key={project.id} type="button" data-on={projectId === project.id} onClick={() => chooseStudy(project)}>
                        <strong>{project.name}</strong>
                        <small>{project.sector} · {project.corpus.length} document{project.corpus.length === 1 ? "" : "s"}</small>
                      </button>
                    ))}
                  </div>
                </>
              ) : null}
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
                  Warm-start documents produce more representative trajectories. Continue only if you accept a weaker reference.
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
                  <p className="lede">Text from these documents is read for currency, channel, product, and named events. A provider search can be run on the Signals step and is stored with this study.</p>
                  <input
                    type="file"
                    multiple
                    onChange={(e) => {
                      const next = Array.from(e.target.files || []).map((file) => ({ file, kind: /\.(csv|parquet|xes|jsonocel)$/i.test(file.name) ? "data_source" : "paper" }));
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
                      <div className="doc" key={doc.id} data-readable={doc.readable === false ? "false" : "true"}>
                        <span>{doc.name}</span>
                        <small>
                          {doc.kind.replaceAll("_", " ")}
                          {doc.parser && doc.readable !== false ? ` · ${doc.parser}${doc.parse_detail ? `, ${doc.parse_detail}` : ""}` : ""}
                          {" · "}
                          {doc.readable === false ? doc.unreadable_reason || "not readable" : `${(doc.characters || 0).toLocaleString()} characters read`}
                          {doc.ingest?.status === "fetched" && doc.ingest.final_url ? ` · fetched from ${new URL(doc.ingest.final_url).host}` : ""}
                        </small>
                      </div>
                    ))}
                    {files.map((item, index) => (
                      <div className="doc" key={item.file.name + index}>
                        <span>{item.file.name}</span>
                        <select
                          value={item.kind}
                          style={{ width: "auto", padding: "4px 8px" }}
                          onChange={(e) => setFiles((current) => current.map((entry, i) => (i === index ? { ...entry, kind: e.target.value } : entry)))}
                        >
                          <option value="paper">Paper</option>
                          <option value="deep_search">Deep search</option>
                          <option value="repo">Repository</option>
                          <option value="data_source">Data source</option>
                          <option value="ontology">Ontology</option>
                          <option value="other">Other</option>
                        </select>
                      </div>
                    ))}
                    {links.map((item) => (
                      <div className="doc" key={item.uri}><span>{item.name}</span><small>{item.kind} · fetched when the study is saved{item.kind === "repo" ? "; a GitHub repository is read through its README, docs, and API definitions" : ""}</small></div>
                    ))}
                  </div>
                  {unreadable.length ? (
                    <p className="warn">
                      {unreadable.length === existingDocs.length && !files.length && !links.length
                        ? "None of these documents can be read, so the run will steer like a cold start. "
                        : `${unreadable.length} document${unreadable.length === 1 ? " was" : "s were"} not read and will not steer the run. `}
                      Each says why above. PDF, Word, web pages, Markdown, text, and API definitions are read; scanned PDFs are not.
                    </p>
                  ) : null}
                  <DataSources projectId={projectId} sector={form.sector} docs={existingDocs} events={sector?.event_namespace || []} onChange={reloadDocs} />
                  {existingDocs.length ? (
                    <FactsPanel
                      projectId={projectId}
                      subDomains={form.sub_domains}
                      jurisdiction={form.jurisdiction}
                      language={form.language}
                      refreshKey={existingDocs.length}
                    />
                  ) : null}
                </div>
              )}
            </>
          ) : null}
          {step === 1 ? (
            <>
              <h1 className="word">How long is the journey?</h1>
              <label>Study name</label>
              <input value={form.name} onChange={(e) => patch({ name: e.target.value })} disabled={Boolean(from)} />
              <label>Sector</label>
              <div className="chips">
                {(sectors.length ? sectors : [{ id: "banking", label: "Banking" }, { id: "insurance", label: "Insurance" }]).map((item) => (
                  <button key={item.id} type="button" className="chip" data-on={form.sector === item.id} disabled={Boolean(from || projectId)} onClick={() => chooseSector(item.id)}>
                    {item.label}
                  </button>
                ))}
              </div>
              <label>Jurisdiction</label>
              <div className="chips">
                {(sector?.jurisdictions || [{ id: "neutral", label: "Neutral retail" }]).map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className="chip"
                    data-on={form.jurisdiction === item.id}
                    onClick={() => patch({ jurisdiction: item.id, ...(item.language && languages.includes(item.language) ? { language: item.language } : {}) })}
                  >
                    {item.label}{item.currency ? ` · ${item.currency}` : ""}
                  </button>
                ))}
              </div>
              <p className="lede">
                {form.jurisdiction === "neutral"
                  ? "No country: currency follows the documents, then the language."
                  : "Sets the currency, local product names, and the KYC rules the judge checks and the samples state; the language starts in the country's own."}
              </p>
              <label>Sub-domain</label>
              <div className="chips">
                {subDomains.map((name) => (
                  <button key={name} type="button" className="chip" data-on={form.sub_domains.includes(name)} onClick={() => toggleDomain(name)}>
                    {name.replaceAll("_", " ")}
                  </button>
                ))}
              </div>
              {form.sub_domains.length > 1 ? (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={Boolean(form.domain_shares)}
                    onChange={(e) => patch({ domain_shares: e.target.checked ? Object.fromEntries(form.sub_domains.map((name) => [name, 1])) : null })}
                  />
                  Set a share per sub-domain
                </label>
              ) : null}
              {form.domain_shares ? (
                <div className="shares">
                  {shares.map(([name, value]) => (
                    <div key={name}>
                      <label title={name.replaceAll("_", " ")}>{name.replaceAll("_", " ")}</label>
                      <input
                        type="number"
                        min="0"
                        step="any"
                        value={form.domain_shares[name]}
                        onChange={(e) => patch({ domain_shares: { ...form.domain_shares, [name]: e.target.value === "" ? "" : Number(e.target.value) } })}
                      />
                      <small>{shareTotal > 0 ? `${Math.round((value / shareTotal) * 100)}%` : "–"}</small>
                    </div>
                  ))}
                  <p className="lede">Each sub-domain becomes its own part of the run, with its own target: {accepted ? "accepted groups" : "journeys"} are split by these shares. Without shares, one journey can cross several sub-domains.</p>
                </div>
              ) : null}
              <div className="row">
                <div>
                  <label>Language</label>
                  <select value={(form.language || "en").split("-")[0]} onChange={(e) => patch({ language: e.target.value })}>
                    {languages.map((code) => (
                      <option key={code} value={code}>{{ en: "English", tr: "Turkish" }[code] || code}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label>{accepted ? "Accepted groups" : groupSize > 1 ? "Prompts" : "Trajectories"}</label>
                  <input type="number" min="1" value={form.target_trajectory_count} onChange={(e) => patch({ target_trajectory_count: Number(e.target.value) })} />
                </div>
                <div>
                  <label>Event budget</label>
                  <input
                    type="number"
                    min="1"
                    placeholder="No budget"
                    value={form.event_budget ?? ""}
                    onChange={(e) => patch({ event_budget: e.target.value === "" ? null : Number(e.target.value) })}
                  />
                </div>
              </div>
              {sequences > maxRun ? (
                <p className="warn">This run asks for {sequences.toLocaleString()} sequences; a run holds at most {maxRun.toLocaleString()}.</p>
              ) : accepted || sequences > smallRun || form.domain_shares ? (
                <p className="lede">
                  {accepted
                    ? `The run keeps drawing groups until ${form.target_trajectory_count.toLocaleString()} are accepted, and stops at five times that if too few are.`
                    : `${sequences.toLocaleString()} sequences.`}{" "}
                  It is generated in batches as a background job; the run page reads its journeys a page at a time, and exports are prepared as files.
                </p>
              ) : null}
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
              <label>Sequences per prompt</label>
              <input type="number" min="1" max="16" value={form.group_size} onChange={(e) => setGroupSize(e.target.value)} />
              <p className="lede">
                {groupSize > 1
                  ? `Each prompt gets ${groupSize} rollouts that share the start and differ from the first decision, so rewards compare them within the group.`
                  : "One sequence per prompt gives no group-relative signal: every advantage is zero. MiMo trains with 16."}
              </p>
              <label>Size the run by</label>
              <div className="chips">
                <button type="button" className="chip" data-on={!accepted} onClick={() => patch({ target_kind: "prompts" })}>Prompts drawn</button>
                <button type="button" className="chip" data-on={accepted} disabled={groupSize < 2} onClick={() => patch({ target_kind: "accepted_groups" })}>Accepted groups</button>
              </div>
              <p className="lede">
                {groupSize < 2
                  ? "Accepted groups need more than one sequence per prompt: a group of one is never accepted or rejected."
                  : accepted
                    ? "The dynamic sampler drops groups where every rollout passes or every rollout fails. The run oversamples until the number you set on the Shape step survive."
                    : "The run draws the number of prompts you set; some groups may be dropped by the dynamic sampler later."}
              </p>
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
                  <label title="How many times the judge may send the study back to be regenerated from its notes">Judge rounds</label>
                  <input type="number" min="1" max="8" value={form.max_cycles} onChange={(e) => patch({ max_cycles: Number(e.target.value) })} />
                </div>
              </div>
              <label>Provider key (optional)</label>
              <select value={form.credential_id} onChange={(e) => patch({ credential_id: e.target.value })}>
                <option value="">No key</option>
                {keys.map((item) => (
                  <option key={item.id} value={item.id}>{item.label} · {item.provider} · {item.fingerprint}</option>
                ))}
              </select>
              {keys.length === 0 ? <p className="lede">A key is only needed for the provider deep search and provider rollouts. Add one under Keys to use it.</p> : null}
              {unchecked ? (
                <p className="lede">
                  {unchecked} saved {unchecked === 1 ? "key has" : "keys have"} not been accepted by {unchecked === 1 ? "its" : "their"} provider yet; check {unchecked === 1 ? "it" : "them"} under Keys.
                </p>
              ) : null}
              <div className="actions">
                <button className="ghost" type="button" disabled={searching || !form.credential_id || form.sub_domains.length === 0} onClick={deepSearch}>
                  {searching ? "Searching" : "Run provider deep search"}
                </button>
                {searchNote ? <small className="muted" style={{ alignSelf: "center" }}>{searchNote}</small> : null}
              </div>
              <p className="lede">The key runs a web search at the provider. The report is scrubbed and saved here. It can take a minute. The journey itself is still generated in the studio.</p>
              <div className="row">
                <div>
                  <label title="Each episode's agent turn, answered by a model at your provider">Provider rollouts per episode</label>
                  <input type="number" min="0" max="4" value={form.provider_rollouts} disabled={!rollouts && (!episodesOn || !form.credential_id || groupSize < 2)}
                    onChange={(e) => patch({ provider_rollouts: Math.min(4, Math.max(0, Number(e.target.value) || 0)) })} />
                </div>
                <div>
                  <label title="The most calls this run may make at the provider">Call cap</label>
                  <input type="number" min="2" max="4000" placeholder={String(Math.min(callEstimate, 4000) || "")} value={form.provider_call_budget ?? ""} disabled={!rollouts}
                    onChange={(e) => patch({ provider_call_budget: e.target.value ? Math.min(4000, Math.max(2, Number(e.target.value) || 2)) : null })} />
                </div>
                <div>
                  <label>Model</label>
                  <input type="text" placeholder="The provider's default" value={form.provider_model ?? ""} disabled={!rollouts}
                    onChange={(e) => patch({ provider_model: e.target.value.trim() || null })} />
                </div>
              </div>
              <p className="lede">
                {!episodesOn
                  ? "Provider rollouts add a model's own turn to each episode; episodes come with the post-training consumer."
                  : groupSize < 2
                    ? "Provider rollouts add a model's own turn to each episode, the decision where a group's sequences part; set at least two sequences per prompt above."
                  : !form.credential_id
                    ? "Choose a key to let a model at your provider take each episode's turn."
                    : rollouts
                      ? `A model at ${chosenKey?.provider || "your provider"} takes each episode's turn ${rollouts} ${rollouts === 1 ? "time" : "times"}: it picks an operation, the mock bank answers, and it reports. Each rollout is two calls, so this run makes at most ${providerCalls.toLocaleString()} (${form.target_trajectory_count.toLocaleString()} prompts × ${rollouts} × 2${providerCalls < callEstimate ? `, capped from ${callEstimate.toLocaleString()}` : ""}). Every call is checked against the episode's skeleton and scored on the same rubric. Your provider bills these calls.`
                      : "Set rollouts above 0 to let a model at your provider take each episode's turn. Each rollout is two calls on your key."}
              </p>
              {searches.map((item) => (
                <div className="doc" key={item.id}>
                  <span>{item.name}</span>
                  <small>{item.provider} · {item.model || "search"}</small>
                  <p>{item.excerpt}</p>
                </div>
              ))}
            </>
          ) : null}
          {step === 3 ? (
            <>
              <h1 className="word">Review the study</h1>
              <p className="lede">Confirming generates synthetic {sectorLabel.toLowerCase()} journeys for this configuration. The investigation view opens on the first one.</p>
              {blockers.length ? (
                <div className="warn">
                  <strong>Before generating</strong>
                  <ul className="blockers">{blockers.map((item) => <li key={item}>{item}</li>)}</ul>
                </div>
              ) : null}
              {quota ? (
                <p className="note">
                  Demo account today: {quota.daily.runs.used} of {quota.daily.runs.limit} runs, {quota.daily.judge_cycles.used} of {quota.daily.judge_cycles.limit} judge cycles,
                  {" "}{quota.daily.deep_searches.used} of {quota.daily.deep_searches.limit} deep searches. Runs hold up to {quota.max_sequences.toLocaleString()} sequences.
                </p>
              ) : null}
              {form.start_mode === "warm" && docCount > 0 && readableCount === 0 && !files.length && !links.length ? (
                <p className="warn">No document can be read yet, so warm-start text will not steer this run.</p>
              ) : null}
              {form.start_mode === "warm" && calibrated.length ? (
                <label className="check">
                  <input type="checkbox" checked={form.calibrate !== false} onChange={(e) => patch({ calibrate: e.target.checked })} />
                  Calibrate next steps and durations from {calibrated.map((doc) => doc.name).join(", ")}
                </label>
              ) : null}
              <div className="actions">
                <button className="primary" type="button" disabled={busy || blockers.length > 0} onClick={confirm}>
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
          <h3>{sectorLabel}</h3>
          <dl>
            <dt>Start</dt>
            <dd>
              {form.start_mode === "warm"
                ? `Warm · ${docCount} document${docCount === 1 ? "" : "s"}${existingDocs.length ? ` (${readableCount} readable)` : ""}`
                : form.cold_start_acknowledged ? "Cold · acknowledged" : "Cold · needs acknowledgment"}
            </dd>
            <dt>Scope</dt>
            <dd>{form.sub_domains.map((item) => item.replaceAll("_", " ")).join(", ") || "None selected"}</dd>
            <dt>Jurisdiction</dt>
            <dd>{(sector?.jurisdictions || []).find((item) => item.id === form.jurisdiction)?.label || "Neutral retail"}</dd>
            <dt>Language</dt>
            <dd>{form.language}</dd>
            <dt>Size</dt>
            <dd>
              {form.target_trajectory_count.toLocaleString()} {unit}
              {shares.length && shareTotal > 0 ? ` · ${shares.map(([name, value]) => `${name.replaceAll("_", " ")} ${Math.round((value / shareTotal) * 100)}%`).join(", ")}` : ""}
            </dd>
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
            {rollouts ? (
              <>
                <dt>Provider</dt>
                <dd>{rollouts} per episode · up to {providerCalls.toLocaleString()} calls</dd>
              </>
            ) : null}
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
