const base = process.env.NEXT_PUBLIC_API_URL || "/backend";

export function token() {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("traj_token") || "";
}

export function setSession(accessToken, account) {
  localStorage.setItem("traj_token", accessToken);
  localStorage.setItem("traj_account", JSON.stringify(account));
}

export function account() {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem("traj_account");
  return raw ? JSON.parse(raw) : null;
}

export function clearSession() {
  localStorage.removeItem("traj_token");
  localStorage.removeItem("traj_account");
}

export async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const auth = token();
  if (auth) headers.Authorization = `Bearer ${auth}`;
  if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(`${base}${path}`, { ...options, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : response.statusText;
    throw new Error(message);
  }
  return data;
}

async function authorised(path) {
  const headers = {};
  const auth = token();
  if (auth) headers.Authorization = `Bearer ${auth}`;
  const response = await fetch(`${base}${path}`, { headers });
  if (!response.ok) {
    const text = await response.text();
    let message = response.statusText;
    try {
      const detail = JSON.parse(text).detail;
      message = typeof detail === "string" ? detail : message;
    } catch {}
    throw new Error(message);
  }
  return response;
}

export async function apiText(path) {
  return (await authorised(path)).text();
}

export async function download(path, filename) {
  const blob = await (await authorised(path)).blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
