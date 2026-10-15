"""WanGP adapter; machine paths and allowed tools belong to owner configuration."""


def build(config):
    from shared.api import init
    from shared.mcp_server import build_server_for_session
    session = init(root=config['app_root'], config_path=config['runtime_config'],
                   output_dir=config['outputs'], console_output=False, cli_args=())
    server = build_server_for_session(session,
        settings={'host': '127.0.0.1', 'json_response': True, 'stateless_http': True},
        api_version=1, allow_read_file_system=False, http_media_transfer=False,
        default_job_event_limit=10)
    return {'server': server, 'busy': lambda: bool(session.active_job and not session.active_job.done)}
