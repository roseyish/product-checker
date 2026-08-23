// This script runs on every page. It listens for a message from the popup
// asking it to grab the page's visible text, then sends it back.

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "scrapePage") {
    // For v1, we grab all visible body text.
    // We'll refine this later to target ingredient sections specifically.
    const pageText = document.body.innerText;

    // Trim to a reasonable length so we don't send an entire huge page
    const trimmedText = pageText.slice(0, 8000);

    sendResponse({ pageText: trimmedText });
  }
  return true; // keeps the message channel open for async response
});