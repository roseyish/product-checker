// Background service worker.
// Receives auto-analyze requests from content scripts, calls the backend once
// per URL, caches results in session storage, and shows the truth score as a
// badge on the extension icon for that tab.

const BACKEND_URL = "http://127.0.0.1:5000/analyze";

const TIER_COLORS = {
  good: "#4c9f6f",
  mixed: "#dca93a",
  bad: "#A55166"
};

function tierFor(score) {
  if (score >= 70) return "good";
  if (score >= 40) return "mixed";
  return "bad";
}

function setBadge(tabId, text, color) {
  chrome.action.setBadgeText({ tabId, text });
  if (color) chrome.action.setBadgeBackgroundColor({ tabId, color });
}

// Clicking the toolbar icon also opens the side panel (instead of a popup)
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

chrome.runtime.onMessage.addListener((request, sender) => {
  if (request.action === "autoAnalyze" && sender.tab) {
    handleAutoAnalyze(request, sender.tab.id);
  }
  if (request.action === "noIngredients" && sender.tab) {
    handleNoIngredients(request.url, sender.tab.id);
  }
  // The floating tab on the page was clicked: open the side panel for that tab
  if (request.action === "openSidePanel" && sender.tab) {
    chrome.sidePanel.open({ tabId: sender.tab.id });
  }
});

// No ingredient list found on the page (yet): show a "?" badge and store a
// guidance state, but never overwrite a real result or an in-flight request.
async function handleNoIngredients(url, tabId) {
  const key = "result:" + url;
  const cached = (await chrome.storage.session.get(key))[key];
  if (cached && (cached.status === "done" || cached.status === "loading")) return;
  await chrome.storage.session.set({ [key]: { status: "no_ingredients" } });
  setBadge(tabId, "?", "#D38C9D");
}

async function handleAutoAnalyze({ url, ingredients, claims, force }, tabId) {
  const key = "result:" + url;
  const cached = (await chrome.storage.session.get(key))[key];

  // Already analyzed this URL in this browser session: just restore the badge.
  // force=true means the content script found a better ingredient list
  // (e.g. a hidden ingredients tab was opened), so redo the analysis.
  if (!force && cached && cached.status === "done") {
    const score = Math.max(0, Math.min(100, Number(cached.data.truth_score) || 0));
    setBadge(tabId, String(score), TIER_COLORS[tierFor(score)]);
    return;
  }
  if (cached && cached.status === "loading") return; // request already in flight

  await chrome.storage.session.set({ [key]: { status: "loading" } });
  setBadge(tabId, "...", "#D38C9D");

  // Never leave the panel spinning: give up if the backend hasn't answered in time.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 180000);
  try {
    const res = await fetch(BACKEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ingredients: ingredients || "", claims: claims || "" }),
      signal: controller.signal
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    await chrome.storage.session.set({ [key]: { status: "done", data } });
    const score = Math.max(0, Math.min(100, Number(data.truth_score) || 0));
    setBadge(tabId, String(score), TIER_COLORS[tierFor(score)]);
  } catch (err) {
    const message = err.name === "AbortError"
      ? "The analysis took too long. Please try again."
      : err.message;
    await chrome.storage.session.set({ [key]: { status: "error", message } });
    setBadge(tabId, "!", "#A55166");
  } finally {
    clearTimeout(timer);
  }
}
