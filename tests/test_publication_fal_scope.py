"""FAL MCP adapters stage authorized inputs and atomically publish scoped files."""
import json
from pathlib import Path

import pytest

from factory.core import cc_backend_tools as tools
from factory.core.mcp_bridge import MCPClient


class FakeFal(MCPClient):
    def __init__(self):
        super().__init__('fal_image', 'unused')
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append(arguments)
        if name == 'fal_edit_image':
            assert Path(arguments['image_path']).read_bytes() == b'allowed input fixture'
        destination = Path(arguments['output_dir']) / str(arguments.get('filename') or 'generated.png')
        destination.write_bytes(b'image fixture')
        return str(destination)


@pytest.mark.parametrize('arguments', [{'filename': '../outside.png'}, {'filename': '/outside.png'},
                                      {'output_dir': '..'}, {'image_path': '../private.txt'}])
def test_file_arguments_cannot_escape_the_task_workspace(tmp_path, arguments):
    work = tmp_path / 'work'
    work.mkdir()
    (tmp_path / 'private.txt').write_text('private fixture')
    client = FakeFal()
    name = 'fal_edit_image' if 'image_path' in arguments else 'fal_generate_image'
    result = client.make_handler(name)(arguments, workdir=str(work), filesystem_mode='workspace')
    assert json.loads(result)['error'] == 'mcp_file_scope_denied'
    assert not client.calls


def test_allowed_image_edit_uses_a_private_snapshot_and_returns_the_real_output(tmp_path):
    source = tmp_path / 'source.png'
    source.write_bytes(b'allowed input fixture')
    client = FakeFal()
    result = client.make_handler('fal_edit_image')({'image_path': 'source.png', 'filename': 'edited.png'},
                                                  workdir=str(tmp_path), filesystem_mode='workspace')
    assert result == str(tmp_path / 'edited.png')
    assert Path(result).read_bytes() == b'image fixture'
    assert not Path(client.calls[0]['output_dir']).is_relative_to(tmp_path)
    assert not Path(client.calls[0]['image_path']).exists()


def test_late_input_link_swap_is_rejected_before_rpc(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    image = work / 'source.png'
    image.write_bytes(b'allowed input fixture')
    private = tmp_path / 'private.txt'
    private.write_bytes(b'private fixture')
    validate = tools._validate_read_path

    def swap(*args):
        path = validate(*args)
        image.unlink()
        image.symlink_to(private)
        return path

    monkeypatch.setattr(tools, '_validate_read_path', swap)
    client = FakeFal()
    result = client.make_handler('fal_edit_image')({'image_path': str(image)}, workdir=str(work))
    assert json.loads(result)['error'] == 'mcp_file_scope_denied'
    assert not client.calls


def test_output_directory_swap_after_rpc_cannot_redirect_publication(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    output = work / 'images'
    output.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    private = outside / 'generated.png'
    private.write_bytes(b'unchanged fixture')

    class SwapFal(FakeFal):
        def call_tool(self, name, arguments):
            result = super().call_tool(name, arguments)
            output.rename(work / 'previous')
            output.symlink_to(outside, target_is_directory=True)
            return result

    result = SwapFal().make_handler('fal_generate_image')({'output_dir': 'images'}, workdir=str(work))
    assert json.loads(result)['error'] == 'mcp_file_scope_denied'
    assert private.read_bytes() == b'unchanged fixture'
