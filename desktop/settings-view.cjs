const { EventEmitter } = require('node:events');

class SettingsView extends EventEmitter {
  constructor({ parent, WebContentsView, preload, url }) {
    super();
    this.parent = parent;
    this.closed = false;
    this.url = url;
    this.view = new WebContentsView({ webPreferences: {
      preload, nodeIntegration: false, contextIsolation: true, sandbox: true,
      webSecurity: true, partition: 'igorek-desktop-owner-settings',
    } });
    this.webContents = this.view.webContents;
    this.view.setBackgroundColor('#171b1a');
    this.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    for (const event of ['will-navigate', 'will-redirect', 'will-attach-webview'])
      this.webContents.on(event, event => event.preventDefault());
    this.webContents.session.setPermissionRequestHandler((_, __, callback) => callback(false));
    this.webContents.session.setPermissionCheckHandler(() => false);
    this.resize = () => this.layout();
    this.parentClosed = () => this.close();
    parent.on('resize', this.resize);
    parent.once('closed', this.parentClosed);
    this.webContents.once('destroyed', () => this.close());
    parent.contentView.addChildView(this.view);
    this.layout();
  }

  isDestroyed() { return this.closed || this.webContents.isDestroyed() || this.parent.isDestroyed(); }

  layout() {
    if (this.isDestroyed()) return;
    const [width, height] = this.parent.getContentSize();
    this.view.setBounds({ x: 0, y: 36, width: Math.max(1, width), height: Math.max(1, height - 36) });
  }

  async loadURL(url) {
    if (url !== this.url || this.isDestroyed()) throw new Error('Invalid settings navigation');
    await this.webContents.loadURL(url);
    if (!this.isDestroyed()) this.focus();
  }

  focus() {
    if (this.isDestroyed()) return;
    this.parent.contentView.addChildView(this.view);
    this.view.setVisible(true);
    this.parent.show();
    this.parent.focus();
    this.webContents.focus();
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.parent.removeListener('resize', this.resize);
    this.parent.removeListener('closed', this.parentClosed);
    if (!this.parent.isDestroyed()) this.parent.contentView.removeChildView(this.view);
    if (!this.webContents.isDestroyed()) this.webContents.close();
    this.emit('closed');
  }
}

module.exports = { SettingsView };
