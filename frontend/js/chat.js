/**
 * chat.js — conversation orchestration v2.
 * Modes: chat | research | deep | agent | compare
 * Features: history, edit/regenerate, export, RAG upload, voice hooks,
 *           citation jump, usage meter, per-message actions.
 */

import { SSEClient } from "./streaming.js";
import { renderMarkdown } from "./markdown.js";
import { $, el, scrollChatToBottom, typingDots, timeLabel, domainOf, setBusy, toast } from "./ui.js";
import { api } from "./api.js";
import { HistoryPanel } from "./history.js";
import { VoiceInput, SpeechReader } from "./voice.js";

export class ChatApp {
  constructor() {
    this.streamEl = $("#chat-stream");
    this.input = $("#composer-input");
    this.form = $("#composer");
    this.modeSelect = $("#mode-select");
    this.modelSelect = $("#model-select");
    this.modelSelectB = $("#model-select-b");
    this.compareField = $("#compare-field");
    this.usageMeter = $("#usage-meter");
    this.welcome = $("#welcome");

    this.history = [];
    this.conversationId = null;
    this.sse = new SSEClient("/api/chat");
    this.busy = false;
    this.current = null;
    this.lastAssistantText = "";

    this.reader = new SpeechReader();
    this.voice = new VoiceInput({
      onResult: (text, final) => {
        if (final) {
          this.input.value = (this.input.value ? this.input.value + " " : "") + text;
          this.#autosize();
        }
      },
      onStateChange: (active) => {
        $("#voice-btn")?.classList.toggle("is-active", active);
      },
    });

    this.historyPanel = new HistoryPanel({
      onSelect: (id) => this.loadConversation(id),
      onDeleted: () => this.newChat(),
    });

    this.#bind();
    this.loadModels();
    this.historyPanel.load();
  }

  /* ---------------- public API ---------------- */

  sendText(text) {
    const message = (text ?? this.input.value).trim();
    if (!message || this.busy) return;
    this.input.value = "";
    this.#autosize();
    this.#hideWelcome();
    this.#appendUser(message);
    this.#beginAssistant(message);
  }

  stop() {
    this.sse.abort();
  }

  clear() {
    this.sse.abort();
    this.newChat();
    toast("Started a new chat");
  }

  newChat() {
    this.history = [];
    this.conversationId = null;
    this.busy = false;
    this.lastAssistantText = "";
    setBusy(false);
    this.streamEl.innerHTML = "";
    this.#showWelcome();
    this.historyPanel.setActive(null);
    this.usageMeter.hidden = true;
  }

  async loadModels() {
    try {
      const data = await api("/api/models");
      const models = data.models || [];
      for (const select of [this.modelSelect, this.modelSelectB]) {
        if (!select) continue;
        select.innerHTML = "";
        models.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m.id;
          // trim trailing "(NVIDIA)" / "(OpenRouter)" provider suffix for compact pills
          opt.textContent = m.label.replace(/\s*\((?:NVIDIA|OpenRouter|Google|Free)\)\s*$/i, "");
          opt.title = m.description || m.id;
          select.appendChild(opt);
        });
      }
      if (data.default && this.modelSelect) this.modelSelect.value = data.default;
      if (this.modelSelectB && models[1]) this.modelSelectB.value = models[1].id;

      // Restore the user's last model choices (if still available)
      const restore = (select, key) => {
        if (!select) return;
        const saved = localStorage.getItem(key);
        if (saved && [...select.options].some((o) => o.value === saved)) {
          select.value = saved;
        }
        select.addEventListener("change", () => {
          try {
            localStorage.setItem(key, select.value);
          } catch {
            /* private mode */
          }
        });
      };
      restore(this.modelSelect, "nyvexa_model");
      restore(this.modelSelectB, "nyvexa_model_b");
    } catch {
      /* models endpoint may 401 if auth fails */
    }
  }

  async loadConversation(id) {
    if (!id) {
      this.clear(); // New chat button passes null — reset the session
      return;
    }
    if (this.busy) return;
    try {
      const data = await api(`/api/conversations/${id}`);
      const conv = data.conversation;
      const messages = data.messages || [];
      this.conversationId = id;
      this.history = messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => ({ role: m.role, content: m.content }));
      this.historyPanel.setActive(id);
      document.querySelector(".nyvexa")?.classList.remove("sidebar-open");

      this.streamEl.innerHTML = "";
      if (!messages.length) this.#showWelcome();
      for (const m of messages) {
        if (m.role === "user") this.#appendUser(m.content, m.id);
        else this.#appendAssistantFromHistory(m);
      }
      scrollChatToBottom(false);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  exportMarkdown() {
    const lines = [`# Nyvexa conversation`, "", `Exported: ${new Date().toISOString()}`, ""];
    // Walk rendered messages
    document.querySelectorAll(".message").forEach((msg) => {
      const role = msg.classList.contains("message--user") ? "User" : "Nyvexa";
      const content = msg.querySelector(".message__content");
      if (!content) return;
      // For assistant with sources, include source list
      lines.push(`## ${role}`, "");
      lines.push(content.innerText || "");
      const sources = msg.closest(".message")?.querySelector(".sources__list");
      if (sources) {
        lines.push("", "### Sources", "");
        sources.querySelectorAll(".source").forEach((s) => {
          const a = s.querySelector("a");
          if (a) lines.push(`- [${a.textContent}](${a.href})`);
        });
      }
      lines.push("");
    });
    const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `nyvexa-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    URL.revokeObjectURL(url);
    toast("Exported as Markdown");
  }

  /* ---------------- internals ---------------- */

  #bind() {
    this.form.addEventListener("submit", (e) => {
      e.preventDefault();
      this.sendText();
    });
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this.sendText();
      }
    });
    this.input.addEventListener("input", () => this.#autosize());

    $("#stop-btn")?.addEventListener("click", () => this.stop());
    $("#clear-btn")?.addEventListener("click", () => this.clear());
    $("#export-btn")?.addEventListener("click", () => this.exportMarkdown());
    $("#sidebar-toggle")?.addEventListener("click", () => {
      document.querySelector(".nyvexa")?.classList.toggle("sidebar-open");
    });

    this.modeSelect?.addEventListener("change", () => {
      const isCompare = this.modeSelect.value === "compare";
      if (this.compareField) this.compareField.hidden = !isCompare;
    });

    $("#suggestions")?.addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      const mode = chip.dataset.mode;
      if (mode && this.modeSelect) this.modeSelect.value = mode;
      this.modeSelect?.dispatchEvent(new Event("change"));
      this.sendText(chip.textContent);
    });

    // Voice
    $("#voice-btn")?.addEventListener("click", () => {
      if (!this.voice.supported) {
        toast("Speech recognition not supported in this browser", "error");
        return;
      }
      this.voice.toggle();
    });
    $("#speak-btn")?.addEventListener("click", () => {
      if (!this.lastAssistantText) {
        toast("Nothing to read yet");
        return;
      }
      this.reader.toggle(this.lastAssistantText);
    });

    // RAG upload
    $("#file-input")?.addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      await this.#uploadFile(file);
      e.target.value = "";
    });

    this.sse.onEvent = (event, data) => this.#handleEvent(event, data);
    this.sse.onError = (message) => this.#handleError(message);
    this.sse.onEnd = (info) => this.#handleEnd(info);

    this.loadKnowledgeBase();
  }

  #autosize() {
    this.input.style.height = "auto";
    this.input.style.height = `${Math.min(this.input.scrollHeight, 180)}px`;
  }

  #hideWelcome() {
    const w = $("#welcome");
    if (w) w.remove();
  }

  #showWelcome() {
    if ($("#welcome")) return;
    const section = el("section", "welcome");
    section.id = "welcome";
    section.innerHTML = `
      <div class="welcome__orb" aria-hidden="true">
        <span class="welcome__ring"></span>
        <span class="welcome__ring welcome__ring--2"></span>
      </div>
      <h2 class="welcome__title">Hello, I'm Nyvexa</h2>
      <p class="welcome__subtitle">Streaming answers, deep research, tool-using agents, and your own knowledge base.</p>
      <div class="welcome__chips" id="suggestions">
        <button class="chip" type="button" data-mode="agent">Run a Python script that visualizes prime numbers</button>
        <button class="chip" type="button" data-mode="deep">Deep research: small language models in 2026</button>
        <button class="chip" type="button" data-mode="research">Research FastAPI SSE streaming patterns</button>
      </div>`;
    this.streamEl.appendChild(section);
    section.querySelector("#suggestions").addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      const mode = chip.dataset.mode;
      if (mode && this.modeSelect) this.modeSelect.value = mode;
      this.modeSelect?.dispatchEvent(new Event("change"));
      this.sendText(chip.textContent);
    });
  }

  #appendUser(text, dbId = null) {
    const msg = el("article", "message message--user");
    msg.dataset.dbId = dbId || "";
    msg.innerHTML = `
      <div class="message__avatar" aria-hidden="true">You</div>
      <div class="message__body">
        <div class="message__meta">
          <span class="message__role">You</span>
          <span class="message__time">${timeLabel()}</span>
        </div>
        <div class="message__content"></div>
        <div class="msg-actions">
          <button class="msg-actions__btn" data-act="edit">Edit</button>
        </div>
      </div>`;
    msg.querySelector(".message__content").textContent = text;
    msg.querySelector('[data-act="edit"]')?.addEventListener("click", () => this.#editMessage(msg, text));
    this.streamEl.appendChild(msg);
    scrollChatToBottom();
  }

  #appendAssistantFromHistory(m) {
    const msg = el("article", "message message--assistant");
    msg.dataset.dbId = m.id || "";
    msg.innerHTML = `
      <div class="message__avatar" aria-hidden="true"></div>
      <div class="message__body">
        <div class="message__meta">
          <span class="message__role">Nyvexa</span>
          <span class="message__time">${timeLabel(new Date((m.created_at || 0) * 1000))}</span>
        </div>
        <div class="message__content"></div>
        <div class="sources-slot"></div>
        <div class="msg-actions">
          <button class="msg-actions__btn" data-act="copy">Copy</button>
          <button class="msg-actions__btn" data-act="regen">Regenerate</button>
          <button class="msg-actions__btn" data-act="speak">Speak</button>
        </div>
      </div>`;
    const content = msg.querySelector(".message__content");
    content.innerHTML = renderMarkdown(m.content || "");
    this.lastAssistantText = m.content || "";
    this.#wireAssistantActions(msg, m.content || "");
    this.streamEl.appendChild(msg);
  }

  #wireAssistantActions(msg, text) {
    msg.querySelector('[data-act="copy"]')?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text);
        toast("Copied");
      } catch {
        toast("Copy failed", "error");
      }
    });
    msg.querySelector('[data-act="speak"]')?.addEventListener("click", () => this.reader.toggle(text));
    msg.querySelector('[data-act="regen"]')?.addEventListener("click", () => this.#regenerate(msg));
    // citation jump
    msg.addEventListener("click", (e) => {
      const cite = e.target.closest(".cite");
      if (!cite) return;
      e.preventDefault();
      const idx = cite.dataset.cite;
      const sourcesSlot = msg.querySelector(".sources-slot");
      const source = sourcesSlot?.querySelector(`.source[data-index="${idx}"] .source__link`);
      if (source) {
        source.scrollIntoView({ behavior: "smooth", block: "center" });
        const li = source.closest(".source");
        li?.classList.add("source--highlight");
        setTimeout(() => li?.classList.remove("source--highlight"), 1600);
      }
    });
  }

  #editMessage(msg, text) {
    if (this.busy) return;
    const dbId = msg.dataset.dbId;
    // Truncate: remove this message and everything after, put text in composer
    const msgs = [...this.streamEl.querySelectorAll(".message")];
    const idx = msgs.indexOf(msg);
    for (let i = idx; i < msgs.length; i++) msgs[i].remove();
    this.history = this.history.slice(0, idx);
    this.input.value = text;
    this.#autosize();
    this.input.focus();
    // Server-side truncate if we have ids
    if (dbId && this.conversationId) {
      api(`/api/conversations/${this.conversationId}/messages/after/${dbId}`, { method: "DELETE" }).catch(() => {});
    }
    toast("Edit your message and resend");
  }

  #regenerate(msg) {
    if (this.busy) return;
    // Find the last user message before this assistant
    const msgs = [...this.streamEl.querySelectorAll(".message")];
    const idx = msgs.indexOf(msg);
    let lastUser = null;
    for (let i = idx - 1; i >= 0; i--) {
      if (msgs[i].classList.contains("message--user")) {
        lastUser = msgs[i];
        break;
      }
    }
    if (!lastUser) return;
    const text = lastUser.querySelector(".message__content").textContent;
    const dbId = lastUser.dataset.dbId;
    // Remove this assistant message and everything after
    for (let i = idx; i < msgs.length; i++) msgs[i].remove();
    this.history.pop(); // remove last assistant from history
    if (dbId && this.conversationId) {
      api(`/api/conversations/${this.conversationId}/messages/after/${dbId}`, { method: "DELETE" }).catch(() => {});
      // keep user message — delete only after it: re-add user is already in DB; use regen by deleting after user then resending
    }
    this.#beginAssistant(text);
  }

  #beginAssistant(userMessage) {
    this.busy = true;
    setBusy(true);

    const mode = this.modeSelect?.value || "chat";
    const isCompare = mode === "compare";

    const msg = el("article", "message message--assistant");
    if (isCompare) {
      msg.innerHTML = `
        <div class="message__avatar" aria-hidden="true"></div>
        <div class="message__body">
          <div class="message__meta">
            <span class="message__role">Nyvexa · Compare</span>
            <span class="message__time">${timeLabel()}</span>
          </div>
          <div class="research" hidden></div>
          <div class="compare">
            <div class="compare__pane compare__pane--a">
              <div class="compare__head"><span></span></div>
              <div class="compare__content" data-slot="a"></div>
            </div>
            <div class="compare__pane compare__pane--b">
              <div class="compare__head"><span></span></div>
              <div class="compare__content" data-slot="b"></div>
            </div>
          </div>
          <div class="sources-slot"></div>
        </div>`;
    } else {
      msg.innerHTML = `
        <div class="message__avatar" aria-hidden="true"></div>
        <div class="message__body">
          <div class="message__meta">
            <span class="message__role">Nyvexa</span>
            <span class="message__time">${timeLabel()}</span>
          </div>
          <div class="research" hidden></div>
          <div class="message__content"></div>
          <div class="sources-slot"></div>
        </div>`;
      msg.querySelector(".message__content").appendChild(typingDots());
    }

    this.streamEl.appendChild(msg);
    scrollChatToBottom();

    this.current = {
      root: msg,
      mode,
      isCompare,
      content: msg.querySelector(".message__content"),
      researchEl: msg.querySelector(".research"),
      sourcesSlot: msg.querySelector(".sources-slot"),
      buffers: { a: "", b: "" },
      buffer: "",
      hasContent: false,
      userMessage,
    };

    const payload = {
      message: userMessage,
      history: this.history.slice(-20),
      model: this.modelSelect?.value || null,
      mode: mode === "compare" ? (this.modeSelect.dataset.baseMode || "chat") : mode,
      research: mode === "research" || mode === "deep",
      conversation_id: this.conversationId,
      use_kb: $("#kb-toggle")?.checked || false,
    };
    if (isCompare) {
      payload.compare_models = [this.modelSelect?.value, this.modelSelectB?.value].filter(Boolean);
      payload.mode = "chat";
    }

    this.history.push({ role: "user", content: userMessage });
    this.sse.send(payload);
  }

  #handleEvent(event, data) {
    const a = this.current;
    if (!a) return;

    switch (event) {
      case "meta":
        if (data.conversation_id) {
          this.conversationId = data.conversation_id;
          const title = a.userMessage.slice(0, 60);
          this.historyPanel.register(this.conversationId, title);
        }
        if (a.isCompare && data.models) {
          const heads = a.root.querySelectorAll(".compare__head span");
          if (heads[0]) heads[0].textContent = data.models[0] || "A";
          if (heads[1]) heads[1].textContent = data.models[1] || "B";
        }
        break;

      case "research":
        this.#renderResearchStep(data);
        break;

      case "sources":
        this.#renderSources(data.sources || []);
        break;

      case "token": {
        if (a.isCompare) {
          const slot = data.slot || "a";
          a.buffers[slot] = (a.buffers[slot] || "") + (data.text || "");
          const pane = a.root.querySelector(`.compare__content[data-slot="${slot}"]`);
          if (pane) pane.innerHTML = renderMarkdown(a.buffers[slot]) + '<span class="caret"></span>';
        } else {
          if (!a.hasContent) {
            a.content.innerHTML = "";
            a.hasContent = true;
          }
          a.buffer += data.text || "";
          a.content.innerHTML = renderMarkdown(a.buffer) + '<span class="caret"></span>';
        }
        scrollChatToBottom(false);
        break;
      }

      case "done": {
        if (data.usage) this.#showUsage(data.usage);
        if (a.isCompare) {
          if (data.slot) {
            const pane = a.root.querySelector(`.compare__content[data-slot="${data.slot}"]`);
            if (pane) pane.innerHTML = renderMarkdown(a.buffers[data.slot] || "");
          } else {
            const combined = `### ${a.root.querySelector(".compare__pane--a .compare__head span")?.textContent || "A"}\n\n${a.buffers.a}\n\n---\n\n### ${a.root.querySelector(".compare__pane--b .compare__head span")?.textContent || "B"}\n\n${a.buffers.b}`;
            this.lastAssistantText = combined;
            this.history.push({ role: "assistant", content: combined });
            a.root.querySelector(".msg-actions")?.remove();
            const actions = el("div", "msg-actions");
            actions.innerHTML = `
              <button class="msg-actions__btn" data-act="copy">Copy</button>
              <button class="msg-actions__btn" data-act="regen">Regenerate</button>`;
            a.root.querySelector(".message__body").appendChild(actions);
            this.#wireAssistantActions(a.root, combined);
          }
        } else {
          const text = a.buffer || "";
          if (!a.hasContent) a.content.innerHTML = renderMarkdown(text || "*No response.*");
          else a.content.innerHTML = renderMarkdown(text);
          this.lastAssistantText = text;
          this.history.push({ role: "assistant", content: text });
          if (this.history.length > 40) this.history = this.history.slice(-40);
          // actions row
          if (!a.root.querySelector(".msg-actions")) {
            const actions = el("div", "msg-actions");
            actions.innerHTML = `
              <button class="msg-actions__btn" data-act="copy">Copy</button>
              <button class="msg-actions__btn" data-act="regen">Regenerate</button>
              <button class="msg-actions__btn" data-act="speak">Speak</button>`;
            a.root.querySelector(".message__body").appendChild(actions);
            this.#wireAssistantActions(a.root, text);
          }
        }
        if (data.conversation_id) this.historyPanel.load();
        break;
      }

      default:
        break;
    }
  }

  #renderResearchStep(data) {
    const a = this.current;
    if (!a?.researchEl) return;
    const panel = a.researchEl;
    panel.hidden = false;
    if (!panel.querySelector(".research__head")) {
      panel.innerHTML = `
        <div class="research__head">
          <span class="research__spinner"></span>
          <span class="research__title">Autonomous Research</span>
        </div>
        <ul class="research__steps"></ul>`;
    }
    const steps = panel.querySelector(".research__steps");
    const step = el("li", "research__step");
    step.textContent = data.message || data.step || "Working…";
    if (data.queries?.length) {
      const ul = el("ul", "research__queries");
      data.queries.forEach((q) => ul.appendChild(el("li", "research__query", { text: q })));
      step.appendChild(ul);
    }
    steps.querySelectorAll(".research__step").forEach((s) => s.classList.add("research__step--done"));
    steps.appendChild(step);
    scrollChatToBottom();
  }

  #renderSources(sources) {
    const a = this.current;
    if (!a?.sourcesSlot || !sources.length) return;
    const slot = a.sourcesSlot;
    slot.innerHTML = "";

    const box = el("div", "sources");
    const head = el("div", "sources__head");
    head.innerHTML = `<span class="sources__title">Sources</span><span class="sources__count">${sources.length}</span>`;
    const list = el("ul", "sources__list");
    sources.forEach((s) => {
      const li = el("li", "source");
      li.dataset.index = s.index;
      const domain = domainOf(s.url);
      li.innerHTML = `
        <span class="source__index">[${s.index}]</span>
        <span>
          <a class="source__link" href="${encodeURI(s.url)}" target="_blank" rel="noopener noreferrer"></a>
          <span class="source__domain"></span>
        </span>`;
      li.querySelector(".source__link").textContent = s.title || domain;
      li.querySelector(".source__domain").textContent = domain;
      list.appendChild(li);
    });
    head.addEventListener("click", () => (list.hidden = !list.hidden));
    box.appendChild(head);
    box.appendChild(list);
    slot.appendChild(box);

    // Turn [1] markers in the message into clickable citations
    this.#linkCitations(a);
    scrollChatToBottom();
  }

  #linkCitations(a) {
    const target = a.isCompare ? null : a.content;
    if (!target) return;
    // Replace [n] text nodes with cite links (once, on finalized or during stream)
    const html = target.innerHTML;
    if (html.includes("class=\"cite\"")) return;
    target.innerHTML = html.replace(/\[(\d{1,2})\]/g, (m, n) => `<sup class="cite" data-cite="${n}">${n}</sup>`);
  }

  #showUsage(usage) {
    if (!usage || !this.usageMeter) return;
    const inTok = usage.prompt_tokens || 0;
    const outTok = usage.completion_tokens || 0;
    const total = usage.total_tokens || inTok + outTok;
    this.usageMeter.hidden = false;
    this.usageMeter.textContent = `↑${inTok} ↓${outTok} (${total} tok)`;
    this.usageMeter.title = usage.model || "";
  }

  #handleError(message) {
    toast(message, "error");
    const a = this.current;
    if (!a) return;
    const holder = a.isCompare ? a.root.querySelector(".compare") : a.content;
    const err = el("div", "message message--error");
    err.style.marginTop = "8px";
    err.innerHTML = `<div class="message__content"></div>`;
    err.querySelector(".message__content").textContent = message;
    (holder || a.root).appendChild(err);
  }

  #handleEnd(info) {
    this.busy = false;
    setBusy(false);
    const a = this.current;
    if (a) {
      a.root.querySelectorAll(".caret").forEach((c) => c.remove());
      const panel = a.researchEl;
      if (panel && !panel.hidden) {
        panel.querySelectorAll(".research__step").forEach((s) => s.classList.add("research__step--done"));
        const spinner = panel.querySelector(".research__spinner");
        if (spinner) spinner.style.display = "none";
        // collapse research panel after done
        setTimeout(() => {
          panel.style.transition = "opacity 0.4s";
          panel.style.opacity = "0.65";
        }, 800);
      }
      if (!a.isCompare) {
        if (!a.hasContent && !a.buffer) {
          a.content.innerHTML = info?.aborted
            ? "<p><em>Stopped.</em></p>"
            : "<p><em>Request ended without a response.</em></p>";
        } else {
          this.#linkCitations(a);
        }
      }
      this.historyPanel.load();
    }
    this.current = null;
    scrollChatToBottom();
  }

  async #uploadFile(file) {
    toast(`Uploading ${file.name}…`);
    const form = new FormData();
    form.append("file", file);
    try {
      const token = localStorage.getItem("nyvexa_token");
      const resp = await fetch("/api/files/upload", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.error || data.detail || `Upload failed (${resp.status})`);
      }
      const result = await resp.json();
      toast(`Ingested ${result.name} (${result.chunks} chunks)`);
      this.loadKnowledgeBase();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async loadKnowledgeBase() {
    const list = $("#kb-files");
    if (!list) return;
    try {
      const data = await api("/api/files");
      list.innerHTML = "";
      for (const f of data.files || []) {
        const li = el("li", "kb__file");
        li.innerHTML = `
          <span class="kb__file-name"></span>
          <span class="kb__file-chunks"></span>
          <button class="kb__file-del" title="Remove">✕</button>`;
        li.querySelector(".kb__file-name").textContent = f.name;
        li.querySelector(".kb__file-chunks").textContent = `${f.chunks}`;
        li.querySelector(".kb__file-del").addEventListener("click", async () => {
          try {
            await api(`/api/files/${f.id}`, { method: "DELETE" });
            this.loadKnowledgeBase();
            toast("Removed from knowledge base");
          } catch (err) {
            toast(err.message, "error");
          }
        });
        list.appendChild(li);
      }
    } catch {
      /* not critical */
    }
  }
}
