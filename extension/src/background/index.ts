// Background service worker. The side panel hosts the agent session (MV3
// service workers are suspended when idle, which would drop the task's
// WebSocket); this worker only wires the toolbar button to the panel.

chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

chrome.runtime.onStartup.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});
