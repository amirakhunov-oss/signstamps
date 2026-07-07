(function () {
  let editorCounter = 0;

  function parseJsonAttr(element, name) {
    try {
      return JSON.parse(element.dataset[name] || "[]");
    } catch {
      return [];
    }
  }

  function editorState(editor) {
    const state = { organizations: [], signers: [] };
    editor.querySelectorAll(".expected-row").forEach((row) => {
      const kind = row.dataset.kind;
      const value = row.querySelector("input").value.trim();
      if (value && !state[kind].includes(value)) state[kind].push(value);
    });
    return state;
  }

  function sync(editor) {
    const state = editorState(editor);
    const input = editor.querySelector('[name="expected_json"], .expected-json');
    if (input) input.value = JSON.stringify(state);
    ["organizations", "signers"].forEach((kind) => {
      const list = editor.querySelector(`.expected-list[data-kind="${kind}"]`);
      list.classList.toggle("empty", list.querySelectorAll(".expected-row").length === 0);
    });
  }

  function addRow(editor, kind, value) {
    const list = editor.querySelector(`.expected-list[data-kind="${kind}"]`);
    const row = document.createElement("div");
    row.className = "expected-row";
    row.dataset.kind = kind;

    const input = document.createElement("input");
    input.type = "text";
    input.value = value || "";
    input.placeholder = kind === "organizations" ? "Организация" : "ФИО подписанта";
    input.setAttribute("list", kind === "organizations" ? editor.dataset.orgListId : editor.dataset.signerListId);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-button";
    remove.title = "Удалить";
    remove.textContent = "×";

    row.appendChild(input);
    row.appendChild(remove);
    list.appendChild(row);

    input.addEventListener("input", () => sync(editor));
    remove.addEventListener("click", () => {
      row.remove();
      sync(editor);
    });
    sync(editor);
    input.focus();
  }

  function initExpectedEditor(editor) {
    if (!editor || editor.dataset.expectedReady === "1") return;
    editor.dataset.expectedReady = "1";

    const orgDatalist = editor.querySelector(".organization-options");
    const signerDatalist = editor.querySelector(".signer-options");
    const orgListId = `organization-options-${editorCounter}`;
    const signerListId = `signer-options-${editorCounter}`;
    editorCounter += 1;
    if (orgDatalist) orgDatalist.id = orgListId;
    if (signerDatalist) signerDatalist.id = signerListId;
    editor.dataset.orgListId = orgListId;
    editor.dataset.signerListId = signerListId;

    parseJsonAttr(editor, "organizations").forEach((value) => addRow(editor, "organizations", value));
    parseJsonAttr(editor, "signers").forEach((value) => addRow(editor, "signers", value));
    editor.querySelectorAll(".add-expected").forEach((button) => {
      button.addEventListener("click", () => addRow(editor, button.dataset.kind, ""));
    });
    sync(editor);
  }

  window.initExpectedEditor = initExpectedEditor;

  document.querySelectorAll(".expected-editor").forEach((editor) => {
    initExpectedEditor(editor);
  });
})();
