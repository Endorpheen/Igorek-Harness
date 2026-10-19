"""Require every native acceptance stage; an empty or partial report must fail."""
import json
from pathlib import Path
import sys

STAGES = {'packaged_file_tools_and_workspace_scope', 'clean_vault_and_verified_model_profile', 'running_worker_does_not_complete_first_result',
          'https_pairing_first_task_and_native_progress', 'lock_unlock_preserves_first_result',
          'full_restart_keeps_profile_cookie_history_and_first_result', 'continued_session_after_restart',
          'guided_demo_real_tools_summary_and_history', 'desktop_navigation_links_and_settings',
          'typed_diagnostics_native_notification_and_owned_recovery'}


def verify(directory):
    value = json.loads((Path(directory) / 'summary.json').read_text())
    if value.get('status') != 'passed' or set(value.get('stages', [])) != STAGES:
        raise ValueError('Native acceptance did not complete every required stage')
    return value


if __name__ == '__main__':
    verify(sys.argv[1])
    print('All native acceptance stages completed')
