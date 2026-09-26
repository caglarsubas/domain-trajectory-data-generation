"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { account, api, setSession } from "../lib/api";

export default function Gate() {
  const router = useRouter();
  const [mode, setMode] = useState("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [kind, setKind] = useState("user");
  const [error, setError] = useState("");

  useEffect(() => {
    if (account()) router.replace("/studio");
  }, [router]);

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      const path = mode === "signin" ? "/auth/login" : "/auth/register";
      const body = mode === "signin" ? { email, password } : { email, password, kind };
      const data = await api(path, { method: "POST", body: JSON.stringify(body) });
      setSession(data.access_token, data.account);
      router.replace("/studio");
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="gate">
      <section className="gate-copy">
        <p className="word" style={{ fontSize: 22, margin: 0 }}>
          Trajectory <em style={{ color: "var(--copper)" }}>Studio</em>
        </p>
        <h1 className="word">Journeys you can inspect, mark, and run again.</h1>
        <p>
          Compose a banking, insurance, telecommunications, or airline study, look at the path event by event, leave a note on the step that feels wrong, and start the next iteration from that note.
        </p>
      </section>
      <form className="gate-form" onSubmit={submit}>
        <h2 className="word">{mode === "signin" ? "Sign in" : "Create an account"}</h2>
        <p className="lede">User and demo accounts bring their own provider key. The judge stays on the platform.</p>
        {error ? <div className="error">{error}</div> : null}
        <label htmlFor="email">Email</label>
        <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <label htmlFor="password">Password</label>
        <input id="password" type="password" minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} required />
        {mode === "register" ? (
          <>
            <label htmlFor="kind">Account</label>
            <select id="kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="user">User</option>
              <option value="demo">Demo</option>
            </select>
          </>
        ) : null}
        <div className="actions">
          <button className="primary" type="submit">{mode === "signin" ? "Enter studio" : "Create account"}</button>
          <button className="ghost" type="button" onClick={() => setMode(mode === "signin" ? "register" : "signin")}>
            {mode === "signin" ? "Need an account" : "I already have one"}
          </button>
        </div>
      </form>
    </div>
  );
}
