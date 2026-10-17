/**
 * api.js — fetch wrapper that attaches the auth bearer token.
 */

const TOKEN_KEY = "nyvexa_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) {
    // Token missing/revoked → drop it and bounce back to the login screen once.
    if (token) {
      setToken("");
      if (!sessionStorage.getItem("nyvexa_reauth")) {
        sessionStorage.setItem("nyvexa_reauth", "1");
        location.reload();
      }
    }
    const err = new Error("Unauthorized");
    err.status = 401;
    throw err;
  }
  if (!resp.ok) {
    let detail = `Request failed (${resp.status})`;
    try {
      const data = await resp.json();
      if (data?.error) detail = data.error;
      if (data?.detail) detail = typeof data.detail === "string" ? data.detail : detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return resp.json();
}
