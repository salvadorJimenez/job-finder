// Keep the list filters while moving around the app, until this tab is closed:
// the list saves its query per tab, and links back to it (logo, Jobs) reuse it.
// Reset links forget it. The selected job (?job=) is not a filter, so it is left out:
// coming back to the list never reopens (and marks as seen) a job you did not just pick.
(function () {
  const KEY = "job-finder:list-query";
  try {
    if (window.location.pathname === "/") {
      const here = new URL(window.location.href);
      here.searchParams.delete("job");
      sessionStorage.setItem(KEY, here.search);
    }
    const saved = sessionStorage.getItem(KEY);
    if (saved) {
      document.querySelectorAll("a[data-keep-filters]").forEach(function (a) { a.href = "/" + saved; });
    }
    document.addEventListener("click", function (event) {
      if (event.target.closest && event.target.closest("a[data-reset-filters]")) sessionStorage.removeItem(KEY);
    });
  } catch (e) { /* storage disabled: links simply go to the unfiltered list */ }
})();

// Split view on the home list (screens at least 1100px wide): clicking a job loads its description
// and actions into the pane on the right without leaving the page. The pane is the server's own
// fragment (/jobs/<id>?pane=1, which also marks the job as seen), the address becomes ?job=<id> with
// every filter kept (back and forward restore the selection), and the row's unread marker is cleared
// in place. j / k or the arrow keys move through the list, Escape closes the pane. Below 1100px, or with
// a modifier key (open in a new tab), the links work as plain links to the full page.
(function () {
  const split = document.getElementById("split");
  const pane = document.getElementById("job-pane");
  if (!split || !pane) return;
  const list = split.querySelector(".split-list");
  const wide = window.matchMedia("(min-width: 1100px)");
  let ticket = 0;

  function currentJob() {
    const id = new URL(window.location.href).searchParams.get("job");
    return id && /^\d+$/.test(id) ? id : null;
  }
  function listUrl(id) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("job", id); else url.searchParams.delete("job");
    return url.pathname + url.search;
  }
  function rowsOf(id) {
    return Array.from(list.querySelectorAll('a[data-job-id="' + id + '"]'))
      .map(function (a) { return a.closest("li.job, li.member"); })
      .filter(Boolean);
  }
  function markSelected(id) {
    list.querySelectorAll("li.selected").forEach(function (li) {
      li.classList.remove("selected");
      li.removeAttribute("aria-current");
    });
    if (!id) return;
    rowsOf(id).forEach(function (li) {
      li.classList.add("selected");
      li.setAttribute("aria-current", "true");
      const group = li.closest("details.dups");
      if (group) group.open = true;
    });
  }

  // The server marked the job as seen: show it in the list too (status class, dot, NEW tag, select, group count).
  function refreshRows(id, status) {
    rowsOf(id).forEach(function (li) {
      li.className = li.className.replace(/\bstatus-\w+/, "status-" + status);
      const head = li.querySelector(".title-line");
      if (status !== "new" && head) head.querySelectorAll(".unread-dot, .tag.new").forEach(function (el) { el.remove(); });
      const select = li.querySelector("form.status select");
      if (select) select.value = status;
      const group = li.closest("li.job");
      if (!group) return;
      const unread = group.querySelectorAll("li.member.status-new").length;
      const own = group.classList.contains("status-new");
      group.classList.toggle("group-unread", !own && unread > 0);
      const groupHead = group.querySelector(".main .title-line");
      if (!own && unread === 0 && groupHead) groupHead.querySelectorAll(".unread-dot").forEach(function (el) { el.remove(); });
      const summary = group.querySelector("details.dups > summary");
      const counter = summary && summary.querySelector(".dups-unread");
      if (counter) { if (unread) counter.textContent = " · " + unread + " unread"; else counter.remove(); }
    });
  }

  // With the filters above the split the pane can start below the fold: bring it up under the top edge
  // (it sticks there while the list scrolls), so a click always shows the description.
  function bringPaneIntoView() {
    const top = pane.getBoundingClientRect().top;
    if (top > 24) window.scrollBy(0, top - 16);
  }

  function history_(mode, id) {
    const url = listUrl(id);
    if (mode === "push") window.history.pushState({ job: id }, "", url);
    else if (mode === "replace") window.history.replaceState({ job: id }, "", url);
  }

  async function show(id, mode) {
    const mine = ++ticket;
    markSelected(id);
    pane.setAttribute("aria-busy", "true");
    let html;
    try {
      const response = await fetch("/jobs/" + id + "?pane=1&back=" + encodeURIComponent(listUrl(null)), { credentials: "same-origin" });
      if (!response.ok) throw new Error("pane " + response.status);
      html = await response.text();
    } catch (e) {
      if (mine === ticket) window.location.assign(listUrl(id));   // the server-rendered page shows the same pane
      return;
    }
    if (mine !== ticket) return;                                    // a newer click won
    pane.innerHTML = html;
    pane.scrollTop = 0;
    pane.removeAttribute("aria-busy");
    history_(mode, id);
    if (mode === "push") bringPaneIntoView();
    const view = pane.querySelector(".job-view");
    if (view) {
      refreshRows(id, view.getAttribute("data-status"));
      const title = view.querySelector(".detail-title");
      if (title) document.title = title.textContent + " · job_finder";
    }
  }

  function clear(mode) {
    ++ticket;
    markSelected(null);
    pane.removeAttribute("aria-busy");
    const empty = document.getElementById("pane-empty-tpl");
    pane.innerHTML = empty ? empty.innerHTML : "";
    pane.scrollTop = 0;
    history_(mode, null);
    document.title = "job_finder";
  }

  // Arriving from a link to ?job=<id> (not a reload or Back): show the pane, not the filters above it.
  try {
    const nav = performance.getEntriesByType("navigation")[0];
    if (wide.matches && currentJob() && nav && nav.type === "navigate") bringPaneIntoView();
  } catch (e) { /* no navigation timing: the page stays where it is */ }

  document.addEventListener("click", function (event) {
    if (!wide.matches || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || !event.target.closest) return;
    if (event.target.closest("a[data-pane-close]")) {
      event.preventDefault();
      clear("push");
      return;
    }
    const link = event.target.closest("a[data-job-id]");
    if (!link || !split.contains(link)) return;
    event.preventDefault();
    const id = link.getAttribute("data-job-id");
    if (id !== currentJob()) show(id, "push");
  });

  window.addEventListener("popstate", function () {
    const id = currentJob();
    if (id) show(id, "none"); else clear("none");
  });

  document.addEventListener("keydown", function (event) {
    if (!wide.matches || event.ctrlKey || event.metaKey || event.altKey || !event.target.closest) return;
    const typing = event.target.closest("input, textarea, select, [contenteditable]");
    if (event.key === "Escape") {
      if (typing || event.target.closest("details[data-menu]") || !currentJob()) return;
      event.preventDefault();
      clear("push");
      return;
    }
    const down = event.key === "j" || event.key === "ArrowDown";
    const up = event.key === "k" || event.key === "ArrowUp";
    if ((!down && !up) || typing || !event.target.closest(".split-list")) return;
    const links = Array.from(list.querySelectorAll("a[data-job-id]")).filter(function (a) { return a.offsetParent !== null; });
    if (!links.length) return;
    const at = links.findIndex(function (a) { return a.getAttribute("data-job-id") === currentJob(); });
    const next = links[at < 0 ? (down ? 0 : links.length - 1) : Math.min(Math.max(at + (down ? 1 : -1), 0), links.length - 1)];
    event.preventDefault();
    next.focus();
    const id = next.getAttribute("data-job-id");
    if (id !== currentJob()) show(id, currentJob() ? "replace" : "push");
  });
})();

// The same for the applications page: its query holds the view (board or list), the filters and the
// sort, saved per tab, and the links back to it (header "Applications", "Back to board") reuse it.
// Reset goes to the page without filters (but in the same view), which saves that clean query.
(function () {
  const KEY = "job-finder:apps-query";
  try {
    if (window.location.pathname === "/applications") sessionStorage.setItem(KEY, window.location.search);
    const saved = sessionStorage.getItem(KEY);
    if (saved) {
      document.querySelectorAll("a[data-keep-apps]").forEach(function (a) { a.href = "/applications" + saved; });
    }
    document.addEventListener("click", function (event) {
      if (event.target.closest && event.target.closest("a[data-reset-apps]")) sessionStorage.removeItem(KEY);
    });
  } catch (e) { /* storage disabled: links simply go to the board */ }
})();

// Filters apply as soon as they change. Text fields and stack chips wait for a short
// pause (so several chips can be picked in a row), and focus and caret are restored
// after the page reloads. Repeated params (?stack=kotlin&stack=rust) are kept as they are.
// The form says where it goes (the jobs list or the applications page) in its action.
(function () {
  const form = document.querySelector("form[data-autosubmit]");
  if (!form) return;
  const FOCUS_KEY = "job-finder:filter-focus";
  let timer = null;

  function apply(focused) {
    clearTimeout(timer);
    if (focused && focused.name) {
      sessionStorage.setItem(FOCUS_KEY, JSON.stringify({
        name: focused.name,
        value: focused.type === "checkbox" || focused.type === "radio" ? focused.value : null,   // which chip, among those named "stack"
        menu: focused.closest && focused.closest("details[data-menu]") ? focused.closest("details[data-menu]").getAttribute("data-menu") : null,
        caret: typeof focused.selectionStart === "number" ? focused.selectionStart : null,
      }));
    }
    const params = new URLSearchParams();
    new FormData(form).forEach(function (value, key) {
      if (value !== "") params.append(key, value);   // keep the URL clean; page resets to 1
    });
    const query = params.toString();
    window.location.assign((form.getAttribute("action") || "/") + (query ? "?" + query : ""));
  }

  const DEBOUNCED = 'input[type="search"], input[name="stack"], input[name="mode"], input[name="outcome"], input[name="phase"]';
  form.addEventListener("change", function (event) {
    if (event.target.matches(DEBOUNCED)) return; // handled by input
    apply(event.target);
  });
  form.addEventListener("input", function (event) {
    if (!event.target.matches(DEBOUNCED)) return;
    clearTimeout(timer);
    timer = setTimeout(function () { apply(event.target); }, 500);
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    apply(document.activeElement);
  });

  const saved = sessionStorage.getItem(FOCUS_KEY);
  if (saved) {
    sessionStorage.removeItem(FOCUS_KEY);
    const state = JSON.parse(saved);
    if (state.menu) {                       // the dropdown you were using stays open while you pick the next value
      const menu = form.querySelector('details[data-menu="' + state.menu + '"]');
      if (menu) menu.open = true;
    }
    const field = state.value !== null && state.value !== undefined
      ? Array.from(form.elements).find(function (el) { return el.name === state.name && el.value === state.value; })
      : form.elements.namedItem(state.name);
    if (field && field.focus) {
      field.focus();
      if (state.caret !== null && typeof field.setSelectionRange === "function") {
        try { field.setSelectionRange(state.caret, state.caret); } catch (e) { /* not a text field */ }
      }
    }
  }
})();

// Applications filter bar: the dropdowns are native <details>, so they open and close without
// JavaScript. This adds what <details> lacks: one open at a time, Escape (focus goes back to the
// button) and a click outside close it, a menu that would run off the right edge opens leftwards,
// and "/" jumps to the search field from anywhere that is not a text field.
(function () {
  const bar = document.querySelector("form.app-filters");
  if (!bar) return;
  const menus = Array.from(bar.querySelectorAll("details[data-menu]"));
  function closeAll(except) {
    menus.forEach(function (menu) { if (menu !== except && menu.open) menu.open = false; });
  }
  menus.forEach(function (menu) {
    menu.addEventListener("toggle", function () {
      if (!menu.open) return;
      closeAll(menu);
      const pop = menu.querySelector(".fpop");
      if (!pop) return;
      pop.classList.remove("fpop-right");
      if (pop.getBoundingClientRect().right > document.documentElement.clientWidth - 8) pop.classList.add("fpop-right");
    });
  });
  document.addEventListener("click", function (event) {
    if (!event.target.closest || !event.target.closest("details[data-menu]")) closeAll(null);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      const open = menus.find(function (menu) { return menu.open; });
      if (open) { open.open = false; open.querySelector("summary").focus(); }
    } else if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey) {
      if (event.target.closest && event.target.closest("input, textarea, select, [contenteditable]")) return;
      const q = bar.querySelector('input[name="q"]');
      if (q) { event.preventDefault(); q.focus(); q.select(); }
    }
  });
})();

// Activity calendar: the section stays open or closed as you left it (default: open, closed on a phone),
// the grid starts scrolled to the newest week, and each day shows its text in a tooltip on hover or focus
// (the title attribute stays as the no-JavaScript fallback and is swapped for the tooltip).
(function () {
  const details = document.querySelector("details.activity");
  if (!details) return;
  const KEY = "job-finder:activity-open";
  const scroller = details.querySelector(".cal-scroll");
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "0") details.open = false;
    else if (saved === "1") details.open = true;
    else if (window.matchMedia("(max-width: 720px)").matches) details.open = false;
  } catch (e) { /* storage disabled: the default stands */ }
  details.querySelector("summary").addEventListener("click", function () {
    setTimeout(function () {
      try { localStorage.setItem(KEY, details.open ? "1" : "0"); } catch (e) { /* not saved */ }
      if (details.open) toEnd();
    }, 0);
  });
  function toEnd() { if (scroller) scroller.scrollLeft = scroller.scrollWidth; }
  toEnd();

  const tip = document.createElement("div");
  tip.className = "cal-tip";
  tip.setAttribute("aria-hidden", "true");
  tip.hidden = true;
  document.body.appendChild(tip);
  details.querySelectorAll(".cal-cell[title]").forEach(function (cell) {
    cell.setAttribute("data-tip", cell.getAttribute("title"));
    cell.removeAttribute("title");
  });
  function show(cell) {
    tip.textContent = cell.getAttribute("data-tip");
    tip.hidden = false;
    const r = cell.getBoundingClientRect();
    const left = Math.min(Math.max(8, r.left + r.width / 2 - tip.offsetWidth / 2), document.documentElement.clientWidth - tip.offsetWidth - 8);
    const top = r.top - tip.offsetHeight - 8;
    tip.style.left = left + "px";
    tip.style.top = (top < 8 ? r.bottom + 8 : top) + "px";
  }
  function hide() { tip.hidden = true; }
  details.addEventListener("mouseover", function (event) {
    const cell = event.target.closest && event.target.closest(".cal-cell[data-tip]");
    if (cell) show(cell); else hide();
  });
  details.addEventListener("mouseleave", hide);
  details.addEventListener("focusin", function (event) {
    const cell = event.target.closest && event.target.closest(".cal-cell[data-tip]");
    if (cell) show(cell);
  });
  details.addEventListener("focusout", hide);
  if (scroller) scroller.addEventListener("scroll", hide);
})();

// Search panel: SEARCH button, inline confirm when today was already searched, and
// progress polling. While a search runs the filters, export, pinned and job list are
// made inert (no clicks, no focus, hidden from assistive tech) and an overlay shows the
// step counter and a progress bar; the header nav stays usable. When it finishes the
// page reloads and the panel shows the outcome stored in the database.
(function () {
  const btn = document.getElementById("search-btn");
  if (!btn) return;
  const area = document.getElementById("results-area");
  const overlay = document.getElementById("search-overlay");
  const card = overlay.querySelector(".overlay-card");
  const step = document.getElementById("search-step");
  const detail = document.getElementById("search-progress-text");
  const meter = document.getElementById("search-meter");
  const done = meter.querySelector(".meter-done");
  const active = meter.querySelector(".meter-active");
  const last = document.getElementById("search-last");
  const running = document.getElementById("search-running");
  const outcomeBox = document.getElementById("search-outcome-box");
  const message = document.getElementById("search-message");
  const idle = document.getElementById("search-idle");
  const confirmBox = document.getElementById("search-confirm");
  const confirmText = document.getElementById("search-confirm-text");
  const confirmYes = document.getElementById("search-confirm-yes");
  const confirmNo = document.getElementById("search-confirm-no");
  const live = document.getElementById("search-live");
  let announced = "";

  function say(text) {
    if (text === announced) return;     // polite region: only announce changes
    announced = text;
    live.textContent = text;
  }

  function showMessage(text) {
    message.textContent = text;
    message.hidden = !text;
  }

  function setBlocked(blocked) {
    area.inert = blocked;
    if (blocked) area.setAttribute("aria-busy", "true");
    else area.removeAttribute("aria-busy");
    overlay.hidden = !blocked;
    running.hidden = !blocked;
    last.hidden = blocked;
    if (outcomeBox) outcomeBox.hidden = blocked;
    if (blocked) btn.setAttribute("aria-busy", "true");
    else btn.removeAttribute("aria-busy");
  }

  function showConfirm(show, whenText) {
    if (whenText) confirmText.textContent = "You already searched today at " + whenText + ". Search again? It only covers what was posted since then.";
    confirmBox.hidden = !show;
    idle.hidden = show;
    if (show) confirmText.focus();      // not a button: a held Enter/Space cannot confirm by accident
    else btn.focus();
  }

  function render(data) {
    const total = data.total || 0;
    const current = Math.min(data.step || 0, total);
    if (total && current) {
      step.textContent = current + " / " + total;
      detail.textContent = (data.progress || "").replace(": ", " · ");
      meter.classList.remove("indeterminate");
      done.style.width = ((current - 1) / total * 100) + "%";
      active.style.width = (100 / total) + "%";
      meter.setAttribute("aria-valuemax", total);
      meter.setAttribute("aria-valuenow", current - 1);
      say("Searching, step " + current + " of " + total);
    } else {
      step.textContent = "starting…";
      meter.classList.add("indeterminate");
      done.style.width = "0";
      active.style.width = "";
    }
  }

  async function start(confirmed) {
    const body = new URLSearchParams();
    if (confirmed) body.set("confirm", "1");
    showMessage("");
    btn.disabled = true;             // no double clicks while the request is in flight
    let res;
    try {
      res = await fetch("/search", { method: "POST", body });
    } catch (e) {
      btn.disabled = false;
      showMessage("Could not reach the server. Is job-finder still running?");
      return;
    }
    if (res.status === 409) {
      const data = await res.json();
      if (data.reason === "already_searched_today") {
        btn.disabled = false;
        showConfirm(true, String(data.last_searched || "").split(" ")[1]);
        return;
      }
      if (data.reason === "running") return poll(true);
    }
    if (res.ok) {
      say("Search started");
      poll(true);
    } else {
      btn.disabled = false;
      let message = "Could not start the search (error " + res.status + ").";
      try { const data = await res.json(); if (data.message) message = data.message; } catch (e) { /* not JSON */ }
      showMessage(message);
    }
  }

  async function poll(first) {
    btn.disabled = true;
    btn.textContent = "SEARCHING…";
    if (first) {
      setBlocked(true);
      card.focus();                  // never leave focus behind inert content or on the disabled button
    }
    let data;
    try {
      data = await (await fetch("/search/status")).json();
    } catch (e) {
      detail.textContent = "lost connection, retrying…";
      setTimeout(poll, 2000);
      return;
    }
    if (data.running) {
      render(data);
      setTimeout(poll, 2000);
    } else {
      window.location.reload();
    }
  }

  btn.addEventListener("click", function () {
    if (btn.dataset.searchedToday) showConfirm(true);
    else start(false);
  });
  confirmYes.addEventListener("click", function () {
    showConfirm(false);
    start(true);
  });
  confirmNo.addEventListener("click", function () { showConfirm(false); });
  confirmBox.addEventListener("keydown", function (event) {
    if (event.key === "Escape") showConfirm(false);
  });

  if (btn.disabled) {           // the page was loaded while a search was running
    setBlocked(true);
    poll(false);
  }
})();

// Kanban board: drag a card onto a phase column to move it. Closed and
// Unassigned columns have no data-phase-id, so they are never drop targets.
// Empty columns are collapsed strips; while dragging they open up (CSS, .is-dragging)
// so they are easy to hit.
(function () {
  const board = document.getElementById("board");
  if (!board) return;
  let dragged = null;

  function column(event) {
    return event.target.closest(".column[data-phase-id]");
  }

  function clearTargets() {
    board.querySelectorAll(".drop-target").forEach(function (el) { el.classList.remove("drop-target"); });
  }

  board.addEventListener("dragstart", function (event) {
    const card = event.target.closest ? event.target.closest(".card[draggable]") : null;
    if (!card) return;
    dragged = card;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.appId);
    card.classList.add("dragging");
    // after the browser has taken its drag image: changing the layout inside dragstart can cancel the drag
    setTimeout(function () { if (dragged) board.classList.add("is-dragging"); }, 0);
  });

  board.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("dragging");
    dragged = null;
    board.classList.remove("is-dragging");
    clearTargets();
  });

  board.addEventListener("dragover", function (event) {
    const col = column(event);
    if (!dragged || !col) return;
    event.preventDefault();               // allow the drop
    event.dataTransfer.dropEffect = "move";
    if (col.classList.contains("drop-target")) return;
    clearTargets();
    col.classList.add("drop-target");
  });

  board.addEventListener("drop", async function (event) {
    const col = column(event);
    if (!dragged || !col) return;
    event.preventDefault();
    const appId = dragged.dataset.appId;
    const sameColumn = dragged.dataset.phaseId === col.dataset.phaseId;
    clearTargets();
    board.classList.remove("is-dragging");
    if (sameColumn) return;
    const body = new URLSearchParams({ phase_id: col.dataset.phaseId, next: "/applications" });
    const res = await fetch("/applications/" + appId + "/move", { method: "POST", body });
    if (res.ok) window.location.reload();
    else window.alert("Could not move the application.");
  });
})();

// Application page, timeline: the composer opens with focus in the notes and closes with CANCEL or
// Escape (CANCEL is hidden without JS, where the summary toggles it), notes boxes grow with their text,
// and DELETE asks for a second click. Everything is a plain form POST without this script.
(function () {
  const composer = document.querySelector("details.composer");
  if (!composer && !document.querySelector("details.entry-edit")) return;

  function grow(box) {
    box.style.height = "auto";
    box.style.height = (box.scrollHeight + 2) + "px";
  }
  document.addEventListener("input", function (event) {
    if (event.target.matches && event.target.matches("textarea[data-autogrow]")) grow(event.target);
  });
  document.addEventListener("toggle", function (event) {
    const box = event.target.querySelector && event.target.querySelector("textarea[data-autogrow]");
    if (box && event.target.open) grow(box);
  }, true);

  function focusNotes(details) {
    setTimeout(function () {
      const box = details.querySelector("textarea");
      if (details.open && box) box.focus();
    }, 0);
  }
  document.querySelectorAll("details.composer > summary, details.entry-edit > summary").forEach(function (summary) {
    summary.addEventListener("click", function () { if (!summary.parentElement.open) focusNotes(summary.parentElement); });
  });

  function close(details) {
    details.open = false;
    const form = details.querySelector("form");
    if (details.classList.contains("composer") && form) form.reset();
    details.querySelector("summary").focus();
  }
  if (composer) {
    const cancel = composer.querySelector("[data-composer-cancel]");
    if (cancel) {
      cancel.hidden = false;
      cancel.addEventListener("click", function () { close(composer); });
    }
  }
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    const details = event.target.closest && event.target.closest("details.composer[open], details.entry-edit[open]");
    if (details) close(details);
  });

  // DELETE: the first click arms the button ("CONFIRM?"), a second one within a few seconds submits.
  let armed = null, timer = null;
  function disarm() {
    clearTimeout(timer);
    if (!armed) return;
    armed.textContent = armed.dataset.label;
    armed.setAttribute("aria-label", armed.dataset.ariaLabel);
    armed.removeAttribute("data-armed");
    armed = null;
  }
  document.addEventListener("click", function (event) {
    const btn = event.target.closest && event.target.closest("button[data-confirm]");
    if (!btn) return disarm();
    if (btn === armed) return;                     // second click: let the form submit
    disarm();
    event.preventDefault();
    armed = btn;
    btn.dataset.label = btn.textContent;
    btn.dataset.ariaLabel = btn.getAttribute("aria-label");
    btn.setAttribute("data-armed", "");
    btn.textContent = "CONFIRM?";
    btn.setAttribute("aria-label", "Confirm: " + btn.dataset.ariaLabel);
    timer = setTimeout(disarm, 4000);
  });
  document.addEventListener("focusout", function (event) {
    if (event.target === armed) disarm();
  });
})();

// Application page, CV viewer: the thumbnail, the file name and OPEN (all [data-cv-open]) open the PDF in a
// modal <dialog>. The big frame gets its src only while the dialog is open (so the PDF is not loaded twice up
// front) and loses it on close. They stay plain target=_blank links, so without this script, or with a
// ctrl/cmd/shift/middle click, the PDF still opens in a new tab. Escape and a click on the backdrop close it;
// focus goes back to whatever opened it.
(function () {
  const dialog = document.querySelector("[data-cv-modal]");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const frame = dialog.querySelector("iframe");
  let trigger = null;

  function open(from) {
    trigger = from;
    frame.setAttribute("src", frame.dataset.src);
    document.documentElement.classList.add("cv-modal-open");
    dialog.showModal();
    const close = dialog.querySelector("[data-cv-close]");
    if (close) close.focus();
  }

  document.querySelectorAll("[data-cv-open]").forEach(function (el) {
    el.addEventListener("click", function (event) {
      if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      open(el);
    });
  });
  dialog.querySelector("[data-cv-close]").addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) dialog.close();  // the dialog has no padding: only the backdrop is "the dialog itself"
  });
  dialog.addEventListener("close", function () {  // Escape, CLOSE and the backdrop all end here
    frame.removeAttribute("src");
    document.documentElement.classList.remove("cv-modal-open");
    if (trigger) trigger.focus();
    trigger = null;
  });
})();

// Application page, CV: drop a file on the drop zone or anywhere on the page (a full-page overlay says
// so while a file is dragged over the window), or pick one with the browse button: it uploads at once
// with fetch to the same POST /applications/<id>/cv the plain form uses, then reloads. The server stays
// the source of truth: type and size are pre-checked here only to answer faster, and on a 400 or 413 the
// server's own message is shown.
(function () {
  const form = document.querySelector("[data-cv-form]");
  if (!form) return;
  const MAX_BYTES = 10 * 1024 * 1024;
  const EXTENSIONS = [".pdf", ".docx"];
  const zone = form.querySelector("[data-dropzone]");
  const input = form.querySelector('input[type="file"]');
  const attach = form.querySelector("[data-attach]");
  const picker = form.querySelector('select[name="cv_id"]');
  const status = document.querySelector("[data-cv-status]");
  const overlay = document.getElementById("drop-overlay");
  let busy = false;

  if (attach) attach.hidden = true;               // choosing a file uploads it; the button is the no-JS fallback

  function say(text, kind) {
    status.textContent = text;
    status.classList.toggle("is-error", kind === "error");
    status.classList.toggle("is-busy", kind === "busy");
  }

  function problem(file) {
    const name = file.name.toLowerCase();
    if (!EXTENSIONS.some(function (ext) { return name.endsWith(ext); })) return "Only .pdf or .docx files can be attached.";
    if (file.size > MAX_BYTES) return "That file is " + (file.size / 1024 / 1024).toFixed(1) + " MB; the limit is 10 MB.";
    if (file.size === 0) return "That file is empty.";
    return "";
  }

  async function serverMessage(res) {
    let text = "";
    try { text = await res.text(); } catch (e) { /* no body */ }
    const alert = new DOMParser().parseFromString(text, "text/html").querySelector('[role="alert"]');
    const message = alert ? alert.textContent.trim() : (text.length < 200 ? text.trim() : "");
    return message || "Could not attach the CV (error " + res.status + ").";
  }

  async function upload(file) {
    if (busy) return;
    const bad = problem(file);
    if (bad) {
      input.value = "";
      say(bad, "error");
      return;
    }
    busy = true;
    zone.classList.add("is-uploading");
    say("Uploading " + file.name + "…", "busy");
    const body = new FormData();
    body.append("file", file);
    let res;
    try {
      res = await fetch(form.action, { method: "POST", body });
    } catch (e) {
      res = null;
    }
    if (res && res.ok) {                          // the redirect after a successful POST ends on the page itself
      say("Attached. Reloading…");
      window.location.reload();
      return;
    }
    busy = false;
    zone.classList.remove("is-uploading");
    input.value = "";
    say(res ? await serverMessage(res) : "Could not reach the server. Is job-finder still running?", "error");
  }

  input.addEventListener("change", function () {
    if (input.files.length) upload(input.files[0]);
  });
  if (picker) picker.addEventListener("change", function () { if (picker.value) form.submit(); });

  function hasFiles(event) {
    return event.dataTransfer && Array.prototype.indexOf.call(event.dataTransfer.types || [], "Files") !== -1;
  }
  let depth = 0;                                  // dragenter/dragleave fire for every child element
  function showOverlay(show) {
    if (!show) depth = 0;
    overlay.hidden = !show;
    if (!show) zone.classList.remove("is-over");
  }
  document.addEventListener("dragenter", function (event) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    depth += 1;
    showOverlay(true);
  });
  document.addEventListener("dragover", function (event) {
    if (!hasFiles(event)) return;
    event.preventDefault();                       // allow the drop
    event.dataTransfer.dropEffect = "copy";
    zone.classList.toggle("is-over", zone.contains(event.target));
  });
  document.addEventListener("dragleave", function (event) {
    if (!hasFiles(event)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) showOverlay(false);
  });
  document.addEventListener("drop", function (event) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    showOverlay(false);
    const files = event.dataTransfer.files;
    if (files.length > 1) say("Drop one file at a time.", "error");
    else if (files.length) upload(files[0]);
  });
  window.addEventListener("dragend", function () { showOverlay(false); });
  document.addEventListener("keydown", function (event) { if (event.key === "Escape") showOverlay(false); });
})();

// COPY FOR RESUME: puts the job's plain-text block (the server's [data-copy-text] textarea next to the
// button) on the clipboard. navigator.clipboard works on localhost (a secure context); otherwise it
// falls back to selecting that textarea and execCommand("copy"). The button says COPIED ✓ for 2 s; if
// both ways fail it says COPY FAILED and opens the textarea with the text selected, ready for Cmd/Ctrl+C.
// Delegated on document, so it also works for a pane loaded with fetch.
(function () {
  const LABEL = "COPY FOR RESUME";
  const timers = new WeakMap();

  function selectText(area) {
    area.focus();
    area.select();
    area.setSelectionRange(0, area.value.length);
  }

  function legacyCopy(area) {
    try {
      selectText(area);
      return document.execCommand("copy");
    } catch (e) { return false; }
  }

  function report(button, status, area, ok) {
    clearTimeout(timers.get(button));
    button.textContent = ok ? "COPIED ✓" : "COPY FAILED";
    if (status) status.textContent = ok ? "Job copied to the clipboard." : "Copy failed. The text is selected: press Cmd+C or Ctrl+C.";
    if (!ok) { area.classList.add("is-open"); selectText(area); }
    else area.classList.remove("is-open");
    timers.set(button, setTimeout(function () {
      button.textContent = LABEL;
      if (status) status.textContent = "";
    }, ok ? 2000 : 6000));
  }

  document.addEventListener("click", function (event) {
    const button = event.target.closest && event.target.closest("[data-copy-job]");
    if (!button) return;
    const box = button.closest(".copy-job");
    const area = box && box.querySelector("[data-copy-text]");
    if (!area) return;
    const status = box.querySelector("[data-copy-status]");
    const text = area.value;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { report(button, status, area, true); },
        function () { report(button, status, area, legacyCopy(area)); });
    } else {
      report(button, status, area, legacyCopy(area));
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".copy-source.is-open").forEach(function (area) { area.classList.remove("is-open"); });
  });
})();

// PIN / UNPIN reload the page, which would jump back to the top. Before the form posts, remember
// the first job row on screen (not the one being pinned, which moves) and where it sits in the
// viewport; after the reload, scroll so that row is back in the same spot. The pinned section
// growing or shrinking above it no longer shifts the list under the pointer. The pane's own scroll
// is kept too. Without a visible row the plain window position is restored.
(function () {
  const KEY = "job-finder:pin-scroll";

  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (!form.matches || !form.matches("form.pin")) return;
    const moving = (form.getAttribute("action").match(/\/jobs\/(\d+)\/pin/) || [])[1];
    const anchor = Array.from(document.querySelectorAll(".split-list a[data-job-id]")).find(function (a) {
      const r = a.getBoundingClientRect();
      return a.dataset.jobId !== moving && r.height > 0 && r.top >= 0 && r.top < window.innerHeight;
    });
    const pane = document.getElementById("job-pane");
    try {
      sessionStorage.setItem(KEY, JSON.stringify({
        path: location.pathname,
        y: window.scrollY,
        anchor: anchor ? anchor.dataset.jobId : null,
        top: anchor ? anchor.getBoundingClientRect().top : null,
        pane: pane ? pane.scrollTop : 0,
      }));
    } catch (e) { /* storage disabled: the page simply reloads at the top */ }
  });

  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(KEY) || "null"); sessionStorage.removeItem(KEY); } catch (e) { return; }
  if (!saved || saved.path !== location.pathname) return;
  const anchor = saved.anchor && document.querySelector('.split-list a[data-job-id="' + saved.anchor + '"]');
  if (anchor && anchor.getBoundingClientRect().height > 0) window.scrollBy(0, anchor.getBoundingClientRect().top - saved.top);
  else window.scrollTo(0, saved.y);
  const pane = document.getElementById("job-pane");
  if (pane) pane.scrollTop = saved.pane;
})();

// Installable app: the service worker only serves the "not running" page when the server is down.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () { /* not installable: the site still works */ });
}
