"use client";

import { useState } from "react";
import { api } from "../lib/api";

export default function KeyRows({ rows, onChange, onError }) {
  const [editing, setEditing] = useState("");
  const [secret, setSecret] = useState("");

  async function replace(event, id) {
    event.preventDefault();
    onError("");
    try {
      await api(`/credentials/${id}`, { method: "PATCH", body: JSON.stringify({ secret }) });
      setEditing("");
      setSecret("");
      await onChange();
    } catch (err) {
      onError(err.message);
    }
  }

  async function remove(row) {
    if (!window.confirm(`Remove ${row.label} (${row.provider})? Runs that used it will need another key to re-run.`)) return;
    onError("");
    try {
      await api(`/credentials/${row.id}`, { method: "DELETE" });
      await onChange();
    } catch (err) {
      onError(err.message);
    }
  }

  return (
    <div className="docs">
      {rows.map((row) => (
        <div className="doc" key={row.id} style={{ flexWrap: "wrap", alignItems: "center" }}>
          <span>{row.label} · {row.provider}</span>
          <small>{row.fingerprint}</small>
          {editing === row.id ? (
            <form onSubmit={(event) => replace(event, row.id)} style={{ display: "flex", gap: 8, width: "100%" }}>
              <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="New secret" required autoFocus />
              <button className="primary" type="submit">Replace</button>
              <button className="ghost" type="button" onClick={() => { setEditing(""); setSecret(""); }}>Cancel</button>
            </form>
          ) : (
            <span style={{ display: "flex", gap: 8 }}>
              <button className="ghost" type="button" onClick={() => { setEditing(row.id); setSecret(""); }}>Replace secret</button>
              <button className="ghost" type="button" onClick={() => remove(row)}>Remove</button>
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
