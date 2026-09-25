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
