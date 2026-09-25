"use client";

import { useEffect, useState } from "react";
import KeyRows from "../../components/KeyRows";
import Shell from "../../components/Shell";
import { api } from "../../lib/api";

export default function Settings() {
  const [rows, setRows] = useState([]);
  const [provider, setProvider] = useState("openai");
  const [label, setLabel] = useState("My key");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");

  function refresh() {
    return api("/credentials").then((data) => setRows(data.data.filter((item) => item.scope === "byok")));
  }

  useEffect(() => {
    refresh().catch((err) => setError(err.message));
  }, []);

  async function save(event) {
    event.preventDefault();
    setError("");
    try {
      await api("/credentials", { method: "POST", body: JSON.stringify({ provider, label, secret, scope: "byok" }) });
      setSecret("");
      await refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <Shell>
      <h1 className="word" style={{ fontSize: 52, marginBottom: 0 }}>Your keys</h1>
      <p className="lede">OpenAI, Anthropic, Google, and xAI keys stay on your account. A warm study can send a web search to the provider you choose. The secret is not shown again.</p>
      {error ? <div className="error">{error}</div> : null}
      <form onSubmit={save} style={{ maxWidth: 520 }}>
        <label>Provider</label>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="openai">OpenAI</option>
          <option value="anthropic">Anthropic</option>
          <option value="google">Google</option>
          <option value="xai">xAI</option>
        </select>
        <label>Label</label>
        <input value={label} onChange={(e) => setLabel(e.target.value)} required />
        <label>Secret</label>
        <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} required />
        <div className="actions"><button className="primary" type="submit">Save key</button></div>
      </form>
      <KeyRows rows={rows} onChange={refresh} onError={setError} />
    </Shell>
  );
}
