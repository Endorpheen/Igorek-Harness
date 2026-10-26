(() => {
  "use strict";
  const diagrams = new WeakSet();
  const minimum = 0.01;
  const maximum = 4;

  function enhance(diagram) {
    if (diagrams.has(diagram) || !diagram.isConnected) return;
    diagrams.add(diagram);
    const russian = document.documentElement.lang.startsWith("ru");
    const labels = russian
      ? {group: "Масштаб диаграммы", fit: "Вписать", out: "Уменьшить", in: "Увеличить", actual: "Натуральный размер", viewport: "Диаграмма"}
      : {group: "Diagram zoom", fit: "Fit", out: "Zoom out", in: "Zoom in", actual: "Actual size", viewport: "Diagram"};
    const frame = document.createElement("section");
    frame.className = "wiki-diagram";
    const controls = document.createElement("div");
    controls.className = "wiki-diagram-controls";
    controls.setAttribute("role", "group");
    controls.setAttribute("aria-label", labels.group);
    const viewport = document.createElement("div");
    viewport.className = "wiki-diagram-viewport";
    viewport.tabIndex = 0;
    viewport.setAttribute("role", "region");
    viewport.setAttribute("aria-label", labels.viewport);
    const space = document.createElement("div");
    space.className = "wiki-diagram-space";
    const canvas = document.createElement("div");
    canvas.className = "wiki-diagram-canvas";
    const output = document.createElement("output");
    output.className = "wiki-diagram-scale";
    output.setAttribute("aria-live", "polite");
    let scale = 1;
    let fit = true;
    let width = 0;
    let height = 0;

    function button(text, title, action) {
      const element = document.createElement("button");
      element.type = "button";
      element.textContent = text;
      element.title = title;
      element.setAttribute("aria-label", title);
      element.addEventListener("click", action);
      controls.append(element);
      return element;
    }
    function draw() {
      if (!width || !viewport.clientWidth) return;
      const available = viewport.clientWidth;
      if (fit) scale = Math.min(1, available / width);
      space.style.width = Math.ceil(width * scale) + "px";
      space.style.height = Math.ceil(height * scale) + "px";
      canvas.style.transform = "scale(" + scale + ")";
      output.value = Math.round(scale * 100) + "%";
      fitting.setAttribute("aria-pressed", String(fit));
      smaller.disabled = scale <= minimum;
      larger.disabled = scale >= maximum;
    }
    function zoom(value) {
      const previous = scale;
      const centerX = (viewport.scrollLeft + viewport.clientWidth / 2) / previous;
      const centerY = (viewport.scrollTop + viewport.clientHeight / 2) / previous;
      fit = false;
      scale = Math.max(minimum, Math.min(maximum, value));
      draw();
      viewport.scrollLeft = centerX * scale - viewport.clientWidth / 2;
      viewport.scrollTop = centerY * scale - viewport.clientHeight / 2;
    }
    const fitting = button(labels.fit, labels.fit, () => {
      fit = true;
      draw();
      viewport.scrollLeft = 0;
      viewport.scrollTop = 0;
    });
    const smaller = button("−", labels.out, () => zoom(scale / 1.25));
    controls.append(output);
    const larger = button("+", labels.in, () => zoom(scale * 1.25));
    button("100%", labels.actual, () => zoom(1));
    diagram.replaceWith(frame);
    frame.append(controls, viewport);
    viewport.append(space);
    space.append(canvas);
    canvas.append(diagram);

    // Material renders SVG into a closed shadow root; scale its host as a whole.
    width = Math.ceil(diagram.scrollWidth);
    canvas.style.width = width + "px";
    height = diagram.offsetHeight;
    draw();
    const observer = new ResizeObserver(() => {
      if (!frame.isConnected) { observer.disconnect(); return; }
      height = diagram.offsetHeight;
      draw();
    });
    observer.observe(viewport);
  }

  function scan(root) {
    if (root.matches?.("div.mermaid")) enhance(root);
    root.querySelectorAll?.("div.mermaid").forEach(enhance);
  }
  function start() {
    scan(document);
    new MutationObserver(records => {
      for (const record of records) for (const node of record.addedNodes) {
        if (node.nodeType === Node.ELEMENT_NODE) scan(node);
      }
    }).observe(document.body, {childList: true, subtree: true});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once: true});
  else start();
})();
