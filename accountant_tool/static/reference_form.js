(function () {
  document.querySelectorAll(".annotator-form").forEach((form) => {
    const kind = form.querySelector("[data-reference-kind]");
    const personField = form.querySelector("[data-person-field]");
    const personInput = personField && personField.querySelector('input[name="person_name"]');
    if (!kind || !personField || !personInput) return;

    const sync = () => {
      const isStamp = kind.value === "stamp";
      personField.hidden = isStamp;
      personInput.disabled = isStamp;
      personInput.required = !isStamp;
      if (isStamp) personInput.value = "";
    };

    kind.addEventListener("change", sync);
    sync();
  });
})();
