/**
 * main.js — bootstrap: auth gate (email + Google), PWA, ChatApp.
 */

import { ChatApp } from "./chat.js";
import { api, getToken, setToken } from "./api.js";

function showAuthMessage() {
  try {
    const raw = sessionStorage.getItem("nyvexa_auth_msg");
    if (!raw) return;
    sessionStorage.removeItem("nyvexa_auth_msg");
    const data = JSON.parse(raw);
    const errorEl = document.getElementById("auth-error");
    if (errorEl && data.message) {
      errorEl.textContent = data.message;
      errorEl.hidden = false;
    }
  } catch {
    /* ignore */
  }
}

function setAuthMode(mode) {
  const tabLogin = document.getElementById("tab-login");
  const tabSignup = document.getElementById("tab-signup");
  const nameInput = document.getElementById("auth-name");
  const submit = document.getElementById("auth-submit");
  const password = document.getElementById("auth-password");
  const sub = document.getElementById("auth-sub");

  tabLogin?.classList.toggle("auth__tab--active", mode === "login");
  tabSignup?.classList.toggle("auth__tab--active", mode === "signup");
  if (nameInput) nameInput.hidden = mode !== "signup";
  if (submit) submit.textContent = mode === "signup" ? "Create account" : "Log in";
  if (password) {
    password.autocomplete = mode === "signup" ? "new-password" : "current-password";
    password.placeholder = mode === "signup" ? "Password (min 8 characters)" : "Password";
  }
  if (sub) {
    sub.textContent = mode === "signup"
      ? "Create your Nyvexa account"
      : "Log in to continue";
  }
  document.getElementById("auth-form")?.setAttribute("data-mode", mode);
}

async function showAuth({ googleEnabled = true } = {}) {
  const overlay = document.getElementById("auth-overlay");
  if (!overlay) return;
  overlay.hidden = false;

  const googleBtn = document.getElementById("google-btn");
  if (googleBtn) {
    if (googleEnabled) {
      googleBtn.hidden = false;
      googleBtn.onclick = () => {
        window.location.href = "/api/auth/google";
      };
    } else {
      googleBtn.hidden = true;
    }
  }

  document.getElementById("tab-login")?.addEventListener("click", () => setAuthMode("login"));
  document.getElementById("tab-signup")?.addEventListener("click", () => setAuthMode("signup"));
  setAuthMode("login");
  showAuthMessage();

  const form = document.getElementById("auth-form");
  const errorEl = document.getElementById("auth-error");
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (errorEl) errorEl.hidden = true;
    const mode = form.getAttribute("data-mode") || "login";
    const email = document.getElementById("auth-email").value.trim();
    const password = document.getElementById("auth-password").value;
    const name = document.getElementById("auth-name")?.value?.trim() || "";
    const submit = document.getElementById("auth-submit");
    if (submit) submit.disabled = true;
    try {
      const path = mode === "signup" ? "/api/auth/signup" : "/api/auth/login";
      const body = mode === "signup" ? { email, password, name } : { email, password };
      const data = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(async (r) => {
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.detail || j.error || `Request failed (${r.status})`);
        return j;
      });
      setToken(data.token);
      sessionStorage.removeItem("nyvexa_reauth");
      localStorage.setItem("nyvexa_user", data.user?.email || "");
      overlay.hidden = true;
      bootApp(data.user);
    } catch (err) {
      if (errorEl) {
        errorEl.textContent = err.message || "Authentication failed";
        errorEl.hidden = false;
      }
    } finally {
      if (submit) submit.disabled = false;
    }
  });

  document.getElementById("auth-password")?.focus();
}

async function doLogout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {
    /* ignore */
  }
  setToken("");
  localStorage.removeItem("nyvexa_user");
  location.reload();
}

function renderUserChip(user) {
  const chip = document.getElementById("user-chip");
  if (!chip) return;
  if (!user) {
    chip.hidden = true;
    return;
  }
  chip.hidden = false;
  const email = user.email || localStorage.getItem("nyvexa_user") || "";
  const avatar = document.getElementById("user-avatar");
  const emailEl = document.getElementById("user-email");
  if (avatar) avatar.textContent = (email[0] || "?").toUpperCase();
  if (emailEl) {
    emailEl.textContent = email;
    emailEl.title = email;
  }
  const profileEmail = document.getElementById("profile-email");
  if (profileEmail) {
    profileEmail.textContent = email;
    profileEmail.title = email;
  }
  const logoutBtn = document.getElementById("logout-btn");
  if (logoutBtn && !logoutBtn._bound) {
    logoutBtn._bound = true;
    logoutBtn.addEventListener("click", doLogout);
  }
  const profileLogout = document.getElementById("profile-logout");
  if (profileLogout && !profileLogout._bound) {
    profileLogout._bound = true;
    profileLogout.addEventListener("click", doLogout);
  }
}

function wireHeaderMenus() {
  const settingsBtn = document.getElementById("settings-btn");
  const settingsMenu = document.getElementById("settings-menu");
  const profileBtn = document.getElementById("profile-btn");
  const profileMenu = document.getElementById("profile-menu");

  const closeAll = () => {
    for (const [btn, menu] of [
      [settingsBtn, settingsMenu],
      [profileBtn, profileMenu],
    ]) {
      if (!menu || !btn) continue;
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    }
  };
  const openOne = (btn, menu) => {
    const willOpen = menu.hidden;
    closeAll();
    if (willOpen) {
      menu.hidden = false;
      btn.setAttribute("aria-expanded", "true");
    }
  };

  settingsBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    openOne(settingsBtn, settingsMenu);
  });
  profileBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    openOne(profileBtn, profileMenu);
  });

  // choosing an action closes the menu (checkbox toggles keep it open)
  for (const menu of [settingsMenu, profileMenu]) {
    menu?.addEventListener("click", (e) => {
      const item = e.target.closest(".menu-pop__item");
      if (!item || item.tagName === "LABEL" || item.querySelector("input")) return;
      closeAll();
    });
  }
  document.addEventListener("click", (e) => {
    if (e.target.closest(".menu-wrap")) return;
    closeAll();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAll();
  });

  // Auto-read replies preference
  const autoread = document.getElementById("autoread-toggle");
  if (autoread) {
    try {
      autoread.checked = localStorage.getItem("nyvexa_autoread") === "1";
    } catch {
      /* private mode */
    }
    autoread.addEventListener("change", () => {
      try {
        localStorage.setItem("nyvexa_autoread", autoread.checked ? "1" : "0");
      } catch {
        /* private mode */
      }
    });
  }
}

let appInstance = null;
let menusWired = false;

function bootApp(user) {
  renderUserChip(user);
  if (!menusWired) {
    menusWired = true;
    wireHeaderMenus();
  }
  if (appInstance) return;
  appInstance = new ChatApp();
  window.nyvexa = appInstance;

  if (window.matchMedia("(min-width: 901px)").matches) {
    appInstance.input?.focus();
  }

  window.addEventListener("beforeunload", (e) => {
    if (appInstance.busy) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

async function ensureAuth() {
  let status = { auth_required: true, google_enabled: false, user: null };
  try {
    status = await api("/api/auth/status");
  } catch {
    /* fall through to login */
  }

  // Already logged in with a valid token?
  if (getToken() && status.user) {
    sessionStorage.removeItem("nyvexa_reauth");
    return status.user;
  }

  // Returning from Google OAuth sets token in localStorage via callback page
  if (getToken() && !status.user) {
    try {
      const me = await api("/api/auth/me");
      return me.user;
    } catch {
      setToken("");
      const errorEl = document.getElementById("auth-error");
      if (errorEl) {
        errorEl.textContent = "Your session expired — please log in again.";
        errorEl.hidden = false;
      }
    }
  }

  await showAuth({ googleEnabled: !!status.google_enabled });
  return null;
}

async function boot() {
  const user = await ensureAuth();
  if (!user) return;
  bootApp(user);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
