(function () {
  function parseSuggestions(input) {
    try {
      return JSON.parse(input.dataset.suggestions || "[]").filter(Boolean);
    } catch {
      return [];
    }
  }

  function tokens(value) {
    return String(value || "")
      .toLowerCase()
      .split(/[^\p{L}\p{N}]+/u)
      .filter(Boolean);
  }

  function matches(query, candidate) {
    const queryTokens = tokens(query);
    if (!queryTokens.length) return [];
    const candidateTokens = tokens(candidate);
    return queryTokens.every((queryToken) =>
      candidateTokens.some((candidateToken) => candidateToken.includes(queryToken))
    );
  }

  function close(input) {
    const field = input.closest(".suggest-field");
    const menu = field && field.querySelector(".suggest-menu");
    if (menu) menu.remove();
  }

  function open(input) {
    close(input);
    const field = input.closest(".suggest-field");
    const suggestions = parseSuggestions(input)
      .filter((item) => item !== input.value.trim() && matches(input.value, item))
      .slice(0, 8);
    if (!field || !suggestions.length) return;

    const menu = document.createElement("div");
    menu.className = "suggest-menu";
    suggestions.forEach((suggestion) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "suggest-option";
      button.textContent = suggestion;
      button.addEventListener("mousedown", (event) => {
        event.preventDefault();
        input.value = suggestion;
        close(input);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      menu.appendChild(button);
    });
    field.appendChild(menu);
  }

  document.querySelectorAll("[data-reference-suggest]").forEach((input) => {
    input.addEventListener("input", () => open(input));
    input.addEventListener("focus", () => open(input));
    input.addEventListener("blur", () => window.setTimeout(() => close(input), 120));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close(input);
    });
  });
})();
