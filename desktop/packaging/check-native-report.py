"""Require every native acceptance stage; an empty or partial report must fail."""
import json
from pathlib import Path
import sys

STAGES = {'search_settings_managed_owner_protocol', 'packaged_file_tools_and_workspace_scope', 'clean_vault_and_verified_model_profile', 'running_worker_does_not_complete_first_result',
          'https_pairing_first_task_and_native_progress', 'lock_unlock_preserves_first_result',
          'full_restart_keeps_profile_cookie_history_and_first_result', 'continued_session_after_restart',
          'compact_composer_and_demo_keyboard_autocomplete',
          'guided_demo_real_tools_summary_and_history', 'desktop_navigation_links_and_settings',
          'typed_diagnostics_native_notification_and_owned_recovery', 'voluntary_diagnostic_report_preview_and_handoff',
          'model_setup_can_be_deferred_for_opencode'}


def verify(directory):
    value = json.loads((Path(directory) / 'summary.json').read_text())
    if value.get('status') != 'passed' or set(value.get('stages', [])) != STAGES:
        raise ValueError('Native acceptance did not complete every required stage')
    return value


SEARCH_STAGES = {'settings_catalog_three_providers', 'exa_ui_ipc_vault_tool_dispatch',
    'serper_ui_ipc_vault_tool_dispatch', 'tavily_ui_ipc_vault_tool_dispatch',
    'failed_save_preserves_selection_and_notifies_only'}


def verify_search(directory):
    value = json.loads((Path(directory) / 'summary.json').read_text())
    layout = value.get('layout', {})
    if (value.get('status') != 'passed' or set(value.get('stages', [])) != SEARCH_STAGES
            or value.get('pageErrors') != [] or not layout.get('visible') or layout.get('overflow') is not False):
        raise ValueError('Native search acceptance did not complete every required stage')
    return value


if __name__ == '__main__':
    (verify_search if sys.argv[2:] == ["--search"] else verify)(sys.argv[1])
    print('All native acceptance stages completed')
