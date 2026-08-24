// Runs automatically on every page (declared in manifest.json).
// If the page looks like a product page, it scrapes the text and asks the
// background worker to analyze it, so the result is ready before the user
// even opens the popup.

if (!window.__ingredientCheckerLoaded) {
  window.__ingredientCheckerLoaded = true;

  function getPageText() {
    // Trim to a reasonable length so we don't send an entire huge page
    return document.body.innerText.slice(0, 8000);
  }

  function normalizeWhitespace(t) {
    return (t || "").replace(/\s+/g, " ").trim();
  }

  // Words that appear in almost every real cosmetic INCI list
  const INCI_HINT = /\b(aqua|water|glycerin|sodium|potassium|acid|extract|parfum|fragrance|oxide|alcohol|oil|phenoxyethanol|tocopherol|betaine|sulfate)\b/i;

  // Try to isolate the actual ingredient list instead of sending the whole page.
  // Strategy: scan text blocks for either an "Ingredients:" header or the block
  // that looks most like an INCI list (many commas + known ingredient words).
  function extractIngredients() {
    const blocks = document.body.innerText.split(/\n+/);
    let best = "";
    for (let i = 0; i < blocks.length; i++) {
      const line = blocks[i].trim();
      const commas = (line.match(/,/g) || []).length;
      if (commas >= 8 && INCI_HINT.test(line) && line.length > best.length) {
        best = line;
      }
      if (/^ingredients\b/i.test(line)) {
        const candidate = line.replace(/^ingredients[:\s-]*/i, "") || (blocks[i + 1] || "");
        if ((candidate.match(/,/g) || []).length >= 3 && candidate.length > best.length) {
          best = candidate.trim();
        }
      }
    }
    return normalizeWhitespace(best).slice(0, 4000);
  }

  // Marketing claims come from the stable parts of the page (product title and
  // meta descriptions), not the full body text, so they don't change between loads.
  function extractClaims() {
    const parts = [];
    const h1 = document.querySelector("h1");
    parts.push(h1 ? h1.innerText : document.title);
    const meta = document.querySelector('meta[name="description"]');
    if (meta && meta.content) parts.push(meta.content);
    const og = document.querySelector('meta[property="og:description"]');
    if (og && og.content && (!meta || og.content !== meta.content)) parts.push(og.content);
    return normalizeWhitespace(parts.join(". ")).slice(0, 1500);
  }

  // Same hunt, but through textContent, which includes text inside hidden,
  // not-yet-clicked tabs (Nykaa, Amazon and co. render them into the DOM early).
  function extractIngredientsFromHidden() {
    let best = "";
    let bestCommas = 0;
    const nodes = document.querySelectorAll("p, li, td, section, div");
    for (const el of nodes) {
      if (el.children.length > 5) continue; // prefer leaf-ish blocks over big containers
      const t = normalizeWhitespace(el.textContent);
      if (t.length < 60 || t.length > 4500) continue;
      const commas = (t.match(/,/g) || []).length;
      if (commas < 8 || !INCI_HINT.test(t)) continue;
      // More commas wins; same commas, shorter text wins (child beats wrapper)
      if (commas > bestCommas || (commas === bestCommas && t.length < best.length)) {
        best = t;
        bestCommas = commas;
      }
    }
    return best.slice(0, 4000);
  }

  function getProductData() {
    // No page-dump fallback anymore: sending random page text produced
    // misleading 0-scores. Empty ingredients now means "not found yet".
    const ingredients = extractIngredients() || extractIngredientsFromHidden();
    return { ingredients, claims: extractClaims() };
  }

  // Cheap heuristic: a product page worth analyzing mentions ingredients
  // AND has a buy signal (cart/buy button text somewhere on the page).
  function looksLikeProductPage() {
    const text = document.body.innerText.toLowerCase();
    if (!text.includes("ingredient")) return false;
    const buySignals = ["add to cart", "add to bag", "add to basket", "buy now", "buy it now"];
    return buySignals.some(s => text.includes(s));
  }

  // Floating pink tab on the right edge of product pages: clicking it opens
  // the extension's side panel with the analysis.
  function injectTab() {
    if (document.getElementById("itc-tab")) return;
    const tab = document.createElement("div");
    tab.id = "itc-tab";
    tab.textContent = "✦";
    tab.title = "Ingredient Truth Checker";
    Object.assign(tab.style, {
      position: "fixed",
      top: "40%",
      right: "0",
      width: "44px",
      height: "56px",
      borderRadius: "16px 0 0 16px",
      background: "#d64a6f",
      color: "#fff",
      fontSize: "20px",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      cursor: "pointer",
      zIndex: "2147483647",
      boxShadow: "0 4px 14px rgba(120, 45, 65, 0.35)",
      transition: "width 0.15s ease",
      userSelect: "none"
    });
    tab.addEventListener("mouseenter", () => { tab.style.width = "52px"; });
    tab.addEventListener("mouseleave", () => { tab.style.width = "44px"; });
    tab.addEventListener("click", () => {
      chrome.runtime.sendMessage({ action: "openSidePanel" });
    });
    document.documentElement.appendChild(tab);
  }

  let lastSentIngredients = "";

  function sendAnalyze(product, force) {
    lastSentIngredients = product.ingredients;
    if (!product.ingredients) {
      // Nothing that looks like an ingredient list yet: tell the panel to show
      // guidance instead of scoring the product 0 against an empty list.
      chrome.runtime.sendMessage({
        action: "noIngredients",
        url: location.href.split("#")[0]
      });
      return;
    }
    chrome.runtime.sendMessage({
      action: "autoAnalyze",
      url: location.href.split("#")[0],
      ingredients: product.ingredients,
      claims: product.claims,
      force: !!force
    });
  }

  function tryAutoAnalyze() {
    if (!looksLikeProductPage()) return;
    injectTab();
    sendAnalyze(getProductData(), false);
    watchForIngredients();
  }

  // Many stores (e.g. Nykaa) keep the ingredient list inside a tab whose text
  // is hidden from innerText until clicked. If a real INCI block appears (or
  // changes) after our first scrape, re-analyze and overwrite the cached result.
  let recheckTimer = null;
  function recheckSoon() {
    clearTimeout(recheckTimer);
    recheckTimer = setTimeout(() => {
      const found = extractIngredients() || extractIngredientsFromHidden();
      if (found && found !== lastSentIngredients) {
        sendAnalyze({ ingredients: found, claims: extractClaims() }, true);
      }
    }, 800);
  }

  function watchForIngredients() {
    // Clicks cover tab switches that only toggle CSS visibility;
    // the observer covers content that gets inserted into the DOM later.
    document.addEventListener("click", recheckSoon, true);
    new MutationObserver(recheckSoon).observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true
    });
  }

  // Manual scrape, still used by the popup's fallback button
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "scrapePage") {
      sendResponse(getProductData());
    }
    return true; // keeps the message channel open for async response
  });

  // Give dynamic content (lazy-loaded ingredient sections, prices) a moment
  // to render before we scrape.
  if (document.readyState === "complete") {
    setTimeout(tryAutoAnalyze, 1500);
  } else {
    window.addEventListener("load", () => setTimeout(tryAutoAnalyze, 1500));
  }
}
