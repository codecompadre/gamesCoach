/* A local, dependency-free coaching client. Coach and athlete text only enters
 * the document through text nodes; Markdown never becomes trusted HTML. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const token = document.querySelector('meta[name="gamescoach-token"]')?.content || "";
  const ICONS = {
    plus: ["M12 5v14M5 12h14"],
    search: ["M21 21l-5-5", "M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0"],
    athlete: ["M20 21v-2a7 7 0 0 0-14 0v2", "M17 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0"],
    folder: ["M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z", "M3 10h18"],
    book: ["M12 5v16", "M12 5C9 3 5 3 2 4v15c4-1 7-1 10 2 3-3 6-3 10-2V4c-3-1-7-1-10 1"],
    settings: ["M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Z", "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0"],
    menu: ["M4 6h16M4 12h16M4 18h16"],
    layers: ["m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5"],
    activity: ["M2 12h5l3-8 4 16 3-8h5"],
    calendar: ["M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2ZM7 2v5M17 2v5M3 10h18M7 14h3M14 14h3M7 18h3"],
    flag: ["M4 22V3", "M4 3c6-5 10 5 16 0v12c-6 5-10-5-16 0"],
    heart: ["M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"],
    "arrow-up": ["M12 19V5M5 12l7-7 7 7"],
    close: ["m6 6 12 12M18 6 6 18"],
    copy: ["M9 9h12v12H9zM5 15H3V3h12v2"],
    upload: ["M12 16V3M7 8l5-5 5 5M3 16v5h18v-5"],
    check: ["m5 12 4 4L19 6"],
    trash: ["M3 6h18M8 6V3h8v3M5 6l1 15h12l1-15M10 10v7M14 10v7"],
  };
  const KIND_LABELS = {chat: "Conversation", workout: "Workout", training: "Training plan", nutrition: "Nutrition & fueling", competition: "Competition prep", document: "Training file"};
  const DEFAULT_REQUESTS = {
    workout: "Create a balanced CrossFit workout with warm-up, scaling, and cooldown.",
    training: "Create a progressive CrossFit training plan with recovery and measurable goals.",
    nutrition: "Suggest practical nutrition goals that support CrossFit training and recovery.",
    competition: "Create a CrossFit competition preparation plan with pacing, skills, and tapering.",
  };
  const state = {data: null, session: null, references: new Set(), busy: false, generating: false, pending: "", context: null, modal: null, drafts: new Map(), unsaved: false};
  let previousFocus = null;
  let composing = false;

  function node(tag, className = "", text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined && text !== null) result.textContent = String(text);
    return result;
  }
  function icon(name) {
    const wrapper = node("span", "icon-slot");
    wrapper.setAttribute("aria-hidden", "true");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    for (const path of ICONS[name] || ICONS.activity) {
      const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
      line.setAttribute("d", path);
      svg.append(line);
    }
    wrapper.append(svg);
    return wrapper;
  }
  function button(text, action, className = "button", iconName = null) {
    const result = node("button", className);
    result.type = "button";
    if (iconName) result.append(icon(iconName));
    result.append(document.createTextNode(text));
    result.dataset.mutation = "";
    result.disabled = state.busy;
    result.addEventListener("click", action);
    return result;
  }
  function introduction(text) { return node("p", "modal-intro", text); }
  function section(text) { return node("h3", "section-heading", text); }
  function help(text) { return node("p", "help-text", text); }
  function empty(text) { return node("div", "empty-state", text); }
  function shortText(text, length = 85) {
    const line = String(text || "Untitled").replace(/\s+/g, " ").trim();
    return line.length > length ? `${line.slice(0, length - 1)}…` : line;
  }
  function dateLabel(value) {
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return "";
    return new Intl.DateTimeFormat(undefined, {month: "short", day: "numeric", year: "numeric"}).format(date);
  }
  function sessionEntries() { return Array.isArray(state.data?.sessions) ? state.data.sessions : []; }
  function libraryEntries() { return Array.isArray(state.data?.library) ? state.data.library : []; }
  function titleOf(entry) { return shortText(entry?.request || entry?.filename || "Saved entry"); }
  function currentDraftKey() { return state.session?.id || "new"; }
  function rememberDraft() { state.drafts.set(currentDraftKey(), $("message-input").value); }

  async function api(path, body) {
    const options = {method: body === undefined ? "GET" : "POST", headers: {"X-GamesCoach-Token": token}, cache: "no-store", credentials: "same-origin", redirect: "error"};
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try { response = await fetch(path, options); }
    catch { throw new Error("The local coaching server could not be reached. Check that gamesCoach is still running in your Ubuntu terminal."); }
    let result;
    try { result = await response.json(); }
    catch { throw new Error("The local server returned an unreadable response. No request was retried."); }
    if (!response.ok) throw new Error(typeof result.error === "string" ? result.error : "The request could not be completed.");
    return result;
  }
  function notice(message, isError = false, persistent = false) {
    if (!message) return;
    const region = $("notice-region");
    const item = node("div", `notice${isError ? " error" : ""}`);
    if (isError) item.setAttribute("role", "alert");
    item.append(node("div", "notice-text", message));
    const close = node("button", "icon-button");
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss notice");
    close.append(icon("close"));
    close.addEventListener("click", () => item.remove());
    item.append(close);
    region.append(item);
    while (region.children.length > 4) region.firstElementChild.remove();
    if (!isError && !persistent) setTimeout(() => item.remove(), 11000);
  }
  function modalError(message) {
    if (!$("modal").open) { notice(message, true); return; }
    let status = $("modal-body").querySelector(".modal-status");
    if (!status) {
      status = node("div", "modal-status");
      status.setAttribute("role", "alert");
      $("modal-body").prepend(status);
    }
    status.textContent = message;
    status.scrollIntoView({block: "nearest"});
  }
  function showWarnings(result) {
    for (const warning of result?.warnings || []) notice(String(warning), true, true);
  }
  function setBusy(busy, generating = false) {
    state.busy = busy;
    state.generating = busy && generating;
    document.body.dataset.busy = String(busy);
    $("main-content").setAttribute("aria-busy", String(busy));
    for (const control of document.querySelectorAll("[data-mutation]")) {
      control.disabled = busy || !state.data || control.dataset.forceDisabled === "true" || (control.dataset.resetConfirmation === "true" && $("reset-confirmation")?.value !== "RESET");
    }
    $("modal-close").disabled = busy;
    $("message-input").readOnly = busy || state.unsaved;
    $("history-search").disabled = busy;
    updateSend();
  }
  async function mutation(action) {
    if (state.busy) return;
    setBusy(true);
    const progress = node("p", "busy-label", "Working on this computer…");
    progress.setAttribute("role", "status");
    if ($("modal").open) $("modal-body").prepend(progress);
    try { await action(); }
    catch (error) { modalError(error.message || "The change could not be completed."); }
    finally { progress.remove(); setBusy(false); }
  }
  async function refreshState(showNotices = false) {
    state.data = await api("/api/state");
    renderSidebar();
    renderContextStrip();
    const demo = Boolean(state.data.demo);
    $("mode-badge").hidden = !demo;
    $("request-caption").textContent = demo ? "Demo: static sample replies. No API requests or personal customization." : "Both coaches use your context. Live replies use paid APIs.";
    $("version-label").textContent = state.data.version ? `v${state.data.version}` : "";
    const profile = state.data.profile;
    const guides = libraryEntries().length;
    $("welcome-context").textContent = `${profile ? `${profile.name || "Athlete"}'s profile is ready` : "Add your athlete profile to make coaching personal"} · ${guides ? `${guides} PDF ${guides === 1 ? "guide" : "guides"} available to both coaches` : "Your PDF guides can live here, too"}. New conversations start without saved references.`;
    if (showNotices) for (const message of state.data.notices || []) notice(String(message), false, true);
  }
  function renderSidebar() {
    const query = $("history-search").value.toLowerCase().trim();
    const chats = sessionEntries().filter((entry) => entry.kind === "chat" && entry.demo === Boolean(state.data?.demo));
    $("chat-count").textContent = String(chats.length);
    $("library-count").textContent = String(libraryEntries().length);
    const list = $("thread-list");
    list.replaceChildren();
    const visible = chats.filter((entry) => `${entry.request} ${entry.created_at}`.toLowerCase().includes(query));
    if (!visible.length) {
      list.append(node("p", "sidebar-empty", chats.length ? "No conversations match your search." : "Every good training block starts with a conversation."));
      return;
    }
    for (const entry of visible) {
      const item = button("", () => loadSession(entry.id), `thread-button${state.session?.id === entry.id ? " active" : ""}`);
      if (entry.id === state.session?.id) item.setAttribute("aria-current", "page");
      item.title = entry.request;
      item.append(node("span", "thread-title", titleOf(entry)), node("span", "thread-meta", `${dateLabel(entry.created_at)}${entry.demo ? " · Demo" : ""}`));
      list.append(item);
    }
  }
  function renderContextStrip() {
    const count = state.references.size;
    $("references-label").textContent = count ? `${count} saved ${count === 1 ? "reference" : "references"} attached` : "No saved references attached";
    const guides = libraryEntries().length;
    $("library-context").textContent = guides ? `${guides} PDF ${guides === 1 ? "guide" : "guides"} ready` : "CrossFit only. Always.";
  }
  function closeSidebar() {
    $("sidebar").classList.remove("open");
    $("sidebar-scrim").hidden = true;
    $("mobile-menu").setAttribute("aria-expanded", "false");
  }
  function freshChat() {
    if (state.busy) return;
    rememberDraft();
    state.session = null;
    state.references = new Set();
    state.context = null;
    state.pending = "";
    state.unsaved = false;
    $("plan-kind").value = "";
    $("message-input").value = state.drafts.get("new") || "";
    $("message-input").readOnly = false;
    closeModal();
    closeSidebar();
    renderMessages(true);
    renderSidebar();
    renderContextStrip();
    resizeComposer();
    $("message-input").focus();
  }
  async function loadSession(id) {
    if (state.busy) return;
    rememberDraft();
    await mutation(async () => {
      const result = await api(`/api/session/${encodeURIComponent(id)}`);
      if (result.session.kind !== "chat") { showEntry(result.session); return; }
      state.session = result.session;
      state.references = new Set(result.session.context_ids || []);
      state.context = null;
      state.unsaved = false;
      state.pending = "";
      $("message-input").value = state.drafts.get(id) || "";
      $("plan-kind").value = "";
      closeModal(true);
      closeSidebar();
      renderMessages(true);
      renderSidebar();
      renderContextStrip();
      resizeComposer();
    });
    $("message-input").focus();
  }

  // Markdown is rendered from a small set of DOM elements. Raw HTML remains
  // ordinary text; links require an explicitly allowed web protocol; images
  // remain text, avoiding remote fetches from model-generated content.
  function allowedURL(value) {
    try {
      const url = new URL(value);
      return (url.protocol === "https:" || url.protocol === "http:") && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }
  function inline(parent, text, depth = 0) {
    if (depth > 6) { parent.append(document.createTextNode(text)); return; }
    const pattern = /(`+)([^`\n]+?)\1|\*\*([^\n]+?)\*\*|__([^\n]+?)__|\[([^\[\]\n]+)\]\(([^\s)]+)\)|~~([^\n]+?)~~|(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)/g;
    let position = 0;
    for (const match of text.matchAll(pattern)) {
      if (match.index > position) parent.append(document.createTextNode(text.slice(position, match.index)));
      let part;
      if (match[1]) part = node("code", "", match[2]);
      else if (match[3] !== undefined || match[4] !== undefined) { part = node("strong"); inline(part, match[3] ?? match[4], depth + 1); }
      else if (match[5] !== undefined) {
        const url = allowedURL(match[6]);
        if (url) {
          part = node("a");
          part.href = url;
          part.target = "_blank";
          part.rel = "noopener noreferrer";
          inline(part, match[5], depth + 1);
        } else part = document.createTextNode(match[0]);
      } else if (match[7] !== undefined) { part = node("del"); inline(part, match[7], depth + 1); }
      else { part = node("em"); inline(part, match[8] ?? match[9], depth + 1); }
      parent.append(part);
      position = match.index + match[0].length;
    }
    if (position < text.length) parent.append(document.createTextNode(text.slice(position)));
  }
  function tableCells(line) { return line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|")); }
  function tableDivider(line) { return /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line); }
  function listItem(line) { return /^(\s*)([-+*]|\d+[.)])\s+(.+)$/.exec(line); }
  function blockStart(lines, i) {
    return /^\s*(#{1,6}\s|```|~~~|>|(?:[-*_]\s*){3,}$)/.test(lines[i]) || Boolean(listItem(lines[i])) || (i + 1 < lines.length && lines[i].includes("|") && tableDivider(lines[i + 1]));
  }
  function markdown(text, depth = 0) {
    const root = node("div", "markdown");
    if (depth >= 20) {
      root.append(node("p", "", String(text || "")));
      return root;
    }
    const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i += 1; continue; }
      const fence = /^\s*(`{3,}|~{3,})(.*)$/.exec(line);
      if (fence) {
        const content = [];
        const marker = fence[1][0];
        const length = fence[1].length;
        i += 1;
        while (i < lines.length && !(lines[i].trim().startsWith(marker.repeat(length)) && /^\s*[`~]+\s*$/.test(lines[i]))) { content.push(lines[i]); i += 1; }
        if (i < lines.length) i += 1;
        const pre = node("pre");
        pre.append(node("code", "", content.join("\n")));
        root.append(pre);
        continue;
      }
      const heading = /^\s*(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
      if (heading) {
        const title = heading[2];
        const h = node(`h${Math.min(heading[1].length + 1, 6)}`);
        if (/warm.?up|mobility/i.test(title)) h.classList.add("block-warmup");
        else if (/strength|skill|gymnastic|weightlift/i.test(title)) h.classList.add("block-strength");
        else if (/conditioning|aerobic|metcon/i.test(title)) h.classList.add("block-conditioning");
        else if (/fuel|nutrition|recover|scal/i.test(title)) h.classList.add("block-fueling");
        inline(h, title);
        root.append(h);
        i += 1;
        continue;
      }
      if (/^\s*(?:[-*_]\s*){3,}$/.test(line)) { root.append(node("hr")); i += 1; continue; }
      if (line.includes("|") && i + 1 < lines.length && tableDivider(lines[i + 1])) {
        const wrapper = node("div", "table-scroll");
        const table = node("table");
        const head = node("thead");
        const row = node("tr");
        const headers = tableCells(line);
        for (const value of headers) { const cell = node("th"); cell.scope = "col"; inline(cell, value); row.append(cell); }
        head.append(row);
        table.append(head);
        const body = node("tbody");
        i += 2;
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          const row = node("tr");
          const cells = tableCells(lines[i]);
          for (let n = 0; n < Math.max(headers.length, cells.length); n += 1) { const cell = node("td"); inline(cell, cells[n] || ""); row.append(cell); }
          body.append(row);
          i += 1;
        }
        table.append(body);
        wrapper.append(table);
        root.append(wrapper);
        continue;
      }
      if (/^\s*>/.test(line)) {
        const quoted = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) { quoted.push(lines[i].replace(/^\s*>\s?/, "")); i += 1; }
        const blockquote = node("blockquote");
        blockquote.append(...markdown(quoted.join("\n"), depth + 1).childNodes);
        root.append(blockquote);
        continue;
      }
      if (listItem(line)) {
        const first = listItem(line);
        const ordered = /^\d/.test(first[2]);
        const list = node(ordered ? "ol" : "ul");
        if (ordered) list.start = Number.parseInt(first[2], 10);
        const stack = [{indent: first[1].length, list, last: null}];
        while (i < lines.length) {
          const entry = listItem(lines[i]);
          if (!entry) {
            if (lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && stack.at(-1).last) {
              stack.at(-1).last.append(document.createTextNode(" "));
              inline(stack.at(-1).last, lines[i].trim());
              i += 1;
              continue;
            }
            if (!lines[i].trim() && i + 1 < lines.length && listItem(lines[i + 1])) { i += 1; continue; }
            break;
          }
          const indent = entry[1].length;
          while (stack.length > 1 && indent < stack.at(-1).indent) stack.pop();
          if (indent > stack.at(-1).indent && stack.at(-1).last) {
            const nested = node(/^\d/.test(entry[2]) ? "ol" : "ul");
            stack.at(-1).last.append(nested);
            stack.push({indent, list: nested, last: null});
          }
          const current = stack.at(-1);
          if (indent === first[1].length && /^\d/.test(entry[2]) !== ordered) break;
          const li = node("li");
          inline(li, entry[3]);
          current.list.append(li);
          current.last = li;
          i += 1;
        }
        root.append(list);
        continue;
      }
      const paragraph = [line];
      i += 1;
      while (i < lines.length && lines[i].trim() && !blockStart(lines, i)) { paragraph.push(lines[i]); i += 1; }
      const p = node("p");
      inline(p, paragraph.join("\n"));
      root.append(p);
    }
    return root;
  }
  async function copyText(text, control) {
    try {
      await navigator.clipboard.writeText(text);
      if (control) {
        control.replaceChildren(icon("check"), document.createTextNode("Copied"));
        setTimeout(() => { if (control.isConnected) control.replaceChildren(icon("copy"), document.createTextNode("Copy reply")); }, 1800);
      }
    } catch { notice("Your browser could not copy automatically. Select the reply text and use Ctrl+C.", true); }
  }
  function messageNode(message, pending = false) {
    const article = node("article", `message ${message.role === "user" ? "user" : "assistant"}${pending ? " pending" : ""}`);
    const label = node("div", "message-label");
    if (message.role === "user") {
      label.append(document.createTextNode("You"));
      article.append(label, node("div", "user-bubble", message.content));
    } else {
      const avatar = node("span", "coach-avatar"); avatar.append(icon("activity"));
      label.append(avatar, document.createTextNode("gamesCoach"));
      if (state.session?.demo || state.data?.demo) label.append(node("small", "", "DEMO SAMPLE"));
      article.append(label, markdown(message.content));
      const actions = node("div", "message-actions");
      const copy = node("button", "copy-button");
      copy.type = "button";
      copy.append(icon("copy"), document.createTextNode("Copy reply"));
      copy.addEventListener("click", () => copyText(message.content, copy));
      actions.append(copy);
      article.append(actions);
    }
    return article;
  }
  function nearBottom() {
    const scroll = $("conversation-scroll");
    return scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 160;
  }
  function renderMessages(forceBottom = false) {
    const follow = forceBottom || nearBottom();
    const list = $("message-list");
    list.replaceChildren();
    for (const message of state.session?.messages || []) list.append(messageNode(message));
    if (state.pending) list.append(messageNode({role: "user", content: state.pending}, true));
    if (state.generating) {
      const working = node("div", "working-message");
      working.setAttribute("role", "status");
      const label = node("div", "message-label");
      const avatar = node("span", "coach-avatar"); avatar.append(icon("activity"));
      label.append(avatar, document.createTextNode("Your coaches are working"));
      const details = node("div", "working-details");
      const dots = node("span", "working-dots"); dots.setAttribute("aria-hidden", "true");
      dots.append(node("i"), node("i"), node("i"));
      details.append(dots, document.createTextNode(state.data?.demo ? "Preparing a static demo reply…" : "Gemini drafts → ChatGPT reviews → Gemini replies"));
      working.append(label, details);
      list.append(working);
    }
    $("welcome").hidden = Boolean(state.session || state.pending);
    $("conversation-title").textContent = state.session ? titleOf(state.session) : state.pending ? shortText(state.pending) : "A fresh starting line";
    if (follow) requestAnimationFrame(() => { $("conversation-scroll").scrollTop = $("conversation-scroll").scrollHeight; });
    updateSend();
  }
  function updateSend() { $("send-button").disabled = state.busy || state.unsaved || !$("message-input").value.trim() || !state.data; }
  function resizeComposer() {
    const input = $("message-input");
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 190)}px`;
    const overLimit = input.value.trim().length > 12000;
    $("request-caption").classList.toggle("caption-over-limit", overLimit);
    $("request-caption").textContent = overLimit
      ? `${input.value.trim().length.toLocaleString()} characters · Limit 12,000 per message. Import a long training file instead.`
      : state.data?.demo ? "Demo: static sample replies. No API requests or personal customization." : "Both coaches use your context. Live replies use paid APIs.";
    updateSend();
  }
  async function submitMessage(event) {
    event?.preventDefault();
    if (state.busy || state.unsaved || !state.data) return;
    let message = $("message-input").value.trim();
    if (!message) return;
    if (message.length > 12000) {
      notice("This message is longer than 12,000 characters. Shorten it or import your training as a file. Nothing was sent to either coach.", true);
      return;
    }
    if (await handleCommand(message)) return;
    let planKind = $("plan-kind").value || null;
    const planCommand = /^\/\s*(workout|training|nutrition|competition)(?:\s+([\s\S]*))?$/i.exec(message);
    if (planCommand) {
      planKind = planCommand[1].toLowerCase();
      message = planCommand[2]?.trim() || DEFAULT_REQUESTS[planKind];
    }
    const originalDraft = $("message-input").value;
    const oldKey = currentDraftKey();
    state.drafts.delete(oldKey);
    state.pending = message;
    $("message-input").value = "";
    resizeComposer();
    setBusy(true, true);
    renderMessages(true);
    try {
      const result = await api("/api/chat", {id: state.session?.id || null, message, plan_kind: planKind, reference_ids: [...state.references]});
      state.session = result.session;
      state.references = new Set(result.context?.reference_ids || result.session.context_ids || []);
      state.context = result.context || null;
      state.pending = "";
      state.unsaved = result.saved === false;
      $("plan-kind").value = "";
      if (state.unsaved) notice("This reply could not be saved. Copy it now. Start a new conversation or reopen a saved conversation before sending another message, so the coaches do not lose this exchange.", true, true);
      if (result.plan) notice(`${KIND_LABELS[result.plan.kind] || "Plan"} saved in Saved plans & files.`);
      if (result.context?.notice) notice(result.context.notice, false, true);
      showWarnings(result);
      try { await refreshState(); }
      catch (error) { notice(error.message, true); }
    } catch (error) {
      state.pending = "";
      $("message-input").value = originalDraft;
      state.drafts.set(oldKey, originalDraft);
      resizeComposer();
      notice(error.message || "The coaches could not complete this reply. Your message is still in the composer. No request was retried.", true, true);
    } finally {
      setBusy(false);
      renderMessages();
      renderSidebar();
      renderContextStrip();
      $("message-input").focus();
    }
  }

  function showModal(title, kind = "", eyebrow = "YOUR WORKSPACE") {
    if (state.busy && !$("modal").open) return null;
    if (!$("modal").open) previousFocus = document.activeElement;
    state.modal = kind;
    $("modal-title").textContent = title;
    $("modal-eyebrow").textContent = eyebrow;
    $("modal-body").replaceChildren();
    if (!$("modal").open) $("modal").showModal();
    closeSidebar();
    return $("modal-body");
  }
  function closeModal(force = false) {
    if (state.busy && !force) return;
    if ($("modal").open) $("modal").close();
    state.modal = null;
  }
  function field(labelText, value = "", options = {}) {
    const label = node("label", `field${options.full ? " full" : ""}`);
    label.append(node("span", "", labelText));
    const input = node(options.textarea ? "textarea" : "input");
    if (!options.textarea) input.type = options.type || "text";
    if (options.name) input.name = options.name;
    input.value = value === null || value === undefined ? "" : String(value);
    if (options.placeholder) input.placeholder = options.placeholder;
    if (options.min !== undefined) input.min = options.min;
    if (options.max !== undefined) input.max = options.max;
    if (options.step !== undefined) input.step = options.step;
    input.maxLength = options.maxLength || 8000;
    input.dataset.mutation = "";
    input.disabled = state.busy;
    label.append(input);
    if (options.description) label.append(node("small", "", options.description));
    return {label, input};
  }
  function showProfile() {
    const body = showModal("Athlete profile", "profile", "MAKE IT PERSONAL");
    if (!body) return;
    body.append(introduction("Give both coaches the details that matter for your CrossFit training. Your saved profile is available in every conversation; you can update it whenever your priorities change."));
    const profile = state.data.profile || {};
    const form = node("form");
    const grid = node("div", "form-grid");
    const fields = [
      ["name", "Name", {placeholder: "What should your coaches call you?"}],
      ["experience", "CrossFit experience", {placeholder: "e.g. 4 years, currently competing"}],
      ["goals", "Training & competition goals", {textarea: true, full: true, placeholder: "What are you working toward?"}],
      ["training_days_per_week", "Training days per week", {type: "number", min: 1, max: 7}],
      ["session_minutes", "Minutes per session", {type: "number", min: 5, max: 300}],
      ["equipment", "Available equipment", {textarea: true, full: true, placeholder: "Your gym, machines, weights, and any limits"}],
      ["limitations", "Injuries, limitations & movements to avoid", {textarea: true, full: true, placeholder: "What should your coaches account for?"}],
      ["competition_date", "Competition date (optional)", {type: "date"}],
      ["age", "Age (optional)", {type: "number", min: 1, max: 120}],
      ["bodyweight_kg", "Bodyweight in kg (optional)", {type: "number", min: 1, max: 600, step: "any"}],
      ["dietary_preferences", "Dietary preferences & restrictions", {textarea: true, full: true}],
    ];
    const inputs = new Map();
    for (const [name, label, options] of fields) {
      const control = field(label, profile[name] ?? "", {...options, name});
      inputs.set(name, control.input);
      grid.append(control.label);
    }
    form.append(grid);
    const actions = node("div", "form-actions");
    actions.append(button("Cancel", () => closeModal(), "button secondary"));
    const save = button("Save athlete profile", () => form.requestSubmit());
    actions.append(save);
    form.append(actions);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const result = {};
      for (const [name, input] of inputs) {
        const value = input.value.trim();
        result[name] = ["training_days_per_week", "session_minutes", "age", "bodyweight_kg"].includes(name) ? (value === "" ? null : Number(value)) : value;
      }
      mutation(async () => {
        await api("/api/profile", {profile: result});
        await refreshState();
        closeModal(true);
        notice("Athlete profile saved. Both coaches will use it on your next reply.");
      });
    });
    body.append(form);
  }
  function referenceRow(entry, includeRead = true) {
    const row = node("div", "entry-row");
    const text = node("div", "entry-text");
    text.append(node("p", "entry-title", titleOf(entry)), node("p", "entry-meta", `${KIND_LABELS[entry.kind] || entry.kind} · ${dateLabel(entry.created_at)}${entry.demo ? " · Demo" : ""}`));
    const actions = node("div", "entry-actions");
    if (includeRead) actions.append(button("Read", () => readEntry(entry.id), "button secondary"));
    const label = node("label", "checkbox-label");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.references.has(entry.id);
    checkbox.dataset.mutation = "";
    checkbox.disabled = state.busy || entry.id === state.session?.id;
    if (entry.id === state.session?.id) checkbox.dataset.forceDisabled = "true";
    checkbox.setAttribute("aria-label", `Use ${titleOf(entry)} as a saved reference`);
    checkbox.addEventListener("change", () => {
      const selected = new Set(state.references);
      if (checkbox.checked) selected.add(entry.id); else selected.delete(entry.id);
      mutation(async () => {
        try { await setReferences(selected); }
        catch (error) { checkbox.checked = state.references.has(entry.id); throw error; }
      });
    });
    label.append(checkbox, document.createTextNode("Use"));
    actions.append(label);
    row.append(text, actions);
    return row;
  }
  async function setReferences(references) {
    if (state.session && !state.unsaved) {
      const result = await api("/api/references", {id: state.session.id, reference_ids: [...references]});
      if (result.session) state.session = result.session;
      else state.session.context_ids = [...references];
      showWarnings(result);
    }
    state.references = new Set(references);
    state.context = null;
    renderContextStrip();
  }
  function showReferences() {
    const body = showModal("Saved references", "references", "CHOOSE WHAT YOUR COACHES USE");
    if (!body) return;
    body.append(introduction("Attach saved plans, training files, or earlier conversations when they are useful. A new browser conversation starts with none attached. Your athlete profile and active PDF library are still available automatically."));
    if (state.references.size) {
      body.append(button("Remove all saved references", () => mutation(async () => {
        await setReferences(new Set());
        showReferences();
        notice("Saved references removed. This conversation, your profile, and PDF library still apply.");
      }), "button secondary", "layers"));
    }
    body.append(section("Available saved entries"));
    const entries = sessionEntries().filter((entry) => entry.id !== state.session?.id);
    const list = node("div", "entry-list");
    for (const entry of entries) list.append(referenceRow(entry));
    body.append(entries.length ? list : empty("No saved entries yet. Import a training file, save a plan, or come back after your first conversation."));
  }
  async function readEntry(id) {
    if (state.busy) return;
    await mutation(async () => {
      const result = await api(`/api/session/${encodeURIComponent(id)}`);
      showEntry(result.session);
    });
  }
  function showEntry(entry) {
    const body = showModal(KIND_LABELS[entry.kind] || "Saved entry", "entry", `${dateLabel(entry.created_at)}${entry.demo ? " · DEMO SAMPLE" : " · SAVED LOCALLY"}`);
    if (!body) return;
    body.append(node("p", "view-request", entry.request));
    if (entry.kind === "chat") {
      for (const message of entry.messages || []) body.append(messageNode(message));
    } else if (entry.kind === "document") body.append(node("div", "view-request", entry.final_plan));
    else body.append(markdown(entry.final_plan));
    const actions = node("div", "form-actions");
    const copy = node("button", "button secondary");
    copy.type = "button";
    copy.append(icon("copy"), document.createTextNode("Copy text"));
    copy.addEventListener("click", () => copyText(entry.kind === "chat" ? entry.messages.map((message) => `${message.role === "user" ? "You" : "gamesCoach"}\n${message.content}`).join("\n\n") : entry.final_plan));
    actions.append(copy);
    if (entry.id !== state.session?.id) actions.append(button(state.references.has(entry.id) ? "Remove reference" : "Use as reference", () => mutation(async () => {
      const refs = new Set(state.references);
      if (refs.has(entry.id)) refs.delete(entry.id); else refs.add(entry.id);
      await setReferences(refs);
      showEntry(entry);
      notice(refs.has(entry.id) ? "Reference attached to this conversation." : "Reference removed from this conversation.");
    }), "button", "layers"));
    body.append(actions);
  }

  async function fileBase64(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const chunks = [];
    for (let i = 0; i < bytes.length; i += 8192) chunks.push(String.fromCharCode(...bytes.subarray(i, i + 8192)));
    return btoa(chunks.join(""));
  }
  async function acceptImport(result) {
    if (result.session && state.session?.id === result.session.id) state.session = result.session;
    if (result.entry?.id) state.references.add(result.entry.id);
    showWarnings(result);
    await refreshState();
    renderContextStrip();
    notice(result.guide ? "PDF guide added to the knowledge library. Its brief and relevant passages will be available to both coaches." : "Training file imported and selected as a reference for this conversation.");
  }
  async function uploadFile(file) {
    if (!file || state.busy) return;
    if (!/\.(docx|txt|md|pdf)$/i.test(file.name)) { modalError("Choose a .docx, .txt, .md, or .pdf file."); return; }
    const maximum = /\.pdf$/i.test(file.name) ? 25 : 10;
    if (file.size > maximum * 1024 * 1024) { modalError(`Choose a ${maximum === 25 ? "PDF" : "training file"} smaller than ${maximum} MiB.`); return; }
    await mutation(async () => {
      const result = await api("/api/upload", {filename: file.name, data: await fileBase64(file), chat_id: state.unsaved ? null : state.session?.id || null});
      await acceptImport(result);
      showFiles();
    });
  }
  async function importPath(path) {
    if (!path.trim() || state.busy) return;
    await mutation(async () => {
      const result = /\.pdf["']?\s*$/i.test(path) ? await api("/api/library", {path}) : await api("/api/import", {path, chat_id: state.unsaved ? null : state.session?.id || null});
      await acceptImport(result);
      showFiles();
    });
  }
  function uploadSection() {
    const fragment = document.createDocumentFragment();
    const zone = node("div", "upload-zone");
    zone.append(icon("upload"), node("p", "", "Bring your training into the conversation"), node("small", "", "Word / text / Markdown up to 10 MiB · PDF up to 25 MiB"));
    const input = node("input", "sr-only");
    input.type = "file";
    input.accept = ".docx,.txt,.md,.pdf";
    input.setAttribute("aria-label", "Choose a training file or PDF guide");
    input.dataset.mutation = "";
    input.addEventListener("change", () => uploadFile(input.files?.[0]));
    zone.append(button("Choose a file", () => input.click(), "button secondary"), input);
    for (const eventName of ["dragenter", "dragover"]) zone.addEventListener(eventName, (event) => { event.preventDefault(); if (!state.busy) zone.classList.add("drag-over"); });
    for (const eventName of ["dragleave", "drop"]) zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.remove("drag-over"); });
    zone.addEventListener("drop", (event) => { if (!state.busy) uploadFile(event.dataTransfer?.files?.[0]); });
    fragment.append(zone);
    const form = node("form", "inline-form");
    const path = field("Or read a path on this computer", "", {placeholder: "C:\\Users\\you\\Desktop\\training.docx"});
    const submit = button("Read file", () => form.requestSubmit(), "button secondary");
    form.append(path.label, submit);
    form.addEventListener("submit", (event) => { event.preventDefault(); importPath(path.input.value); });
    fragment.append(form, help("Windows paths work in Ubuntu / WSL. Training documents are saved locally and selected as references; PDFs join the standing knowledge library."));
    return fragment;
  }
  function showFiles() {
    const body = showModal("Saved plans & files", "files", "YOUR TRAINING, IN ONE PLACE");
    if (!body) return;
    body.append(uploadSection(), section("Saved plans & training documents"));
    const entries = sessionEntries().filter((entry) => entry.kind !== "chat");
    if (!entries.length) { body.append(empty("Your saved workouts, training plans, nutrition goals, competition plans, and imported training files will appear here.")); return; }
    const search = field("Find a saved entry", "", {type: "search", placeholder: "Search plans and training files"});
    body.append(search.label);
    const list = node("div", "entry-list");
    list.classList.add("saved-entry-list");
    const fill = () => {
      list.replaceChildren();
      const matches = entries.filter((entry) => `${entry.request} ${entry.kind}`.toLowerCase().includes(search.input.value.toLowerCase()));
      for (const entry of matches) list.append(referenceRow(entry));
      if (!matches.length) list.append(empty("No saved entries match your search."));
    };
    search.input.addEventListener("input", fill);
    fill();
    body.append(help("Check Use to include an entry in this conversation. Reading or selecting saved entries does not call either API."), list);
  }
  function showLibrary() {
    const body = showModal("PDF knowledge library", "library", "STANDING CROSSFIT REFERENCES");
    if (!body) return;
    body.append(introduction("Your guides are prepared locally. Both coaches automatically receive a standing brief and relevant source passages on every coaching request. New conversations keep this library available."));
    const guides = libraryEntries();
    const list = node("div", "entry-list");
    for (const guide of guides) {
      const row = node("div", "entry-row");
      const content = node("div", "entry-text");
      content.append(node("p", "entry-title", guide.filename), node("p", "entry-meta", `${guide.pages || guide.page_count || 0} PDF pages · prepared locally`));
      row.append(content, button("Remove", () => mutation(async () => {
        await api("/api/library/remove", {id: guide.id});
        await refreshState();
        showLibrary();
        notice("Guide removed from future library lookups. The original PDF is unchanged.");
      }), "button secondary"));
      list.append(row);
    }
    body.append(guides.length ? list : empty("No guides yet. Add your CrossFit methodology PDFs below or upload a PDF from Saved plans & files."));
    body.append(section("Add a PDF or watch a folder"));
    const form = node("form", "inline-form");
    const path = field("PDF file or folder path", "", {placeholder: "C:\\Users\\you\\Desktop\\CrossFit"});
    form.append(path.label, button("Add to library", () => form.requestSubmit()));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!path.input.value.trim()) { path.input.focus(); return; }
      mutation(async () => {
        const result = await api("/api/library", {path: path.input.value.trim()});
        showWarnings(result);
        await refreshState();
        showLibrary();
        notice("PDF knowledge updated locally. Both coaches will receive the prepared context on your next request.");
      });
    });
    body.append(form, help("A watched folder checks for new or changed PDFs at startup. Importing or refreshing a guide does not call the coaching APIs."));
    const folder = state.data.folder;
    if (folder?.folder) {
      body.append(section("Folder checked at startup"), node("p", "view-request", folder.folder), button("Stop checking this folder", () => mutation(async () => {
        await api("/api/library/unwatch", {});
        await refreshState();
        showLibrary();
        notice("Folder auto-loading stopped. Imported PDF knowledge remains available.");
      }), "button secondary"));
    }
    body.append(help("Removing a guide or resetting library memory never deletes your original PDF files."));
  }
  function detailList(entries) {
    const list = node("dl", "detail-list");
    for (const [label, value] of entries) { const row = node("div"); row.append(node("dt", "", label), node("dd", "", value)); list.append(row); }
    return list;
  }
  function showContext() {
    const body = showModal("Your coaching context", "context", "YOU CHOOSE THE STARTING POINT");
    if (!body) return;
    body.append(introduction("Saved context helps your coaches stay consistent. It is context supplied by this app; it does not retrain either model. A fresh conversation clears the conversation thread and starts without saved references."));
    const profile = state.data.profile;
    body.append(detailList([
      ["This conversation", state.session ? `${state.session.messages.length / 2} saved exchanges` : "New · no earlier exchanges"],
      ["Athlete profile", profile ? profile.name || "Saved profile" : "Not set"],
      ["Saved references attached", state.references.size],
      ["Standing PDF guides", libraryEntries().length],
      ["Optional coaching notes", state.data.config?.notes?.enabled && state.data.config?.notes?.configured ? "Enabled" : "Not active"],
    ]));
    const passages = state.context?.methodology_excerpts || state.session?.methodology_excerpts || [];
    if (passages.length) {
      body.append(section("Source passages used for the latest reply"));
      const sources = node("ul", "source-list");
      const seen = new Set();
      for (const passage of passages) {
        const citation = `${passage.filename}, PDF p. ${passage.pdf_page}`;
        if (!seen.has(citation)) { sources.append(node("li", "", citation)); seen.add(citation); }
      }
      body.append(sources);
    }
    const actions = node("div", "form-actions");
    actions.append(button("Choose saved references", showReferences, "button secondary"), button("Start a fresh conversation", freshChat));
    body.append(actions, help("For a completely new learning path, Settings & memory lets you clear saved history or reset all app memory. Neither option changes the permanent CrossFit coaching scope."));
  }
  function settingSwitch(title, description, checked, name) {
    const row = node("div", "setting-row");
    const descriptionNode = node("div");
    descriptionNode.append(node("h3", "", title), node("p", "", description));
    const label = node("label", "switch");
    const input = node("input");
    input.type = "checkbox";
    input.checked = checked;
    input.name = name;
    input.setAttribute("aria-label", title);
    input.dataset.mutation = "";
    label.append(input, node("span", "switch-track"));
    row.append(descriptionNode, label);
    return {row, input};
  }
  function showSettings() {
    const body = showModal("Settings & memory", "settings", "YOUR SPACE. YOUR STARTING POINT.");
    if (!body) return;
    const config = state.data.config || {};
    const memory = state.data.memory || {};
    const settings = memory.settings || {};
    body.append(introduction(state.data.demo ? "You are using demo mode. Replies are static examples and do not call either API. Saved demo conversations remain clearly labeled." : "Your browser connects to the coaching server on this computer. Live coaching shares the selected athlete context with Gemini and ChatGPT and may incur API charges."));
    body.append(section("Coaching models"), detailList([
      ["Gemini · lead coach", config.models?.Gemini || "Not configured"],
      ["Gemini API key", config.keys?.Gemini ? "Configured · value hidden" : "Not configured"],
      ["ChatGPT · reviewing coach", config.models?.ChatGPT || "Not configured"],
      ["OpenAI API key", config.keys?.ChatGPT ? "Configured · value hidden" : "Not configured"],
    ]), help("API keys are read from the server's environment. Keys are never shown or entered on this page."), section("Context preferences"));
    const form = node("form");
    const notes = settingSwitch("Optional coaching notes", config.notes?.configured ? "Use your configured local coaching notes on future requests. The CrossFit scope always applies." : "No optional coaching notes are configured. Enabling this allows configured notes to be used on future requests.", settings.coach_notes !== false, "coach_notes");
    const automatic = settingSwitch("Recent-history references in the CLI", "Let new terminal chats attach recent saved entries automatically. New browser conversations always start without saved references.", settings.auto_references !== false, "auto_references");
    form.append(notes.row, automatic.row);
    const actions = node("div", "form-actions");
    actions.append(button("Save preferences", () => form.requestSubmit(), "button secondary"));
    form.append(actions);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      mutation(async () => {
        await api("/api/memory/settings", {auto_references: automatic.input.checked, coach_notes: notes.input.checked});
        await refreshState();
        showSettings();
        notice("Context preferences saved for future coaching requests.");
      });
    });
    body.append(form, section("Saved on this computer"));
    const stats = node("div", "memory-stats");
    for (const [count, label] of [[memory.chats || 0, "chats"], [memory.plans || 0, "plans"], [memory.documents || 0, "training files"], [memory.guides || 0, "PDF guides"]]) {
      const item = node("span"); item.append(node("strong", "", count), document.createTextNode(label)); stats.append(item);
    }
    body.append(stats);
    if (memory.unknown_sessions) body.append(help(`${memory.unknown_sessions} unreadable saved ${memory.unknown_sessions === 1 ? "entry will" : "entries will"} also be removed when history is cleared.`));
    const danger = node("section", "danger-section");
    danger.append(section("Choose a clean starting point"));
    for (const [scope, title, description, label] of [
      ["history", "Clear saved history", "Remove chats, plans, and imported training text. Keep your athlete profile and PDF knowledge library.", "Clear history"],
      ["all", "Reset all app memory", "Also clear your athlete profile and PDF index, stop folder auto-loading, and disable optional coaching notes and automatic CLI history references.", "Reset all memory"],
    ]) {
      const row = node("div", "memory-choice");
      const text = node("div"); text.append(node("h4", "", title), node("p", "", description));
      row.append(text, button(label, () => showReset(scope), scope === "all" ? "button danger" : "button secondary"));
      danger.append(row);
    }
    danger.append(help("You will review the exact changes and type RESET before anything is deleted. Original source files and API keys stay untouched. Resetting app memory does not delete data from your provider accounts or retrain their models."));
    body.append(danger);
  }
  function showReset(scope) {
    const all = scope === "all";
    const body = showModal(all ? "Reset all app memory?" : "Clear saved history?", "reset", "THIS CHOICE IS YOURS");
    if (!body) return;
    body.append(introduction("This permanently deletes local saved context. You can start a fresh conversation without deleting anything by choosing New conversation instead."));
    const description = node("div", "reset-description");
    description.append(node("p", "", "This will delete:"));
    const list = node("ul");
    list.append(node("li", "", "All saved conversations and coaching plans"), node("li", "", "All imported training text saved by this app"));
    if (all) list.append(node("li", "", "Your athlete profile and prepared PDF knowledge index"), node("li", "", "The watched PDF folder configuration"), node("li", "", "Optional coaching notes and automatic CLI history references will be disabled"));
    description.append(list, node("p", "", all ? "Your original Word/PDF/text files and API keys stay untouched. The permanent CrossFit coaching instructions remain." : "Your athlete profile, PDF knowledge, watched folder, and coaching-note preference stay in place. Original files and API keys stay untouched."));
    body.append(description);
    const form = node("form");
    const confirmation = field("Type RESET to confirm", "", {placeholder: "RESET", maxLength: 20});
    confirmation.input.id = "reset-confirmation";
    confirmation.input.autocomplete = "off";
    confirmation.input.spellcheck = false;
    const actions = node("div", "form-actions");
    actions.append(button("Keep my memory", showSettings, "button secondary"));
    const commit = button(all ? "Reset all app memory" : "Delete saved history", () => form.requestSubmit(), "button danger", "trash");
    commit.dataset.resetConfirmation = "true";
    commit.disabled = true;
    confirmation.input.addEventListener("input", () => { commit.disabled = state.busy || confirmation.input.value !== "RESET"; });
    actions.append(commit);
    form.append(confirmation.label, actions);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (confirmation.input.value !== "RESET" || state.busy) return;
      mutation(async () => {
        let result;
        try { result = await api("/api/memory/reset", {scope, confirmation: confirmation.input.value}); }
        catch (error) {
          // A filesystem failure can leave a partially completed reset. Refresh
          // its counts before the athlete returns to Settings to review it.
          try { await refreshState(); } catch { /* Preserve the reset's actual error. */ }
          throw error;
        }
        showWarnings(result);
        state.session = null;
        state.references = new Set();
        state.context = null;
        state.pending = "";
        state.unsaved = false;
        state.drafts.clear();
        $("message-input").value = "";
        $("plan-kind").value = "";
        await refreshState();
        renderMessages(true);
        resizeComposer();
        closeModal(true);
        notice(all ? "App memory reset. You can add a new athlete profile and guides for your next learning path." : "Saved history cleared. Your profile and PDF knowledge are ready for a fresh conversation.", false, true);
      });
    });
    body.append(form);
    setTimeout(() => confirmation.input.focus(), 0);
  }
  function showHelp() {
    const body = showModal("Chat shortcuts", "help", "KEEP THE CONVERSATION MOVING");
    if (!body) return;
    body.append(introduction("Type naturally, paste a whole training week, or use a shortcut. Your browser composer supports multiple lines without running terminal commands."));
    const list = node("div", "command-list");
    for (const [command, description] of [
      ["/workout [request]", "Create a workout and save a separate plan."],
      ["/training [request]", "Create and save a multiweek training plan."],
      ["/nutrition [request]", "Create and save CrossFit fueling goals."],
      ["/competition [request]", "Create and save competition preparation."],
      ["/new · /quit", "Return to a fresh conversation; saved history stays."],
      ["/clearrefs", "Remove attached saved references from this conversation."],
      ["/context", "See which profile, guides, and references apply."],
      ["/file [path]", "Import a training file, or open the file picker."],
      ["/learn [path]", "Add a PDF or folder, or open the PDF library."],
      ["/read ID", "Attach a saved entry by its ID."],
      ["/profile", "Edit the athlete profile."],
      ["/history", "Browse saved plans and files."],
      ["/settings", "Manage context preferences and reset memory."],
      ["/help", "Show these shortcuts."],
    ]) { list.append(node("code", "", command), node("p", "", description)); }
    body.append(list, help("The four plan commands call both coaches in live mode and use your API budget. Navigation, saved-entry tools, imports, preferences, and memory controls do not call the coaching APIs."));
  }
  async function handleCommand(message) {
    const match = /^\/\s*([a-z]+)(?:\s+([\s\S]*))?$/i.exec(message);
    if (!match) return false;
    const command = match[1].toLowerCase();
    const argument = match[2]?.trim() || "";
    if (Object.hasOwn(DEFAULT_REQUESTS, command)) {
      if (["--help", "-h", "/help"].includes(argument)) { $("message-input").value = ""; resizeComposer(); showHelp(); return true; }
      return false;
    }
    const known = ["new", "quit", "back", "exit", "help", "profile", "history", "file", "readfile", "learn", "library", "sources", "context", "clearrefs", "read", "settings", "view"];
    if (!known.includes(command)) { notice(`Unknown shortcut /${command}. Use /help to see the available commands. This was not sent to either coach.`, true); return true; }
    $("message-input").value = "";
    state.drafts.delete(currentDraftKey());
    resizeComposer();
    if (["new", "quit", "back", "exit"].includes(command)) freshChat();
    else if (command === "help") showHelp();
    else if (command === "profile") showProfile();
    else if (command === "history") showFiles();
    else if (command === "settings") showSettings();
    else if (["context", "sources"].includes(command)) showContext();
    else if (command === "library") showLibrary();
    else if (["file", "readfile"].includes(command)) {
      if (argument) await importPath(argument); else showFiles();
    } else if (command === "learn") {
      if (!argument) showLibrary();
      else await mutation(async () => { const result = await api("/api/library", {path: argument}); showWarnings(result); await refreshState(); notice("PDF knowledge updated locally."); });
    } else if (command === "clearrefs") await mutation(async () => {
      await setReferences(new Set());
      notice("Saved references removed. This conversation, your profile, and PDF library still apply.");
    });
    else if (command === "read") {
      if (!/^[a-f0-9]{12,32}$/.test(argument)) notice("Use /read followed by the saved entry's full ID, or choose a reference using the button above the composer.", true);
      else await mutation(async () => { const refs = new Set(state.references); refs.add(argument); await setReferences(refs); notice("Saved entry attached as a reference."); });
    } else if (command === "view") $("message-list").lastElementChild?.scrollIntoView({block: "start", behavior: "smooth"});
    return true;
  }

  for (const placeholder of document.querySelectorAll("[data-icon]")) placeholder.replaceChildren(...icon(placeholder.dataset.icon).childNodes);
  $("new-chat").addEventListener("click", freshChat);
  document.querySelector(".brand").addEventListener("click", (event) => { event.preventDefault(); if (!state.busy) freshChat(); });
  $("tool-profile").addEventListener("click", showProfile);
  $("tool-files").addEventListener("click", showFiles);
  $("tool-library").addEventListener("click", showLibrary);
  $("tool-settings").addEventListener("click", showSettings);
  $("show-context").addEventListener("click", showContext);
  $("references-button").addEventListener("click", showReferences);
  $("attach-file").addEventListener("click", showFiles);
  $("history-search").addEventListener("input", renderSidebar);
  $("mobile-menu").addEventListener("click", () => {
    const open = !$("sidebar").classList.contains("open");
    $("sidebar").classList.toggle("open", open);
    $("sidebar-scrim").hidden = !open;
    $("mobile-menu").setAttribute("aria-expanded", String(open));
    if (open) $("new-chat").focus();
  });
  $("sidebar-scrim").addEventListener("click", closeSidebar);
  $("modal-close").addEventListener("click", () => closeModal());
  $("modal").addEventListener("cancel", (event) => { if (state.busy) event.preventDefault(); });
  $("modal").addEventListener("close", () => { state.modal = null; if (previousFocus?.isConnected) previousFocus.focus(); });
  $("modal").addEventListener("click", (event) => {
    if (event.target !== $("modal") || state.busy) return;
    const rect = $("modal").getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeModal();
  });
  $("composer-form").addEventListener("submit", submitMessage);
  $("message-input").addEventListener("input", () => { resizeComposer(); rememberDraft(); });
  $("message-input").addEventListener("compositionstart", () => { composing = true; });
  $("message-input").addEventListener("compositionend", () => { composing = false; });
  $("message-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !composing && event.keyCode !== 229) { event.preventDefault(); submitMessage(event); }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("sidebar").classList.contains("open")) closeSidebar();
    if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === "o") { event.preventDefault(); freshChat(); }
  });
  for (const suggestion of document.querySelectorAll("[data-prompt]")) suggestion.addEventListener("click", () => { if (state.busy) return; $("message-input").value = suggestion.dataset.prompt; resizeComposer(); rememberDraft(); $("message-input").focus(); });
  window.addEventListener("beforeunload", (event) => { if (state.generating || state.unsaved) { event.preventDefault(); event.returnValue = ""; } });
  // Stop the browser navigating to a dropped file outside the import target.
  document.addEventListener("dragover", (event) => { if (event.dataTransfer?.types.includes("Files")) event.preventDefault(); });
  document.addEventListener("drop", (event) => { if (event.dataTransfer?.types.includes("Files")) event.preventDefault(); });

  async function initialize() {
    setBusy(true);
    try {
      if (!token || token === "{{CSRF_TOKEN}}") throw new Error("Open this page using the URL printed by gamescoach gui. The local session token is missing.");
      await refreshState(true);
      renderMessages();
    } catch (error) {
      $("welcome-context").textContent = "Your training space could not be loaded.";
      notice(error.message, true, true);
    } finally { setBusy(false); resizeComposer(); }
  }
  initialize();
})();
