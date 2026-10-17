/**
 * streaming.js — minimal SSE client over fetch POST (HTTP POST /api/chat).
 * Emits parsed events to a handler: onEvent(eventName, dataObject).
 */

export class SSEClient {
  constructor(url = "/api/chat") {
    this.url = url;
    this.controller = null;
    this.onEvent = () => {};
    this.onEnd = () => {};
    this.onError = () => {};
  }

  get active() {
    return this.controller !== null;
  }

  abort() {
    if (this.controller) {
      this.controller.abort();
      this.controller = null;
    }
  }

  async send(payload) {
    this.abort();
    const controller = new AbortController();
    this.controller = controller;

    let response;
    try {
      const token = localStorage.getItem("nyvexa_token");
      response = await fetch(this.url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
    } catch (err) {
      this.controller = null;
      if (err.name === "AbortError") {
        this.onEnd({ aborted: true });
        return;
      }
      this.onError("Network error — is the server running?");
      return;
    }

    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try {
        const data = await response.json();
        if (data?.error) detail = data.error;
      } catch {
        /* keep default */
      }
      this.controller = null;
      this.onError(detail);
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let sawDone = false;

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let sep;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          const parsed = this.#parseFrame(frame);
          if (!parsed) continue;
          const { event, data } = parsed;
          if (event === "done") sawDone = true;
          if (event === "error") {
            this.onError(data.message || "Unknown error");
            continue;
          }
          this.onEvent(event, data);
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        this.onError("Stream interrupted unexpectedly.");
      }
    } finally {
      this.controller = null;
      this.onEnd({ aborted: !sawDone && !controller.signal.aborted ? false : controller.signal.aborted });
    }
  }

  #parseFrame(frame) {
    let event = "message";
    const dataLines = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) return null;
    let data = {};
    try {
      data = JSON.parse(dataLines.join("\n"));
    } catch {
      data = { text: dataLines.join("\n") };
    }
    return { event, data };
  }
}
