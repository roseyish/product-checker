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

  function countCommas(s) {
    return (s.match(/,/g) || []).length;
  }

  // Try to isolate the actual ingredient list instead of sending the whole page.
  // Only counts a line as the real INCI list (many commas + known ingredient
  // words) - the short "Ingredients:" heading blurb is handled separately as
  // a last resort so it can never outrank a genuine full list (see
  // extractIngredientsFromHeading and findBestIngredients below).
  function extractIngredients() {
    const blocks = document.body.innerText.split(/\n+/);
    let best = "";
    for (const raw of blocks) {
      const line = raw.trim();
      if (countCommas(line) >= 8 && INCI_HINT.test(line) && line.length > best.length) {
        best = line;
      }
    }
    return normalizeWhitespace(best).slice(0, 4000);
  }

  // Last resort only: a short "Ingredients: ..." heading followed by a brief
  // list (e.g. a "Key Ingredients" callout with 3-4 highlighted actives).
  // This is easy to satisfy but is NOT the real, complete ingredient
  // statement, so it must never be preferred over a genuine INCI block found
  // by extractIngredients()/extractIngredientsFromHidden() - only used when
  // both of those come up empty.
  function extractIngredientsFromHeading() {
    const blocks = document.body.innerText.split(/\n+/);
    for (let i = 0; i < blocks.length; i++) {
      const line = blocks[i].trim();
      if (!/^ingredients\b/i.test(line)) continue;
      const candidate = line.replace(/^ingredients[:\s-]*/i, "") || (blocks[i + 1] || "");
      if (countCommas(candidate) >= 3) {
        return normalizeWhitespace(candidate).slice(0, 4000);
      }
    }
    return "";
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

  // Runs both real-INCI strategies and keeps whichever is more comma-dense
  // (i.e. actually looks like a full ingredient statement) instead of just
  // taking whichever happens to run first. Only falls back to the short
  // heading blurb if neither finds anything.
  function findBestIngredients() {
    const fromLines = extractIngredients();
    const fromHidden = extractIngredientsFromHidden();
    const best = countCommas(fromHidden) > countCommas(fromLines) ? fromHidden : fromLines;
    return best || extractIngredientsFromHeading();
  }

  function getProductData() {
    // No page-dump fallback anymore: sending random page text produced
    // misleading 0-scores. Empty ingredients now means "not found yet".
    const ingredients = findBestIngredients();
    return { ingredients, claims: extractClaims() };
  }

  // Some stores (Nykaa and co.) don't render the ingredients panel into the
  // DOM at all until its tab is clicked, so neither extractor above can see
  // it no matter how the text is hidden. Find that tab and click it so the
  // real content shows up on the next scrape.
  const triedTabTriggers = new WeakSet();

  function findIngredientsTabTrigger() {
    const candidates = document.querySelectorAll('button, a, li, span, div, [role="tab"]');
    for (const el of candidates) {
      if (el.children.length > 2) continue; // tab labels are short, leaf-ish elements
      if (!/^ingredients?$/i.test(normalizeWhitespace(el.textContent))) continue;
      if (triedTabTriggers.has(el)) continue;
      const cls = (el.className || "").toString().toLowerCase();
      const alreadyActive = el.getAttribute("aria-selected") === "true" ||
        cls.includes("active") || cls.includes("selected");
      if (alreadyActive) continue;
      if (typeof el.click === "function") return el;
    }
    return null;
  }

  function clickIngredientsTab() {
    const trigger = findIngredientsTabTrigger();
    if (!trigger) return false;
    triedTabTriggers.add(trigger);
    trigger.click();
    return true;
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
      safeSend({ action: "openSidePanel" });
    });
    document.documentElement.appendChild(tab);
  }

  let lastSentIngredients = "";
  let ingredientObserver = null;

  // After the extension is reloaded or updated, this old content script stays
  // on the page but can no longer reach the extension ("Extension context
  // invalidated"). Detect that and remove our tab and listeners quietly.
  function contextAlive() {
    return !!(chrome.runtime && chrome.runtime.id);
  }

  function teardown() {
    clearTimeout(recheckTimer);
    if (ingredientObserver) ingredientObserver.disconnect();
    document.removeEventListener("click", recheckSoon, true);
    const tab = document.getElementById("itc-tab");
    if (tab) tab.remove();
  }

  function safeSend(message) {
    if (!contextAlive()) {
      teardown();
      return;
    }
    try {
      const p = chrome.runtime.sendMessage(message);
      if (p && p.catch) p.catch(() => {});
    } catch (e) {
      teardown();
    }
  }

  function sendAnalyze(product, force) {
    lastSentIngredients = product.ingredients;
    if (!product.ingredients) {
      // Nothing that looks like an ingredient list yet: tell the panel to show
      // guidance instead of scoring the product 0 against an empty list.
      safeSend({
        action: "noIngredients",
        url: location.href.split("#")[0]
      });
      return;
    }
    safeSend({
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
    // Attach listeners before simulating a click so the recheck below sees it.
    watchForIngredients();
    clickIngredientsTab();
    sendAnalyze(getProductData(), false);
  }

  // Many stores (e.g. Nykaa) keep the ingredient list inside a tab whose
  // content isn't in the DOM at all until clicked. If a real INCI block
  // appears (or changes) after our first scrape, re-analyze and overwrite
  // the cached result.
  let recheckTimer = null;
  function recheckSoon() {
    clearTimeout(recheckTimer);
    recheckTimer = setTimeout(() => {
      if (!contextAlive()) {
        teardown();
        return;
      }
      clickIngredientsTab();
      const found = findBestIngredients();
      if (found && found !== lastSentIngredients) {
        sendAnalyze({ ingredients: found, claims: extractClaims() }, true);
      }
    }, 800);
  }

  function watchForIngredients() {
    // Clicks cover tab switches that only toggle CSS visibility;
    // the observer covers content that gets inserted into the DOM later.
    document.addEventListener("click", recheckSoon, true);
    ingredientObserver = new MutationObserver(recheckSoon);
    ingredientObserver.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true
    });
  }

  // Manual scrape, still used by the popup's fallback button
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "scrapePage") {
      if (clickIngredientsTab()) {
        // Give the tab content a moment to render before reading it back.
        setTimeout(() => sendResponse(getProductData()), 400);
      } else {
        sendResponse(getProductData());
      }
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
