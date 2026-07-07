(function () {
  let active = null;
  let dragState = null;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function pointFromEvent(event, layer, width, height) {
    const wrap = layer.closest(".page-image-wrap");
    if (window.imageViewerPointFromEvent && wrap) {
      return window.imageViewerPointFromEvent(event, wrap);
    }
    const bounds = layer.getBoundingClientRect();
    const xPct = clamp(((event.clientX - bounds.left) / bounds.width) * 100, 0, 100);
    const yPct = clamp(((event.clientY - bounds.top) / bounds.height) * 100, 0, 100);
    return {
      x: (xPct / 100) * width,
      y: (yPct / 100) * height,
    };
  }

  function clearLayer(layer) {
    layer.innerHTML = "";
  }

  function bboxFromPoints(points, width, height) {
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    return {
      x1: clamp(Math.min(...xs), 0, width),
      y1: clamp(Math.min(...ys), 0, height),
      x2: clamp(Math.max(...xs), 0, width),
      y2: clamp(Math.max(...ys), 0, height),
    };
  }

  function pointsAttr(points) {
    return points.map((point) => `${point.x},${point.y}`).join(" ");
  }

  function updateFormPolygon(state) {
    if (state.points.length < 3) return;
    const bbox = bboxFromPoints(state.points, state.width, state.height);
    state.form.querySelector('[name="x1"]').value = Math.round(bbox.x1);
    state.form.querySelector('[name="y1"]').value = Math.round(bbox.y1);
    state.form.querySelector('[name="x2"]').value = Math.round(bbox.x2);
    state.form.querySelector('[name="y2"]').value = Math.round(bbox.y2);
    const polygonInput = state.form.querySelector('[name="polygon_json"]');
    if (polygonInput) {
      polygonInput.value = JSON.stringify(
        state.points.map((point) => ({ x: Math.round(point.x), y: Math.round(point.y) }))
      );
    }
    state.form.querySelector(".coords-preview").textContent =
      `Полигон: ${state.points.length} точек. Вершины можно двигать мышью.`;
  }

  function makeSvg(state) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("draw-svg");
    svg.setAttribute("viewBox", `0 0 ${state.width} ${state.height}`);
    svg.setAttribute("preserveAspectRatio", "none");
    return svg;
  }

  function appendShape(svg, tag, className, points) {
    const shape = document.createElementNS("http://www.w3.org/2000/svg", tag);
    shape.classList.add(className);
    shape.setAttribute("points", pointsAttr(points));
    svg.appendChild(shape);
  }

  function marker(point, index, finishable) {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "draw-point";
    el.dataset.index = String(index);
    if (finishable) el.dataset.finish = "true";
    el.style.left = `${(point.x / active.width) * 100}%`;
    el.style.top = `${(point.y / active.height) * 100}%`;
    el.title = finishable ? "Замкнуть полигон" : "Перетащить точку";
    return el;
  }

  function renderDrawing(state) {
    clearLayer(state.layer);
    const svg = makeSvg(state);
    const previewPoints = state.hoverPoint ? [...state.points, state.hoverPoint] : state.points;
    if (previewPoints.length >= 2) appendShape(svg, "polyline", "draw-polyline", previewPoints);
    if (state.points.length >= 3) appendShape(svg, "polygon", "draw-polygon-preview", state.points);
    state.layer.appendChild(svg);
    state.points.forEach((point, index) => {
      state.layer.appendChild(marker(point, index, index === 0 && state.points.length >= 3));
    });
  }

  function renderEditable(state) {
    clearLayer(state.layer);
    const svg = makeSvg(state);
    appendShape(svg, "polygon", "editable-polygon", state.points);
    state.layer.appendChild(svg);
    state.points.forEach((point, index) => state.layer.appendChild(marker(point, index, false)));
    updateFormPolygon(state);
  }

  function finishSelection() {
    if (!active || active.points.length < 3) return;
    active.wrap.classList.remove("drawing");
    active.wrap.classList.add("editing");
    active.button.textContent = "Выделить заново";
    renderEditable(active);
  }

  function stopActiveDrawing() {
    if (!active) return;
    active.wrap.classList.remove("drawing", "editing");
    clearLayer(active.layer);
    active.button.textContent = "Выделить";
    ["x1", "y1", "x2", "y2"].forEach((name) => {
      const input = active.form.querySelector(`[name="${name}"]`);
      if (input) input.value = "";
    });
    const polygonInput = active.form.querySelector('[name="polygon_json"]');
    if (polygonInput) polygonInput.value = "";
    active = null;
  }

  function startSelection(form) {
    stopActiveDrawing();
    const pageId = form.dataset.pageId;
    const wrap = document.querySelector(`.page-image-wrap[data-page-id="${pageId}"]`);
    const layer = wrap.querySelector(".draw-layer");
    const button = form.querySelector(".start-draw");
    wrap.classList.add("drawing");
    button.textContent = "Идёт разметка";
    form.querySelector(".coords-preview").textContent =
      "Ставьте точки по краю объекта. Замкните по первой точке или двойным кликом.";
    active = {
      form,
      wrap,
      layer,
      button,
      points: [],
      hoverPoint: null,
      width: Number(wrap.dataset.width),
      height: Number(wrap.dataset.height),
    };
  }

  document.addEventListener("click", (event) => {
    const startButton = event.target.closest(".start-draw");
    if (startButton) {
      startSelection(startButton.closest(".annotator-form"));
      return;
    }
    if (!active || !active.wrap.classList.contains("drawing")) return;
    if (active.wrap.dataset.suppressDrawClick === "1") {
      delete active.wrap.dataset.suppressDrawClick;
      return;
    }
    const finishPoint = event.target.closest('.draw-point[data-finish="true"]');
    if (finishPoint) {
      finishSelection();
      return;
    }
    if (!event.target.closest(".draw-layer")) return;
    active.points.push(pointFromEvent(event, active.layer, active.width, active.height));
    active.hoverPoint = null;
    renderDrawing(active);
  });

  document.addEventListener("dblclick", (event) => {
    if (!active || !active.wrap.classList.contains("drawing") || !event.target.closest(".draw-layer")) return;
    event.preventDefault();
    finishSelection();
  });

  document.addEventListener("mousemove", (event) => {
    if (!active || !active.wrap.classList.contains("drawing") || !event.target.closest(".draw-layer")) return;
    if (active.points.length === 0) return;
    active.hoverPoint = pointFromEvent(event, active.layer, active.width, active.height);
    renderDrawing(active);
  });

  document.addEventListener("pointerdown", (event) => {
    const pointEl = event.target.closest(".draw-point");
    if (!pointEl || !active || !active.wrap.classList.contains("editing")) return;
    event.preventDefault();
    event.stopPropagation();
    dragState = { index: Number(pointEl.dataset.index) };
    active.layer.setPointerCapture(event.pointerId);
  });

  document.addEventListener("pointermove", (event) => {
    if (!dragState || !active) return;
    active.points[dragState.index] = pointFromEvent(event, active.layer, active.width, active.height);
    renderEditable(active);
  });

  document.addEventListener("pointerup", (event) => {
    if (!dragState || !active) return;
    dragState = null;
    if (active.layer.hasPointerCapture(event.pointerId)) active.layer.releasePointerCapture(event.pointerId);
  });

  document.addEventListener("keydown", (event) => {
    if (!active) return;
    if (event.key === "Escape") {
      stopActiveDrawing();
      return;
    }
    if (event.key === "Enter" && active.wrap.classList.contains("drawing")) {
      event.preventDefault();
      finishSelection();
      return;
    }
    if ((event.key === "Backspace" || event.key === "Delete") && active.wrap.classList.contains("drawing")) {
      active.points.pop();
      active.hoverPoint = null;
      renderDrawing(active);
    }
  });

  document.addEventListener("submit", (event) => {
    const form = event.target.closest(".annotator-form");
    if (!form) return;
    const polygonInput = form.querySelector('[name="polygon_json"]');
    const coords = form.querySelector(".coords-preview");
    const hasBox = ["x1", "y1", "x2", "y2"].every((name) => form.querySelector(`[name="${name}"]`)?.value);
    if (!polygonInput || (polygonInput.value && hasBox)) return;
    event.preventDefault();
    if (coords) coords.textContent = "Сначала выделите объект полигоном и замкните его по первой точке или двойным кликом.";
  });
})();
