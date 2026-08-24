// The popup has no button: it analyzes on open (or shows the cached result)
// and the pill at the top is purely a loading spinner / retry control.
const loaderPill = document.getElementById("loader");

const NO_INGREDIENTS_MSG =
  "No ingredient list found on this page yet. Scroll down and open the " +
  "product's Ingredients section; the analysis will run by itself.";

function showLoader() {
  loaderPill.classList.remove("hidden", "retry");
  loaderPill.innerHTML = "";
  const spin = document.createElement("span");
  spin.className = "spinner";
  loaderPill.appendChild(spin);
}

function hideLoader() {
  loaderPill.classList.add("hidden");
}

function showRetry() {
  loaderPill.classList.remove("hidden");
  loaderPill.classList.add("retry");
  loaderPill.textContent = "Try again";
}

loaderPill.addEventListener("click", () => {
  if (loaderPill.classList.contains("retry")) runAnalysis();
});

loadCachedResult();

async function loadCachedResult() {
  const resultDiv = document.getElementById("result");

  // chrome.storage only exists once the extension has been reloaded with the
  // "storage" permission from the manifest. If it's missing, skip the cache
  // and just analyze directly.
  if (!chrome.storage || !chrome.storage.session) {
    runAnalysis();
    return;
  }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) return;

  const key = "result:" + tab.url.split("#")[0];
  const cached = (await chrome.storage.session.get(key))[key];

  // Page wasn't auto-detected (SPA store, hidden ingredients tab, or the tab
  // was open before the extension loaded): just run the analysis right now.
  if (!cached) {
    runAnalysis();
    return;
  }

  // Keep listening the whole time the panel is open: the content script
  // re-analyzes when a hidden ingredients tab gets opened on the page, and
  // the panel should swap to the better result the moment it lands.
  chrome.storage.session.onChanged.addListener(changes => {
    const change = changes[key];
    if (!change || !change.newValue) return;
    if (change.newValue.status === "loading") {
      showLoader();
      resultDiv.textContent = "";
    } else if (change.newValue.status === "done") {
      hideLoader();
      renderResult(change.newValue.data);
    } else if (change.newValue.status === "no_ingredients") {
      hideLoader();
      resultDiv.textContent = NO_INGREDIENTS_MSG;
    } else if (change.newValue.status === "error") {
      hideLoader();
      resultDiv.textContent = "Analysis failed: " + change.newValue.message;
      showRetry();
    }
  });

  if (cached.status === "done") {
    renderResult(cached.data);
  } else if (cached.status === "loading") {
    showLoader();
  } else if (cached.status === "no_ingredients") {
    resultDiv.textContent = NO_INGREDIENTS_MSG;
  } else if (cached.status === "error") {
    resultDiv.textContent = "Analysis failed: " + cached.message;
    showRetry();
  }
}

async function runAnalysis() {
  const resultDiv = document.getElementById("result");

  showLoader();
  resultDiv.textContent = "";

  try {
    // Get the current active tab
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    // Content scripts can't run on browser-internal pages
    if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("edge://") || tab.url.startsWith("about:")) {
      resultDiv.textContent = "This page can't be analyzed. Open a product page and try again.";
      hideLoader();
      return;
    }

    // Try to inject the content script on demand (works when we have activeTab,
    // e.g. after an icon click). If not, the manifest-declared content script
    // should already be there, so a failure here is fine.
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content.js"]
      });
    } catch (_) { /* rely on the manifest-declared content script */ }

    // Ask the content script on that tab to scrape the page text
    let scrapeResponse;
    try {
      scrapeResponse = await chrome.tabs.sendMessage(tab.id, { action: "scrapePage" });
    } catch (_) {
      resultDiv.textContent = "Can't read this page yet. Refresh the product page once and try again.";
      showRetry();
      return;
    }
    // Content script separates the ingredient list from the marketing claims.
    // No list found = show guidance, never score against an empty list.
    if (!scrapeResponse.ingredients) {
      hideLoader();
      resultDiv.textContent = NO_INGREDIENTS_MSG;
      return;
    }

    const backendResponse = await fetch("http://127.0.0.1:5000/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ingredients: scrapeResponse.ingredients,
        claims: scrapeResponse.claims || ""
      })
    });

    const data = await backendResponse.json();

    if (data.error) {
      resultDiv.textContent = "Error: " + data.error;
      showRetry();
      return;
    }

    // Cache it so reopening the popup on this page shows the result instantly
    if (tab.url && chrome.storage && chrome.storage.session) {
      const key = "result:" + tab.url.split("#")[0];
      await chrome.storage.session.set({ [key]: { status: "done", data } });
    }

    hideLoader();
    renderResult(data);

  } catch (err) {
    resultDiv.textContent = "Something went wrong: " + err.message;
    showRetry();
  }
}

// Map each verdict to a style + tiny icon
const VERDICT_STYLES = {
  "holds up":        { cls: "verdict-good",  icon: "✓" },
  "partly true":     { cls: "verdict-mixed", icon: "~" },
  "doesn't hold up": { cls: "verdict-bad",   icon: "✗" }
};

function sectionTitle(text) {
  const h = document.createElement("div");
  h.className = "section-title";
  h.textContent = text;
  return h;
}

// Score tier drives the ring color and verdict styling
function scoreTier(score) {
  if (score >= 70) return "good";
  if (score >= 40) return "mixed";
  return "bad";
}

function renderResult(data) {
  const resultDiv = document.getElementById("result");
  resultDiv.innerHTML = "";

  // ── HERO: Truth Score ring + one-word verdict ──
  const score = Math.max(0, Math.min(100, Number(data.truth_score) || 0));
  const tier = scoreTier(score);

  const hero = document.createElement("div");
  hero.className = "card hero";

  const ring = document.createElement("div");
  ring.className = "ring ring-" + tier;
  ring.style.setProperty("--score", score);
  const ringInner = document.createElement("div");
  ringInner.className = "ring-inner";
  const num = document.createElement("span");
  num.className = "ring-num";
  num.textContent = score;
  const lbl = document.createElement("span");
  lbl.className = "ring-label";
  lbl.textContent = "truth";
  ringInner.appendChild(num);
  ringInner.appendChild(lbl);
  ring.appendChild(ringInner);

  const heroText = document.createElement("div");
  heroText.className = "hero-text";
  const word = document.createElement("div");
  word.className = "verdict-word tier-" + tier;
  word.textContent = data.verdict_word || "";
  const oneLiner = document.createElement("div");
  oneLiner.className = "hero-oneliner";
  oneLiner.textContent = data.one_liner || "";
  heroText.appendChild(word);
  heroText.appendChild(oneLiner);

  hero.appendChild(ring);
  hero.appendChild(heroText);
  resultDiv.appendChild(hero);

  // ── CLAIMS: compact rows, tap to reveal the reality ──
  if (data.claims && data.claims.length > 0) {
    resultDiv.appendChild(sectionTitle("Claims"));
    data.claims.forEach(item => {
      const v = VERDICT_STYLES[(item.verdict || "").toLowerCase()] || VERDICT_STYLES["partly true"];

      const row = document.createElement("div");
      row.className = "card claim-row " + v.cls;

      const head = document.createElement("div");
      head.className = "claim-head";
      const icon = document.createElement("span");
      icon.className = "verdict-icon " + v.cls;
      icon.textContent = v.icon;
      const name = document.createElement("strong");
      name.textContent = item.claim_short;
      const chev = document.createElement("span");
      chev.className = "chev";
      chev.textContent = "▾";
      head.appendChild(icon);
      head.appendChild(name);
      head.appendChild(chev);
      row.appendChild(head);

      const reality = document.createElement("div");
      reality.className = "claim-reality";
      reality.textContent = item.reality;
      row.appendChild(reality);

      row.addEventListener("click", () => row.classList.toggle("open"));
      resultDiv.appendChild(row);
    });
  }

  // ── FOR YOU: one combined list, ✓ good fits then ✗ cautions ──
  const fits = data.match_if || [];
  const avoid = data.swipe_left_if || [];
  if (fits.length > 0 || avoid.length > 0) {
    resultDiv.appendChild(sectionTitle("Is it for you?"));
    const wrap = document.createElement("div");
    wrap.className = "chip-wrap";
    appendChips(wrap, fits, "chip-good");
    appendChips(wrap, avoid, "chip-bad");
    resultDiv.appendChild(wrap);
  }

  // ── HERO INGREDIENTS: the 2-3 ingredients doing the real work ──
  const heroes = data.hero_ingredients || [];
  if (heroes.length > 0) {
    resultDiv.appendChild(sectionTitle("Hero ingredients"));
    heroes.forEach(h => {
      const row = document.createElement("div");
      row.className = "ing-row";
      const name = document.createElement("strong");
      name.textContent = h.name;
      const job = document.createElement("div");
      job.className = "ing-job";
      job.textContent = h.job;
      row.appendChild(name);
      row.appendChild(job);
      resultDiv.appendChild(row);
    });
  }

  // ── ROUTINE: when to use, what to layer after ──
  const routine = data.routine || {};
  const routineRows = [
    ["When", routine.when],
    ["How often", routine.how_often],
    ["Follow with", routine.follow_with],
    ["Avoid with", routine.avoid_with]
  ].filter(([, value]) => value);
  if (routineRows.length > 0) {
    resultDiv.appendChild(sectionTitle("How to use"));
    routineRows.forEach(([label, value]) => {
      const row = document.createElement("div");
      row.className = "routine-row";
      const l = document.createElement("span");
      l.className = "routine-label";
      l.textContent = label;
      const v = document.createElement("span");
      v.className = "routine-value";
      v.textContent = value;
      row.appendChild(l);
      row.appendChild(v);
      resultDiv.appendChild(row);
    });
  }

  // ── TIMELINE: honest expectations per claim that (partly) held up ──
  const timeline = data.timeline || [];
  if (timeline.length > 0) {
    resultDiv.appendChild(sectionTitle("When you'll see results"));
    timeline.forEach(t => {
      const row = document.createElement("div");
      row.className = "time-row";
      const what = document.createElement("strong");
      what.textContent = t.expect;
      const when = document.createElement("span");
      when.className = "time-when";
      when.textContent = t.when;
      row.appendChild(what);
      row.appendChild(when);
      resultDiv.appendChild(row);
    });
  }

  // ── WATCH FLAGS: irritants/allergens actually in the list ──
  const flags = data.watch_flags || [];
  if (flags.length > 0) {
    resultDiv.appendChild(sectionTitle("Watch out for"));
    const wrap = document.createElement("div");
    wrap.className = "chip-wrap";
    appendChips(wrap, flags.map(f => ({ chip: f.flag, why: f.why })), "chip-warn");
    resultDiv.appendChild(wrap);
  }

  // ── DISCLAIMER: fixed line from the backend ──
  if (data.disclaimer) {
    const d = document.createElement("div");
    d.className = "disclaimer";
    d.textContent = data.disclaimer;
    resultDiv.appendChild(d);
  }
}

function appendChips(wrap, items, chipClass) {
  items.forEach(item => {
    const chip = document.createElement("div");
    chip.className = "chip " + chipClass;

    const label = document.createElement("span");
    label.textContent = item.chip;
    chip.appendChild(label);

    if (item.why) {
      const why = document.createElement("div");
      why.className = "chip-why";
      why.textContent = item.why;
      chip.appendChild(why);
      chip.addEventListener("click", () => chip.classList.toggle("open"));
    }
    wrap.appendChild(chip);
  });
}