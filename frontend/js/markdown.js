/**
 * markdown.js — safe, dependency-free Markdown renderer for Nyvexa.
 * Escapes HTML first, then supports: fenced code, headings, lists,
 * blockquotes, tables, hr, links, bold/italic/inline-code.
 */

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ESCAPES[ch]);
}

export function safeUrl(url) {
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
  } catch {
    /* invalid URL */
  }
  return "#";
}

function inline(text) {
  let out = text;
  out = out.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
  out = out.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, label, url) =>
      `<a href="${safeUrl(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`
  );
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  out = out.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  return out;
}

function renderTable(lines) {
  const rows = lines.map((l) =>
    l.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()))
  );
  if (rows.length < 2) return "";
  const head = rows[0];
  const body = rows.slice(2);
  const thead = `<tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr>`;
  const tbody = body
    .map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`)
    .join("");
  return `<table><thead>${thead}</thead><tbody>${tbody}</tbody></table>`;
}

function addCodeChrome(html) {
  const holder = document.createElement("div");
  holder.innerHTML = html;
  holder.querySelectorAll("pre > code").forEach((code) => {
    const pre = code.parentElement;
    const lang = (code.className.match(/language-([\w+-]+)/) || [])[1] || "";
    const wrapper = document.createElement("div");
    wrapper.className = "code-block";
    if (lang) {
      const label = document.createElement("span");
      label.className = "code-block__lang";
      label.textContent = lang;
      wrapper.appendChild(label);
    }
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "code-block__copy";
    copy.textContent = "Copy";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(code.textContent);
        copy.textContent = "Copied";
        setTimeout(() => (copy.textContent = "Copy"), 1500);
      } catch {
        copy.textContent = "Failed";
      }
    });
    wrapper.appendChild(copy);
    pre.parentNode.insertBefore(wrapper, pre);
    wrapper.appendChild(pre);
  });
  return holder.innerHTML;
}

export function renderMarkdown(source) {
  if (!source) return "";
  let text = escapeHtml(source);

  // Close an unterminated code fence so partial streams render as code.
  const fenceCount = (text.match(/```/g) || []).length;
  if (fenceCount % 2 === 1) {
    text += "\n```";
  }

  // Extract fenced code blocks first so their contents are untouched.
  const fences = [];
  let body = text.replace(/```([\w+-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const token = `%%CODEBLOCK${fences.length}%%`;
    fences.push(`<pre><code class="language-${lang || "text"}">${code.replace(/\n$/, "")}</code></pre>`);
    return token;
  });

  const lines = body.split("\n");
  const html = [];
  let list = null; // 'ul' | 'ol' | null
  let quote = null;
  let tableLines = null;

  const closeList = () => {
    if (list) {
      html.push(`</${list}>`);
      list = null;
    }
  };
  const closeQuote = () => {
    if (quote !== null) {
      html.push("</blockquote>");
      quote = null;
    }
  };
  const closeTable = () => {
    if (tableLines) {
      html.push(renderTable(tableLines));
      tableLines = null;
    }
  };
  const closeAll = () => {
    closeList();
    closeQuote();
    closeTable();
  };

  for (const raw of lines) {
    const line = raw.trimEnd();

    if (/^%%CODEBLOCK\d+%%$/.test(line.trim())) {
      closeAll();
      html.push(line.trim());
      continue;
    }

    if (/^\s*$/.test(line)) {
      closeAll();
      continue;
    }

    if (/^\s*\|.*\|\s*$/.test(line)) {
      closeList();
      closeQuote();
      tableLines = tableLines || [];
      tableLines.push(line);
      continue;
    } else {
      closeTable();
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closeAll();
      const level = heading[1].length;
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }

    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
      closeAll();
      html.push("<hr />");
      continue;
    }

    const quoteMatch = line.match(/^&gt;\s?(.*)$/) || line.match(/^>\s?(.*)$/);
    if (quoteMatch) {
      closeList();
      if (quote === null) {
        html.push("<blockquote>");
        quote = true;
      }
      html.push(`<p>${inline(quoteMatch[1])}</p>`);
      continue;
    }

    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      closeQuote();
      const kind = ul ? "ul" : "ol";
      if (list !== kind) {
        closeList();
        html.push(`<${kind}>`);
        list = kind;
      }
      html.push(`<li>${inline((ul || ol)[1])}</li>`);
      continue;
    }

    closeList();
    closeQuote();
    html.push(`<p>${inline(line)}</p>`);
  }
  closeAll();

  let out = html.join("\n");
  out = out.replace(/%%CODEBLOCK(\d+)%%/g, (_, i) => fences[Number(i)]);
  return addCodeChrome(out);
}

/** Incremental-safe render: renders the full buffer each call (fast enough for chat). */
export function renderStream(buffer) {
  return renderMarkdown(buffer);
}
