// TasteAtlas frontend: form -> POST /api/plan -> render timeline.
(function () {
  "use strict";

  var form = document.getElementById("plan-form");
  var goBtn = document.getElementById("go");
  var errBox = document.getElementById("error");
  var loading = document.getElementById("loading");
  var result = document.getElementById("result");
  var healthEl = document.getElementById("health");

  var STEP_IDS = ["st1", "st2", "st3", "st4"];
  var stepTimer = null;
  var draftFields = ["city", "likes", "occasion"];
  try {
    var draft = JSON.parse(sessionStorage.getItem("tasteatlas-draft") || "{}");
    draftFields.forEach(function (id) {
      if (typeof draft[id] === "string") document.getElementById(id).value = draft[id];
    });
  } catch (_) { /* storage may be disabled */ }
  form.addEventListener("input", function () {
    var draft = {};
    draftFields.forEach(function (id) { draft[id] = document.getElementById(id).value; });
    try { sessionStorage.setItem("tasteatlas-draft", JSON.stringify(draft)); } catch (_) {}
  });

  function showError(msg) {
    errBox.textContent = msg;
    errBox.classList.remove("hidden");
  }
  function hideError() {
    errBox.classList.add("hidden");
    errBox.textContent = "";
  }

  function animateSteps() {
    STEP_IDS.forEach(function (id) {
      document.getElementById(id).classList.remove("active", "done");
    });
    document.querySelector(".bar-fill").style.width = "35%";
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function renderPlan(data) { window.renderTastePlan(data); }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    hideError();
    result.classList.add("hidden");
    loading.classList.remove("hidden");
    goBtn.disabled = true;
    animateSteps();

    var payload = {
      city: document.getElementById("city").value.trim(),
      likes: document.getElementById("likes").value.trim(),
      occasion: document.getElementById("occasion").value
    };

    fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(function (resp) { return resp.json().then(function (j) { return { ok: resp.ok, j: j }; }); })
      .then(function (r) {
        clearInterval(stepTimer);
        loading.classList.add("hidden");
        goBtn.disabled = false;
        if (!r.ok) {
          var detail = r.j.detail;
          showError(typeof detail === "string" ? detail : "Please check the city, interests and occasion fields.");
          return;
        }
        renderPlan(r.j);
      })
      .catch(function (err) {
        clearInterval(stepTimer);
        loading.classList.add("hidden");
        goBtn.disabled = false;
        showError("Network error: " + err.message);
      });
  });

  // health badge
  fetch("/api/health")
    .then(function (r) { return r.json(); })
    .then(function (h) {
      if (h.qloo_configured && h.llm_configured) {
        healthEl.textContent = "● keys configured · live integration not verified";
        healthEl.className = "ok";
      } else {
        healthEl.textContent = "● setup required · live integration not verified";
        healthEl.className = "bad";
      }
    })
    .catch(function () {
      healthEl.textContent = "● backend unreachable";
      healthEl.className = "bad";
    });
})();
