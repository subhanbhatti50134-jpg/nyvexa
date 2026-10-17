/**
 * history.js — sidebar conversation list: load, select, rename, delete, new.
 */

import { api } from "./api.js";
import { el, toast } from "./ui.js";

export class HistoryPanel {
  constructor({ onSelect, onDeleted }) {
    this.listEl = document.getElementById("conv-list");
    this.onSelect = onSelect || (() => {});
    this.onDeleted = onDeleted || (() => {});
    this.conversations = [];
    this.activeId = null;

    document.getElementById("new-chat-btn")?.addEventListener("click", () => {
      this.activeId = null;
      this.render();
      this.onSelect(null);
    });
  }

  async load() {
    try {
      const data = await api("/api/conversations");
      this.conversations = data.conversations || [];
      this.render();
    } catch (err) {
      if (err.status !== 401) console.warn("history load failed", err);
    }
  }

  setActive(id) {
    this.activeId = id;
    this.render();
  }

  register(id, title) {
    if (!id) return;
    if (!this.conversations.find((c) => c.id === id)) {
      this.conversations.unshift({ id, title: title || "New chat", mode: "chat", updated_at: Date.now() / 1000 });
    } else {
      const c = this.conversations.find((c) => c.id === id);
      if (c && title) c.title = title;
    }
    this.activeId = id;
    this.render();
  }

  async rename(id) {
    const conv = this.conversations.find((c) => c.id === id);
    if (!conv) return;
    const item = this.listEl.querySelector(`[data-conv="${id}"]`);
    if (!item) return;

    const titleEl = item.querySelector(".conv__title");
    const input = el("input", "conv__rename-input", { value: conv.title });
    titleEl.replaceWith(input);
    input.focus();
    input.select();

    const save = async () => {
      const title = input.value.trim();
      if (title && title !== conv.title) {
        try {
          await api(`/api/conversations/${id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title }),
          });
          conv.title = title;
        } catch (err) {
          toast(err.message, "error");
        }
      }
      this.render();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") save();
      if (e.key === "Escape") this.render();
    });
    input.addEventListener("blur", save);
  }

  async remove(id) {
    try {
      await api(`/api/conversations/${id}`, { method: "DELETE" });
      this.conversations = this.conversations.filter((c) => c.id !== id);
      if (this.activeId === id) {
        this.activeId = null;
        this.onDeleted();
      }
      this.render();
      toast("Conversation deleted");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  render() {
    if (!this.listEl) return;
    this.listEl.innerHTML = "";
    if (!this.conversations.length) {
      this.listEl.appendChild(
        el("p", "sidebar__empty", { text: "No conversations yet. Start a new chat!" })
      );
      return;
    }
    for (const conv of this.conversations) {
      const item = el("div", `conv${conv.id === this.activeId ? " conv--active" : ""}`);
      item.dataset.conv = conv.id;
      item.innerHTML = `
        <span class="conv__title"></span>
        <span class="conv__mode"></span>
        <span class="conv__actions">
          <button class="conv__btn" data-act="rename" title="Rename">✎</button>
          <button class="conv__btn conv__btn--danger" data-act="delete" title="Delete">✕</button>
        </span>`;
      item.querySelector(".conv__title").textContent = conv.title || "Untitled";
      item.querySelector(".conv__mode").textContent = conv.mode || "chat";

      item.addEventListener("click", (e) => {
        const act = e.target.closest("[data-act]")?.dataset.act;
        if (act === "rename") {
          e.stopPropagation();
          this.rename(conv.id);
          return;
        }
        if (act === "delete") {
          e.stopPropagation();
          this.remove(conv.id);
          return;
        }
        this.onSelect(conv.id);
      });
      this.listEl.appendChild(item);
    }
  }
}
