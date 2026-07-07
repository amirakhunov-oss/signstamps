(function () {
  function parseJson(value) {
    try {
      return JSON.parse(value || "[]");
    } catch {
      return [];
    }
  }

  function optionList(values) {
    return values.map((value) => {
      const option = document.createElement("option");
      option.value = value;
      return option;
    });
  }

  function makeDatalist(className, values) {
    const datalist = document.createElement("datalist");
    datalist.className = className;
    datalist.append(...optionList(values));
    return datalist;
  }

  function makeExpectedColumn(title, kind, buttonText) {
    const column = document.createElement("div");
    const heading = document.createElement("h3");
    const list = document.createElement("div");
    const button = document.createElement("button");

    heading.textContent = title;
    list.className = "expected-list";
    list.dataset.kind = kind;
    button.type = "button";
    button.className = "secondary add-expected";
    button.dataset.kind = kind;
    button.textContent = buttonText;

    column.append(heading, list, button);
    return column;
  }

  function makeExpectedEditor(organizations, signers) {
    const editor = document.createElement("div");
    editor.className = "expected-editor";
    editor.dataset.organizations = "[]";
    editor.dataset.signers = "[]";

    const input = document.createElement("input");
    input.type = "hidden";
    input.className = "expected-json";
    input.value = '{"organizations":[],"signers":[]}';

    const columns = document.createElement("div");
    columns.className = "expected-columns";
    columns.append(
      makeExpectedColumn("Ожидаемые печати", "organizations", "+ Печать организации"),
      makeExpectedColumn("Ожидаемые подписи", "signers", "+ Подпись человека"),
    );

    editor.append(
      input,
      makeDatalist("organization-options", organizations),
      makeDatalist("signer-options", signers),
      columns,
    );
    return editor;
  }

  function fileSize(bytes) {
    if (!bytes) return "";
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function renderFiles(form) {
    const input = form.querySelector("[data-pdf-files]");
    const list = form.querySelector("[data-batch-files]");
    const organizations = parseJson(form.dataset.organizations);
    const signers = parseJson(form.dataset.signers);
    const files = Array.from(input.files || []);

    list.innerHTML = "";
    list.hidden = files.length === 0;

    files.forEach((file, index) => {
      const item = document.createElement("article");
      item.className = "batch-file";

      const head = document.createElement("div");
      head.className = "batch-file-head";

      const number = document.createElement("span");
      number.className = "batch-file-number";
      number.textContent = String(index + 1);

      const name = document.createElement("div");
      name.className = "batch-file-name";
      name.textContent = file.name;

      const meta = document.createElement("span");
      meta.className = "muted";
      meta.textContent = fileSize(file.size);

      head.append(number, name, meta);

      const details = document.createElement("details");
      details.className = "batch-expected";

      const summary = document.createElement("summary");
      summary.textContent = "Ожидаемые печати и подписи";
      details.append(summary, makeExpectedEditor(organizations, signers));

      item.append(head, details);
      list.appendChild(item);

      if (window.initExpectedEditor) {
        window.initExpectedEditor(details.querySelector(".expected-editor"));
      }
    });
  }

  function syncBeforeSubmit(form) {
    const values = Array.from(form.querySelectorAll(".batch-file .expected-json")).map((input) => {
      try {
        return JSON.parse(input.value || "{}");
      } catch {
        return { organizations: [], signers: [] };
      }
    });
    form.querySelector("[data-expected-jsons]").value = JSON.stringify(values);
  }

  document.querySelectorAll("[data-batch-upload]").forEach((form) => {
    const input = form.querySelector("[data-pdf-files]");
    input.addEventListener("change", () => renderFiles(form));
    form.addEventListener("submit", () => syncBeforeSubmit(form));
    renderFiles(form);
  });
})();
