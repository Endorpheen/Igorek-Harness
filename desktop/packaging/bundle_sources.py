"""Reviewed application source allowlist shared by all desktop targets."""

LEGAL_FILES = ('LICENSE', 'docs/THIRD_PARTY_NOTICES.md',
               'docs/licenses/CODEX-LICENSE.txt', 'docs/licenses/CODEX-NOTICE.txt',
               'docs/licenses/OPENCODE-ICON-LICENSE.txt')


APP_FILES = (
    'opencode-service.cjs', 'opencode-tasks.cjs', 'opencode-egress.cjs', 'opencode-workspace.cjs', 'opencode-sandbox.py', 'opencode-windows.py', 'opencode-kvm-guest.py', 'opencode-host-services-client.py',
    'browser-favorites.cjs', 'workspace-agent.cjs', 'completion-sound.cjs', 'completion-sound-settings.js',
    'release-updates.cjs', 'release-updates-ui.cjs',
    'whats-new.cjs', 'whats-new-preload.cjs', 'whats-new.html', 'whats-new.css', 'whats-new.js',
    'release-progress.cjs', 'release-progress-preload.cjs', 'release-progress.html', 'release-progress.css', 'release-progress.js', 'release-install.cjs', 'release-install.py', 'links.cjs', 'about.html', 'about.css', 'about.js',
    'main.cjs', 'session-preload.cjs', 'ui-navigation.cjs', 'mcp-task.cjs', 'policy.cjs', 'runtime.cjs', 'owner-policy.cjs', 'recovery-files.cjs', 'installed-image.cjs',
    'window-menu.cjs', 'window-chrome.js', 'window-chrome.css', 'diagnostics.html', 'diagnostics.js', 'diagnostics-preload.cjs',
    'owner-preload.cjs', 'owner-ui.js', 'onboarding-state.cjs', 'profile-cards.cjs', 'owner.html', 'owner.css',
    'browser.cjs', 'browser-preload.cjs', 'browser-ui.js', 'browser.html', 'browser.css',
    'browser-interaction.cjs', 'browser-reading.cjs',
    'notifications.cjs', 'notification-ui.js',
    'shortcuts.cjs', 'shortcuts-preload.cjs', 'shortcuts.html', 'shortcuts.css', 'shortcuts-ui.js',
    'github-reviews.cjs', 'github-controller.cjs', 'chatgpt-workspace.cjs', 'chatgpt-page-tools.cjs', 'chatgpt-permissions.cjs', 'workspace-browser.cjs', 'download-history.cjs',
)


BACKEND_FILES = ('opencode_profiles.py', 'opencode_host_services.py', 'host-service-runner.py', 'host-service-wangp.py', 'configure-host-service.py', 'mobile_access.py', 'codex_quota.py', 'chatgpt_connection.py', 'gmail_connection.py', 'private_mcp.py', 'mcp_access.py', 'backend.py', 'onboarding.py', 'runtime_logs.py', 'vault_owner.py', 'provider_setup.py', 'provider_probe.py', 'agent_runtime.py', 'isolation_readiness.py', 'image_setup.py', 'vault_egress.py', 'execution_mode.py', 'socket_bootstrap.py', 'qemu_access.py')
