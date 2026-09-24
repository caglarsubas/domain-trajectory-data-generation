"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { account, clearSession } from "../lib/api";

export default function Shell({ children }) {
  const path = usePathname();
  const router = useRouter();
  const [who, setWho] = useState(null);

  useEffect(() => {
    const current = account();
    if (!current) {
      router.replace("/");
      return;
    }
    setWho(current);
  }, [router]);

  if (!who) return null;

  function signOut() {
    clearSession();
    router.replace("/");
  }

  const item = (href, label) => (
    <Link href={href} data-active={path === href || (href !== "/studio" && path.startsWith(href))}>
      {label}
    </Link>
  );

  return (
    <>
      <header className="app-bar">
        <Link href="/studio" className="brand word">
          Trajectory <em>Banking</em>
        </Link>
        <nav className="nav">
          {item("/studio", "Studio")}
          {item("/studio/compose", "New run")}
          {item("/settings", "Keys")}
          {who.kind === "admin" ? item("/admin", "Platform") : null}
          <button className="text-btn" onClick={signOut} type="button">
            Sign out
          </button>
        </nav>
      </header>
      <main className="page">{children}</main>
    </>
  );
}
