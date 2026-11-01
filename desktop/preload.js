const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('gameVaultDesktop', Object.freeze({
  version: '4.1.0',
  platform: process.platform,
  isDesktop: true,
  downloadGame: (id, filename) => ipcRenderer.invoke('gamevault:download-game', { id, filename }),
  listLibrary: () => ipcRenderer.invoke('gamevault:list-library'),
  openLibrary: () => ipcRenderer.invoke('gamevault:open-library'),
  launchLibraryItem: (name) => ipcRenderer.invoke('gamevault:launch-library-item', name),
  onDownloadProgress: (callback) => {
    const listener = (_event, progress) => callback(progress);
    ipcRenderer.on('gamevault:download-progress', listener);
    return () => ipcRenderer.removeListener('gamevault:download-progress', listener);
  }
}));
