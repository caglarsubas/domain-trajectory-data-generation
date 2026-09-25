"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import KeyRows from "../../components/KeyRows";
import Shell from "../../components/Shell";
import { account, api } from "../../lib/api";

export default function Admin() {
  const router = useRouter();
  const [rows, setRows] = useState([]);
  const [provider, setProvider] = useState("openai");
  const [label, setLabel] = useState("Platform");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");

  function refresh() {
    return api("/credentials").then((data) => setRows(data.data.filter((item) => item.scope === "platform")));
  }

  useEffect(() => {
    if (account() && account().kind !== "admin") router.replace("/studio");
    refresh().catch((err) => setError(err.message));
  }, [router]);

  async function save(event) {
    event.preventDefault();
    setError("");
    try {
      await api("/credentials", { method: "POST", body: JSON.stringify({ provider, label, secret, scope: "platform" }) });
      setSecret("");
      await refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <Shell>
      <h1 className="word" style={{ fontSize: 52, marginBottom: 0 }}>Platform keys</h1>
      <p className="lede">These keys can run provider deep search for an admin study. User and demo runs cannot select them. Journey generation stays in the studio. The evaluation tenant key stays in the server environment.</p>
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
        <div className="actions"><button className="primary" type="submit">Store platform key</button></div>
      </form>
      <KeyRows rows={rows} onChange={refresh} onError={setError} />
    </Shell>
  );
}
