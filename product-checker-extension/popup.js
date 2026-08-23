document.getElementById("analyzeBtn").addEventListener("click", async () => {
  const btn = document.getElementById("analyzeBtn");
  const resultDiv = document.getElementById("result");

  btn.disabled = true;
  btn.textContent = "Analyzing...";
  resultDiv.textContent = "";

  try {
    // Get the current active tab
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    // Ask the content script on that tab to scrape the page text
    const scrapeResponse = await chrome.tabs.sendMessage(tab.id, { action: "scrapePage" });
    const pageText = scrapeResponse.pageText;

    // For v1, we send the raw scraped text as "ingredients" and leave "claims" blank.
    // We'll improve targeting (separating ingredients vs claims) once we see real results.
    const backendResponse = await fetch("http://127.0.0.1:5000/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ingredients: pageText,
        claims: ""
      })
    });

    const data = await backendResponse.json();

    if (data.error) {
      resultDiv.textContent = "Error: " + data.error;
      return;
    }

    renderResult(data);

  } catch (err) {
    resultDiv.textContent = "Something went wrong: " + err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyze This Page";
  }
});

function renderResult(data) {
  const resultDiv = document.getElementById("result");
  resultDiv.innerHTML = "";

  const summary = document.createElement("p");
  summary.innerHTML = "<strong>Summary:</strong> " + data.summary;
  resultDiv.appendChild(summary);

  if (data.claims_analysis && data.claims_analysis.length > 0) {
    const claimsHeader = document.createElement("p");
    claimsHeader.innerHTML = "<strong>Claims:</strong>";
    resultDiv.appendChild(claimsHeader);

    data.claims_analysis.forEach(item => {
      const div = document.createElement("div");
      const verdictClass = item.verdict.replace(/\s+/g, "-");
      div.className = "claim " + verdictClass;
      div.innerHTML = `<strong>${item.claim}</strong> — ${item.verdict}<br>${item.explanation}`;
      resultDiv.appendChild(div);
    });
  }

  if (data.concerns && data.concerns.length > 0) {
    const concernsHeader = document.createElement("p");
    concernsHeader.innerHTML = "<strong>Concerns:</strong>";
    resultDiv.appendChild(concernsHeader);

    const list = document.createElement("ul");
    data.concerns.forEach(c => {
      const li = document.createElement("li");
      li.textContent = c;
      list.appendChild(li);
    });
    resultDiv.appendChild(list);
  }
}