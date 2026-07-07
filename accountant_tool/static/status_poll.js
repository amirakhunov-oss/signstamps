(function () {
  const script = document.currentScript;
  const mode = script.dataset.mode;
  const terminalStatuses = new Set(["green", "red"]);

  function setStatusClass(el, status) {
    if (!el) return;
    el.className = `status ${status}`;
    el.textContent = status;
  }

  function setRowStatus(row, status) {
    if (!row) return;
    row.dataset.docStatus = status;
    row.classList.remove("green", "red", "processing", "queued");
    row.classList.add(status);
  }

  function pageChip(page) {
    const chip = document.createElement("span");
    chip.className = `page-chip ${page.status}`;
    chip.textContent = page.number;
    return chip;
  }

  async function fetchJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function pollDocument() {
    const documentId = script.dataset.documentId;
    const initialStatus = script.dataset.initialStatus;
    const initialPageCount = Number(script.dataset.pageCount || "0");
    if (terminalStatuses.has(initialStatus) && initialPageCount > 0) return;
    let attempts = 0;
    const timer = setInterval(async () => {
      attempts += 1;
      try {
        const status = await fetchJson(`/documents/${documentId}/status`);
        setStatusClass(document.querySelector("[data-doc-status]"), status.status);
        const reason = document.querySelector("[data-status-reason]");
        if (reason) reason.textContent = status.status_reason;
        if (status.done && status.page_count > 0) {
          clearInterval(timer);
          window.location.reload();
        }
      } catch (error) {
        if (attempts > 20) clearInterval(timer);
      }
      if (attempts > 180) clearInterval(timer);
    }, 2000);
  }

  function updateListRow(status) {
    const row = document.querySelector(`[data-document-row="${status.id}"]`);
    if (!row) return;
    setRowStatus(row, status.status);
    const reason = row.querySelector("[data-status-reason]");
    if (reason) reason.textContent = status.status_reason;
    const chips = row.querySelector("[data-page-chips]");
    if (chips) {
      chips.innerHTML = "";
      if (status.pages.length) {
        status.pages.forEach((page) => chips.appendChild(pageChip(page)));
      } else {
        const empty = document.createElement("span");
        empty.className = "muted";
        empty.textContent = "нет страниц";
        chips.appendChild(empty);
      }
    }
  }

  function pollList() {
    const day = script.dataset.day;
    let attempts = 0;
    const timer = setInterval(async () => {
      attempts += 1;
      try {
        const payload = await fetchJson(`/documents/statuses?day=${encodeURIComponent(day)}`);
        payload.documents.forEach(updateListRow);
        const hasPending = payload.documents.some((doc) => !doc.done);
        if (!hasPending && attempts > 2) clearInterval(timer);
      } catch (error) {
        if (attempts > 20) clearInterval(timer);
      }
      if (attempts > 180) clearInterval(timer);
    }, 3000);
  }

  if (mode === "document") pollDocument();
  if (mode === "list") pollList();
})();
