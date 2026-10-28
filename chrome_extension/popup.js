document.getElementById("fill").addEventListener("click", async () => {
  const msg = document.getElementById("msg");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: true },
      func: () => (window.__autofillRun ? window.__autofillRun() : 0)
    });
    const total = results.reduce((sum, r) => sum + (r.result || 0), 0);
    msg.textContent = total ? `Filled ${total} fields` : "No matching fields found";
  } catch (e) {
    msg.textContent = "Can't run on this page";
  }
});
