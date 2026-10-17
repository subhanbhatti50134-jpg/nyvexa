/**
 * ui.js — DOM helpers, toasts, autosize, scroll, typing indicators.
 */

export function $(selector, root = document) {
  return root.querySelector(selector);
}

export function $all(selector, root = document) {
  return [...root.querySelectorAll(selector)];
}

export function el(tag, className, attrs = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  });
  return node;
}

export function toast(message, type = "info") {
  let node = $(".toast");
  if (!node) {
    node = el("div", "toast");
    document.body.appendChild(node);
  }
  node.textContent = message;
  node.className = `toast toast--${type} toast--visible`;
  clearTimeout(node._timer);
  node._timer = setTimeout(() => {
    node.classList.remove("toast--visible");
  }, 3200);
}

export function autosize(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
}

export function scrollChatToBottom(smooth = true) {
  const chat = $("#chat");
  if (!chat) return;
  chat.scrollTo({ top: chat.scrollHeight, behavior: smooth ? "smooth" : "auto" });
}

export function typingDots() {
  const wrap = el("span", "typing");
  for (let i = 0; i < 3; i++) wrap.appendChild(el("span", "typing__dot"));
  return wrap;
}

export function timeLabel(date = new Date()) {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function domainOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function setBusy(busy) {
  const send = $("#send-btn");
  const stop = $("#stop-btn");
  const input = $("#composer-input");
  if (send) send.disabled = busy;
  if (stop) stop.hidden = !busy;
  if (input) input.disabled = busy;
  const hint = $("#status-hint");
  if (hint) hint.textContent = busy ? "Nyvexa is responding…" : "Shift + Enter for a new line";
}
