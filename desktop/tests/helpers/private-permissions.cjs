const assert = require('node:assert/strict');
const { statSync } = require('node:fs');
const { execFileSync } = require('node:child_process');
const { join } = require('node:path');

function assertPrivatePermissions(file, mode = 0o600) {
  if (process.platform !== 'win32') {
    assert.equal(statSync(file).mode & 0o777, mode);
    return;
  }
  const script = `$ErrorActionPreference = 'Stop';
    $file = $env:IGOREK_TEST_ACL_PATH;
    $rules = if ([System.IO.Directory]::Exists($file)) {
      [System.IO.Directory]::GetAccessControl($file).Access
    } else { [System.IO.File]::GetAccessControl($file).Access };
    foreach ($rule in $rules) {
      $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value;
      if ($rule.AccessControlType -eq 'Allow' -and $sid -in @('S-1-1-0','S-1-5-11','S-1-5-32-545')) {
        throw 'Private fixture is accessible to other ordinary users';
      }
    }`;
  execFileSync(join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
    ['-NoProfile', '-NonInteractive', '-Command', script],
    {env: {...process.env, IGOREK_TEST_ACL_PATH: file}, stdio: 'pipe'});
}
module.exports = { assertPrivatePermissions };
