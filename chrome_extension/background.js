// Keyboard shortcut (Alt+Shift+F). Change it at chrome://extensions/shortcuts
chrome.commands.onCommand.addListener(async (command) => {
  if (command !== "fill-page") return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id, allFrames: true },
    func: () => (window.__autofillRun ? window.__autofillRun() : 0)
  });
  const total = results.reduce((sum, r) => sum + (r.result || 0), 0);
  chrome.action.setBadgeText({ text: String(total), tabId: tab.id });
  setTimeout(() => chrome.action.setBadgeText({ text: "", tabId: tab.id }), 3000);
});
