'use strict'
const { contextBridge, ipcRenderer } = require('electron')
const methods = ['bootstrap', 'scan', 'import', 'browse_folders', 'prepare_import', 'commit_import', 'remove', 'batch_edit', 'save', 'approve', 'page', 'xml', 'plan', 'export', 'cancel', 'set_output', 'backup', 'export_komga', 'komga_sync']

contextBridge.exposeInMainWorld('comic', {
  invoke: (method, params = {}) => methods.includes(method)
    ? ipcRenderer.invoke('comic:call', method, params)
    : Promise.reject(new Error('不支持的操作。')),
  chooseDirectory: () => ipcRenderer.invoke('comic:choose-directory'),
  chooseDirectories: () => ipcRenderer.invoke('comic:choose-directories'),
  chooseCbz: () => ipcRenderer.invoke('comic:choose-cbz'),
  chooseBackup: () => ipcRenderer.invoke('comic:choose-backup'),
  chooseKomgaManifest: () => ipcRenderer.invoke('comic:choose-komga-manifest'),
  komgaConfig: () => ipcRenderer.invoke('comic:komga-config'),
  saveKomgaConfig: input => ipcRenderer.invoke('comic:save-komga-config', input),
  testKomgaConfig: input => ipcRenderer.invoke('comic:test-komga-config', input),
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
