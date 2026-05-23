const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('receptionist', {
  listBusinesses: () => ipcRenderer.invoke('business:list'),
  getBusiness: (configPath) => ipcRenderer.invoke('business:get', configPath),
  listAppointments: (configPath, options = {}) => ipcRenderer.invoke('business:appointments', configPath, options),
  getEmailSetup: (configPath) => ipcRenderer.invoke('business:emailSetup', configPath),
  updateEmailSetup: (payload) => ipcRenderer.invoke('business:emailUpdate', payload),
  updateBusiness: (payload) => ipcRenderer.invoke('business:update', payload),
  chooseConfig: () => ipcRenderer.invoke('dialog:chooseConfig'),
  openConfig: (configPath) => ipcRenderer.invoke('config:openExternal', configPath),
  startAgent: (options) => ipcRenderer.invoke('agent:start', options),
  stopAgent: () => ipcRenderer.invoke('agent:stop'),
  agentStatus: () => ipcRenderer.invoke('agent:status'),
  setupBooking: (businessSlug) => ipcRenderer.invoke('booking:setup', businessSlug),
  sendAppointmentEmail: (payload) => ipcRenderer.invoke('appointment:send-email', payload),
  renameAppointment: (payload) => ipcRenderer.invoke('appointment:rename', payload),
  deleteAppointment: (payload) => ipcRenderer.invoke('appointment:delete', payload),
  runReminderCommand: (payload) => ipcRenderer.invoke('reminders:run', payload),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  onProcessLog: (callback) => ipcRenderer.on('process-log', (_event, payload) => callback(payload)),
  onAgentStatus: (callback) => ipcRenderer.on('agent-status', (_event, payload) => callback(payload)),
  onUpdateStatus: (callback) => ipcRenderer.on('update-status', (_event, payload) => callback(payload)),
});

contextBridge.exposeInMainWorld('windowControls', {
  minimize: () => ipcRenderer.invoke('window:minimize'),
  toggleMaximize: () => ipcRenderer.invoke('window:toggle-maximize'),
  close: () => ipcRenderer.invoke('window:close'),
});
