const { app, BrowserWindow, dialog, shell, ipcMain } = require('electron');
const { spawn } = require('child_process');
const net = require('net');
const path = require('path');
const fs = require('fs');
const http = require('http');

const PRODUCT = 'GameVault';
const PREFERRED_PORT = 37421;
const MAX_START_ATTEMPTS = 20;
let serverProcess = null;
let serverPort = null;
let mainWindow = null;
let isQuitting = false;
let restartTimer = null;
let restartCount = 0;
let logStream = null;

function appDirectory() {
  return path.join(app.getPath('appData'), PRODUCT);
}

function dataDirectory() {
  const directory = path.join(appDirectory(), 'data');
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}

function libraryDirectory() {
  const directory = path.join(appDirectory(), 'library');
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}

function log(message, error) {
  const line = `[GameVault ${new Date().toISOString()}] ${message}${error ? `: ${error.stack || error}` : ''}\n`;
  console.log(line.trimEnd());
  try {
    if (!logStream) {
      fs.mkdirSync(app.getPath('logs'), { recursive: true });
      logStream = fs.createWriteStream(path.join(app.getPath('logs'), 'gamevault.log'), { flags: 'a' });
    }
    logStream.write(line);
  } catch (writeError) {
    console.error('Impossible d’écrire le journal GameVault', writeError);
  }
}

function safeLibraryName(value) {
  const base = path.basename(String(value || 'game.bin'));
  return (base.replace(/[^a-zA-Z0-9._ -]/g, '_').trim() || 'game.bin').slice(0, 180);
}

function downloadGameToLibrary(id, filename, sender) {
  return new Promise((resolve, reject) => {
    const safeName = safeLibraryName(filename);
    const target = path.join(libraryDirectory(), safeName);
    const downloadKey = String(id).replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 80);
    const temporary = `${target}.${downloadKey}.part`;
    let resumeFrom = 0;
    try { resumeFrom = fs.existsSync(temporary) ? fs.statSync(temporary).size : 0; } catch (_) {}
    const headers = resumeFrom ? { Range: `bytes=${resumeFrom}-` } : {};
    const request = http.get({
      host: '127.0.0.1', port: serverPort,
      path: `/api/download/${encodeURIComponent(String(id))}`,
      timeout: 30_000, headers
    }, (response) => {
      if (response.statusCode < 200 || response.statusCode >= 300) {
        response.resume();
        reject(new Error(`Téléchargement refusé (${response.statusCode})`));
        return;
      }
      const resumed = resumeFrom > 0 && response.statusCode === 206;
      if (resumeFrom > 0 && !resumed) {
        try { fs.unlinkSync(temporary); } catch (_) {}
        resumeFrom = 0;
      }
      const rangeMatch = String(response.headers['content-range'] || '').match(/bytes\s+\d+-\d+\/(\d+)/i);
      const total = Number(rangeMatch?.[1] || response.headers['content-length'] || 0);
      let received = resumeFrom;
      const output = fs.createWriteStream(temporary, { flags: resumed ? 'a' : 'w' });
      response.on('data', (chunk) => {
        received += chunk.length;
        if (!sender.isDestroyed()) sender.send('gamevault:download-progress', { id: String(id), received, total });
      });
      response.on('error', (error) => { output.destroy(); reject(error); });
      output.on('error', reject);
      output.on('finish', () => output.close(() => {
        try {
          fs.renameSync(temporary, target);
          resolve({ success: true, name: safeName, path: target, size: received });
        } catch (error) { reject(error); }
      }));
      response.pipe(output);
    });
    request.on('timeout', () => request.destroy(new Error('Délai de téléchargement dépassé')));
    request.on('error', reject);
  });
}

ipcMain.handle('gamevault:download-game', (event, payload) => {
  if (!serverPort || !payload || payload.id === undefined) throw new Error('Jeu invalide');
  return downloadGameToLibrary(payload.id, payload.filename, event.sender);
});

ipcMain.handle('gamevault:list-library', () => fs.readdirSync(libraryDirectory(), { withFileTypes: true })
  .filter((entry) => entry.isFile() && !entry.name.endsWith('.part'))
  .map((entry) => {
    const file = path.join(libraryDirectory(), entry.name);
    return { name: entry.name, size: fs.statSync(file).size };
  }));

ipcMain.handle('gamevault:open-library', () => shell.openPath(libraryDirectory()));
ipcMain.handle('gamevault:launch-library-item', (_event, name) => {
  const directory = libraryDirectory();
  const target = path.join(directory, safeLibraryName(name));
  if (!target.startsWith(directory + path.sep) || !fs.existsSync(target)) throw new Error('Fichier introuvable');
  return shell.openPath(target);
});

function probePort(port) {
  return new Promise((resolve) => {
    const probe = net.createServer();
    probe.once('error', () => resolve(false));
    probe.listen(port, '127.0.0.1', () => probe.close(() => resolve(true)));
  });
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const port = probe.address().port;
      probe.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

async function chooseServerPort() {
  const configured = Number(process.env.GAMEVAULT_PLUGIN_PORT || PREFERRED_PORT);
  if (Number.isInteger(configured) && configured > 0 && configured < 65536 && await probePort(configured)) {
    return configured;
  }
  const port = await findFreePort();
  log(`Port ${configured} indisponible ; port local de remplacement : ${port}`);
  return port;
}

function requestHealth(port) {
  return new Promise((resolve) => {
    const request = http.get({ host: '127.0.0.1', port, path: '/api/health', timeout: 900 }, (response) => {
      let body = '';
      response.on('data', (chunk) => { body += chunk; });
      response.on('end', () => {
        try { resolve(response.statusCode === 200 && JSON.parse(body).ready === true); }
        catch (_) { resolve(false); }
      });
    });
    request.on('error', () => resolve(false));
    request.on('timeout', () => { request.destroy(); resolve(false); });
  });
}

async function waitForServer(port, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline && !isQuitting) {
    if (await requestHealth(port)) return true;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return false;
}

function serverCommand(port) {
  const projectRoot = path.resolve(__dirname, '..');
  if (app.isPackaged && process.platform === 'win32') {
    const executable = path.join(process.resourcesPath, 'server', 'GameVaultServer.exe');
    if (!fs.existsSync(executable)) throw new Error(`Serveur compilé absent : ${executable}. Lancez npm run build:server.`);
    return { executable, args: [], cwd: path.dirname(executable) };
  }
  const python = process.env.GAMEVAULT_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  return { executable: python, args: [path.join(projectRoot, 'serveur_local.py')], cwd: projectRoot };
}

async function spawnLocalServer(port) {
  const command = serverCommand(port);
  const env = {
    ...process.env,
    GAMEVAULT_PORT: String(port),
    GAMEVAULT_DATA_DIR: dataDirectory(),
    GAMEVAULT_BIND_HOST: '127.0.0.1',
    GAMEVAULT_NO_BROWSER: '1',
    PYTHONUNBUFFERED: '1'
  };
  log(`Démarrage du serveur local sur 127.0.0.1:${port} (${command.executable})`);
  const child = spawn(command.executable, command.args, {
    cwd: command.cwd, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']
  });
  serverProcess = child;
  child.stdout.on('data', (chunk) => logStream?.write(`[serveur] ${chunk}`));
  child.stderr.on('data', (chunk) => logStream?.write(`[serveur:err] ${chunk}`));
  child.on('error', (error) => {
    log('Erreur au démarrage du serveur local', error);
    if (serverProcess === child) serverProcess = null;
  });
  child.on('exit', (code, signal) => {
    log(`Serveur arrêté (code=${code}, signal=${signal || 'aucun'})`);
    if (serverProcess === child) serverProcess = null;
    if (!isQuitting) scheduleRestart();
  });

  const ready = await waitForServer(port);
  if (!ready) {
    if (serverProcess === child && child.exitCode === null) child.kill();
    throw new Error(`Le serveur local ne répond pas sur 127.0.0.1:${port}. Consultez le journal GameVault.`);
  }
  restartCount = 0;
  log(`Serveur prêt sur http://127.0.0.1:${port}`);
}

function scheduleRestart() {
  if (isQuitting || restartTimer) return;
  restartCount += 1;
  const delay = Math.min(1000 * (2 ** Math.min(restartCount - 1, 5)), 30_000);
  log(`Redémarrage du serveur dans ${delay / 1000}s (tentative ${restartCount})`);
  restartTimer = setTimeout(async () => {
    restartTimer = null;
    try {
      await spawnLocalServer(serverPort);
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload();
    } catch (error) {
      log('Échec du redémarrage du serveur', error);
      scheduleRestart();
    }
  }, delay);
}

async function createWindow() {
  serverPort = await chooseServerPort();
  await spawnLocalServer(serverPort);
  mainWindow = new BrowserWindow({
    width: 1440, height: 900, minWidth: 980, minHeight: 640,
    backgroundColor: '#060c14', title: PRODUCT, autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
      devTools: !app.isPackaged
    }
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  await mainWindow.loadURL(`http://127.0.0.1:${serverPort}/accounts.html`);
  mainWindow.on('closed', () => { mainWindow = null; });
}

app.setAppUserModelId('com.gamevault.desktop');
// Keep all writable user files under %APPDATA%\\GameVault regardless of the
// Electron executable name (installer, portable build, or development mode).
fs.mkdirSync(appDirectory(), { recursive: true });
app.setPath('userData', appDirectory());
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.whenReady().then(createWindow).catch((error) => {
    log('Échec du démarrage de GameVault', error);
    dialog.showErrorBox(PRODUCT, `${error.message}\n\nConsultez le journal : ${path.join(app.getPath('logs'), 'gamevault.log')}`);
    app.quit();
  });
}

app.on('before-quit', () => {
  isQuitting = true;
  if (restartTimer) clearTimeout(restartTimer);
  if (serverProcess && serverProcess.exitCode === null) {
    log('Arrêt du serveur local à la fermeture de GameVault');
    serverProcess.kill();
  }
  if (logStream) logStream.end();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
