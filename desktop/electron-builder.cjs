const path = require('node:path');
const appDirectory = process.env.IGOREK_DESKTOP_APP_DIR;
if (!appDirectory || !path.isAbsolute(appDirectory)) throw new Error('Set IGOREK_DESKTOP_APP_DIR to the reviewed staging directory.');

// The current downloader uses fetch; preserve environment-proxy support.
if (process.env.HTTPS_PROXY || process.env.HTTP_PROXY || process.env.https_proxy || process.env.http_proxy) {
  require.resolve('undici');
  require('@electron/get').initializeProxy();
}

module.exports = {
  appId: 'app.igorek.desktop', productName: 'Игорёк', executableName: 'igorek',
  electronVersion: require('./package.json').devDependencies.electron,
  asar: false,
  npmRebuild: false,
  icon: path.join(appDirectory, 'assets/igorek.png'),
  directories: { app: appDirectory, output: process.env.IGOREK_DESKTOP_DIST_DIR || path.resolve(__dirname, '../build/desktop-release') },
  files: ['**/*', '!**/__pycache__/**', '!**/*.pyc'],
  artifactName: 'igorek-${version}-${os}-${arch}.${ext}',
  win: { target: [{ target: 'nsis', arch: ['x64'] }] },
  nsis: { oneClick: false, perMachine: false, allowElevation: false, allowToChangeInstallationDirectory: true,
    deleteAppDataOnUninstall: false, createDesktopShortcut: true, createStartMenuShortcut: true },
  linux: { target: ['AppImage'], category: 'Development', syncDesktopName: true,
    desktop: { entry: { StartupWMClass: 'igorek-desktop' } } },
  publish: null,
};
