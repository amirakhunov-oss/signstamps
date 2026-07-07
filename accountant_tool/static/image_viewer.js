(function () {
  const states = new WeakMap();
  const step = 42;

  function normalizedRotation(value) {
    return ((Number(value || 0) % 360) + 360) % 360;
  }

  function rotatedSize(width, height, rotation, scale) {
    const sideways = rotation === 90 || rotation === 270;
    return {
      width: (sideways ? height : width) * scale,
      height: (sideways ? width : height) * scale,
    };
  }

  function rotationOffset(width, height, rotation, scale) {
    if (rotation === 90) return { x: height * scale, y: 0 };
    if (rotation === 180) return { x: width * scale, y: height * scale };
    if (rotation === 270) return { x: 0, y: width * scale };
    return { x: 0, y: 0 };
  }

  function fitScale(wrap, width, height, rotation) {
    const viewport = wrap.getBoundingClientRect();
    const size = rotatedSize(width, height, rotation, 1);
    return Math.min(viewport.width / size.width, viewport.height / size.height);
  }

  function clampZoom(zoom) {
    return Math.max(0.25, Math.min(6, zoom));
  }

  function getState(wrap) {
    let state = states.get(wrap);
    if (state) return state;
    state = {
      rotation: normalizedRotation(wrap.dataset.rotation),
      zoom: 1,
      panX: 0,
      panY: 0,
      dragging: null,
    };
    states.set(wrap, state);
    return state;
  }

  function documentToViewport(wrap, x, y) {
    const state = getState(wrap);
    const width = Number(wrap.dataset.width);
    const height = Number(wrap.dataset.height);
    const scale = fitScale(wrap, width, height, state.rotation) * state.zoom;
    const offset = rotationOffset(width, height, state.rotation, scale);
    let rx = x * scale;
    let ry = y * scale;
    if (state.rotation === 90) {
      rx = -y * scale;
      ry = x * scale;
    } else if (state.rotation === 180) {
      rx = -x * scale;
      ry = -y * scale;
    } else if (state.rotation === 270) {
      rx = y * scale;
      ry = -x * scale;
    }
    return {
      x: state.panX + offset.x + rx,
      y: state.panY + offset.y + ry,
    };
  }

  function viewportToDocument(wrap, sx, sy) {
    const state = getState(wrap);
    const width = Number(wrap.dataset.width);
    const height = Number(wrap.dataset.height);
    const scale = fitScale(wrap, width, height, state.rotation) * state.zoom;
    const offset = rotationOffset(width, height, state.rotation, scale);
    const rx = sx - state.panX - offset.x;
    const ry = sy - state.panY - offset.y;
    let x = rx / scale;
    let y = ry / scale;
    if (state.rotation === 90) {
      x = ry / scale;
      y = -rx / scale;
    } else if (state.rotation === 180) {
      x = -rx / scale;
      y = -ry / scale;
    } else if (state.rotation === 270) {
      x = -ry / scale;
      y = rx / scale;
    }
    x = Math.max(0, Math.min(width, x));
    y = Math.max(0, Math.min(height, y));
    return {
      xPct: (x / width) * 100,
      yPct: (y / height) * 100,
      x,
      y,
    };
  }

  function centerState(wrap, state) {
    const width = Number(wrap.dataset.width);
    const height = Number(wrap.dataset.height);
    const viewport = wrap.getBoundingClientRect();
    const size = rotatedSize(width, height, state.rotation, fitScale(wrap, width, height, state.rotation) * state.zoom);
    state.panX = (viewport.width - size.width) / 2;
    state.panY = (viewport.height - size.height) / 2;
  }

  function applyViewer(wrap) {
    const content = wrap.querySelector(".page-image-content");
    if (!content) return;
    const state = getState(wrap);
    const width = Number(wrap.dataset.width);
    const height = Number(wrap.dataset.height);
    const scale = fitScale(wrap, width, height, state.rotation) * state.zoom;
    const offset = rotationOffset(width, height, state.rotation, scale);

    wrap.dataset.rotation = String(state.rotation);
    wrap.dataset.zoom = state.zoom.toFixed(2);
    content.style.width = `${width}px`;
    content.style.height = `${height}px`;
    content.style.transform = `translate(${state.panX + offset.x}px, ${state.panY + offset.y}px) rotate(${state.rotation}deg) scale(${scale})`;

    document.querySelectorAll(`.rotate-page[data-page-id="${wrap.dataset.pageId}"]`).forEach((button) => {
      button.classList.toggle("active", state.rotation !== 0);
    });
    document.querySelectorAll(`.viewer-zoom-value[data-page-id="${wrap.dataset.pageId}"]`).forEach((el) => {
      el.textContent = `${Math.round(state.zoom * 100)}%`;
    });
  }

  function resetToFit(wrap) {
    const state = getState(wrap);
    state.zoom = 1;
    centerState(wrap, state);
    applyViewer(wrap);
  }

  function rotate(pageId, delta) {
    const wrap = document.querySelector(`.page-image-wrap[data-page-id="${pageId}"]`);
    if (!wrap) return;
    const state = getState(wrap);
    state.rotation = normalizedRotation(state.rotation + Number(delta || 0));
    centerState(wrap, state);
    applyViewer(wrap);
    wrap.dispatchEvent(new CustomEvent("image-viewer:changed", { bubbles: true }));
  }

  function zoomAt(wrap, factor, clientX, clientY) {
    const state = getState(wrap);
    const bounds = wrap.getBoundingClientRect();
    const sx = clientX - bounds.left;
    const sy = clientY - bounds.top;
    const before = viewportToDocument(wrap, sx, sy);
    const oldZoom = state.zoom;
    state.zoom = clampZoom(state.zoom * factor);
    if (state.zoom === oldZoom) return;
    const after = documentToViewport(wrap, before.x, before.y);
    state.panX += sx - after.x;
    state.panY += sy - after.y;
    applyViewer(wrap);
    wrap.dispatchEvent(new CustomEvent("image-viewer:changed", { bubbles: true }));
  }

  function zoomCenter(pageId, factor) {
    const wrap = document.querySelector(`.page-image-wrap[data-page-id="${pageId}"]`);
    if (!wrap) return;
    const bounds = wrap.getBoundingClientRect();
    zoomAt(wrap, factor, bounds.left + bounds.width / 2, bounds.top + bounds.height / 2);
  }

  function pan(wrap, dx, dy) {
    const state = getState(wrap);
    state.panX += dx;
    state.panY += dy;
    applyViewer(wrap);
  }

  function activate(wrap) {
    document.querySelectorAll(".page-image-wrap.active-viewer").forEach((item) => item.classList.remove("active-viewer"));
    wrap.classList.add("active-viewer");
  }

  function initWrap(wrap) {
    const state = getState(wrap);
    centerState(wrap, state);
    applyViewer(wrap);

    wrap.addEventListener("pointerdown", (event) => {
      activate(wrap);
      if (event.target.closest(".draw-point")) return;
      const deferred = wrap.classList.contains("drawing");
      if (!deferred) event.preventDefault();
      state.dragging = {
        x: event.clientX,
        y: event.clientY,
        startX: event.clientX,
        startY: event.clientY,
        moved: false,
        deferred,
      };
      if (!deferred) {
        wrap.classList.add("panning");
        wrap.setPointerCapture(event.pointerId);
      }
    });
    wrap.addEventListener("pointermove", (event) => {
      if (!state.dragging) return;
      const dx = event.clientX - state.dragging.x;
      const dy = event.clientY - state.dragging.y;
      const totalMove = Math.abs(event.clientX - state.dragging.startX) + Math.abs(event.clientY - state.dragging.startY);
      if (state.dragging.deferred && totalMove <= 4) return;
      if (state.dragging.deferred) {
        event.preventDefault();
        state.dragging.deferred = false;
        wrap.classList.add("panning");
        if (!wrap.hasPointerCapture(event.pointerId)) wrap.setPointerCapture(event.pointerId);
      }
      if (totalMove > 4) state.dragging.moved = true;
      state.dragging = {
        x: event.clientX,
        y: event.clientY,
        startX: state.dragging.startX,
        startY: state.dragging.startY,
        moved: state.dragging.moved,
        deferred: state.dragging.deferred,
      };
      pan(wrap, dx, dy);
    });
    wrap.addEventListener("pointerup", (event) => {
      if (state.dragging?.moved) {
        wrap.dataset.suppressDrawClick = "1";
        window.setTimeout(() => {
          if (wrap.dataset.suppressDrawClick === "1") delete wrap.dataset.suppressDrawClick;
        }, 80);
      }
      state.dragging = null;
      wrap.classList.remove("panning");
      if (wrap.hasPointerCapture(event.pointerId)) wrap.releasePointerCapture(event.pointerId);
    });
    wrap.addEventListener("wheel", (event) => {
      event.preventDefault();
      activate(wrap);
      zoomAt(wrap, event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX, event.clientY);
    }, { passive: false });
    wrap.addEventListener("click", () => activate(wrap));
  }

  function refreshAll() {
    document.querySelectorAll(".page-image-wrap").forEach((wrap) => {
      if (!states.has(wrap)) initWrap(wrap);
      else applyViewer(wrap);
    });
  }

  window.imageViewerPointFromEvent = function (event, wrap) {
    const bounds = wrap.getBoundingClientRect();
    const sx = Math.max(0, Math.min(bounds.width, event.clientX - bounds.left));
    const sy = Math.max(0, Math.min(bounds.height, event.clientY - bounds.top));
    return viewportToDocument(wrap, sx, sy);
  };

  document.addEventListener("click", (event) => {
    const rotateButton = event.target.closest(".rotate-page");
    if (rotateButton) {
      rotate(rotateButton.dataset.pageId, rotateButton.dataset.rotate);
      return;
    }
    const zoomButton = event.target.closest(".zoom-page");
    if (zoomButton) {
      if (zoomButton.dataset.zoom === "fit") {
        const wrap = document.querySelector(`.page-image-wrap[data-page-id="${zoomButton.dataset.pageId}"]`);
        if (wrap) resetToFit(wrap);
      } else {
        zoomCenter(zoomButton.dataset.pageId, Number(zoomButton.dataset.zoom));
      }
    }
  });

  document.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    const wrap = document.querySelector(".page-image-wrap.active-viewer") || document.querySelector(".page-image-wrap");
    if (!wrap) return;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName || "")) return;
    event.preventDefault();
    if (event.key === "ArrowLeft") pan(wrap, step, 0);
    if (event.key === "ArrowRight") pan(wrap, -step, 0);
    if (event.key === "ArrowUp") pan(wrap, 0, step);
    if (event.key === "ArrowDown") pan(wrap, 0, -step);
  });

  window.addEventListener("resize", () => {
    document.querySelectorAll(".page-image-wrap").forEach(resetToFit);
  });
  document.addEventListener("DOMContentLoaded", refreshAll);
  refreshAll();
})();
