'use strict'
const { contextBridge, ipcRenderer } = require('electron')
const methods = ['bootstrap', 'scan', 'import', 'browse_folders', 'prepare_import', 'commit_import', 'remove', 'batch_edit', 'batch_number', 'save', 'approve', 'page', 'xml', 'scrape_preview', 'model_chatgpt_status', 'model_chatgpt_sign_in', 'model_chatgpt_cancel', 'model_chatgpt_disconnect', 'model_chatgpt_models', 'model_chatgpt_usage', 'model_config_get', 'model_profile_save', 'model_profile_delete', 'model_profile_default', 'model_profile_test', 'model_profile_models', 'model_profile_duplicate', 'model_assist_preview', 'site_config_get', 'site_config_save', 'site_config_clear', 'agent_prepare', 'agent_apply', 'agent_search', 'agent_search_preview', 'plan', 'export', 'cancel', 'set_output', 'backup', 'export_komga', 'komga_sync', 'komga_pull', 'komga_resolve', 'prepare_review_sync', 'commit_review_sync', 'komga_jobs', 'komga_jobs_retry']

contextBridge.exposeInMainWorld('comic', {
  invoke: (method, params = {}) => methods.includes(method)
    ? ipcRenderer.invoke('comic:call', method, params)
    : Promise.reject(new Error('不支持的操作。')),
  chooseDirectory: () => ipcRenderer.invoke('comic:choose-directory'),
  chooseDirectories: () => ipcRenderer.invoke('comic:choose-directories'),
  chooseBackup: () => ipcRenderer.invoke('comic:choose-backup'),
  chooseKomgaManifest: () => ipcRenderer.invoke('comic:choose-komga-manifest'),
  komgaConfig: () => ipcRenderer.invoke('comic:komga-config'),
  saveKomgaConfig: input => ipcRenderer.invoke('comic:save-komga-config', input),
  testKomgaConfig: input => ipcRenderer.invoke('comic:test-komga-config', input),
  openKomgaBook: id => ipcRenderer.invoke('comic:open-komga-book', id),
  openLocation: (kind, id) => ipcRenderer.invoke('comic:open-location', kind, id),
  getVersion: () => ipcRenderer.invoke('comic:version'),
  setWindowState: state => ipcRenderer.send('comic:window-state', { dirty: state?.dirty === true, busy: state?.busy === true }),
  closeWindow: () => ipcRenderer.invoke('comic:close'),
  onEvent: callback => {
    const listener = (_event, event, data) => callback(event, data)
    ipcRenderer.on('comic:event', listener)
    return () => ipcRenderer.removeListener('comic:event', listener)
  },
})
