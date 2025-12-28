// static/js/report.js
(function () {
  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }
  function qsa(sel, root) {
    return Array.from((root || document).querySelectorAll(sel));
  }

  // CSS.escape fallback (старые браузеры)
  function cssEscapeSafe(value) {
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(value);
    return String(value).replace(/["\\]/g, "\\$&");
  }

  ready(() => {
    // Filters
    const toggleNa = document.getElementById("toggle-na");
    const togglePass = document.getElementById("toggle-pass");

    function applyFilters() {
      const hideNa = toggleNa ? toggleNa.checked : false;
      const hidePass = togglePass ? togglePass.checked : false;

      qsa(".check-row").forEach(row => {
        const isNa = row.classList.contains("status-na");
        const isPass = row.classList.contains("status-pass");

        let hidden = false;
        if (hideNa && isNa) hidden = true;
        if (hidePass && isPass) hidden = true;

        row.style.display = hidden ? "none" : "";
      });
    }

    if (toggleNa) toggleNa.addEventListener("change", applyFilters);
    if (togglePass) togglePass.addEventListener("change", applyFilters);
    if (toggleNa || togglePass) applyFilters();

    // Page flags
    const root = qs("#report-root");
    const reportId = root ? root.getAttribute("data-report-id") : null;
    const isPro = root ? root.getAttribute("data-is-pro") === "1" : false;

    // Upgrade buttons (free users)
    qsa(".upgrade-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        window.location.href = "/pricing";
      });
    });

    // Pro steps loader
    const cache = new Map(); // checkId -> content_md

    async function loadProSteps(checkId) {
      if (!reportId) throw new Error("Missing reportId");

      const url = `/api/report/${encodeURIComponent(reportId)}/pro/${encodeURIComponent(checkId)}`;
      const r = await fetch(url, { credentials: "include" });

      if (r.status === 402 || r.status === 403) {
        return { ok: false, reason: "PRO_REQUIRED" };
      }
      if (!r.ok) {
        return { ok: false, reason: "ERROR" };
      }
      return r.json();
    }

    function renderMdPlain(md) {
      // безопасно: как текст (без HTML)
      const pre = document.createElement("pre");
      pre.className = "pro-md";
      pre.textContent = md || "";
      return pre;
    }

    function setContainerMessage(container, msg) {
      container.innerHTML = "";
      const div = document.createElement("div");
      div.className = "pro-md";
      div.textContent = msg;
      container.appendChild(div);
    }

    async function onOpenPro(checkId, btn) {
      const container = qs(`.pro-content[data-check-id="${cssEscapeSafe(checkId)}"]`);
      if (!container) return;

      const isVisible = container.style.display !== "none";
      if (isVisible) {
        container.style.display = "none";
        btn.textContent = "Show steps";
        return;
      }

      // If UI shows the pro button but server says user isn't pro
      if (!isPro) {
        window.location.href = "/pricing";
        return;
      }

      btn.disabled = true;
      const prevText = btn.textContent;
      btn.textContent = "Loading…";

      try {
        if (cache.has(checkId)) {
          container.innerHTML = "";
          container.appendChild(renderMdPlain(cache.get(checkId)));
          container.style.display = "block";
          btn.textContent = "Hide steps";
          return;
        }

        const data = await loadProSteps(checkId);
        if (!data || data.ok === false) {
          container.style.display = "block";
          if (data && data.reason === "PRO_REQUIRED") {
            setContainerMessage(container, "Pro required to view step-by-step fixes.");
            btn.textContent = "Show steps";
          } else {
            setContainerMessage(container, "Steps not found for this check yet.");
            btn.textContent = "Show steps";
          }
          return;
        }

        const md = data.content_md || "";
        cache.set(checkId, md);

        container.innerHTML = "";
        container.appendChild(renderMdPlain(md));
        container.style.display = "block";
        btn.textContent = "Hide steps";
      } catch (e) {
        container.style.display = "block";
        setContainerMessage(container, "Failed to load steps. Please try again.");
        btn.textContent = prevText || "Show steps";
      } finally {
        btn.disabled = false;
      }
    }

    // Pro open buttons
    qsa(".pro-open-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const checkId = btn.getAttribute("data-check-id");
        if (!checkId) return;
        onOpenPro(checkId, btn);
      });
    });
  });
})();
