"""Stage file-bearing MCP calls behind the selected task's filesystem grant."""
from __future__ import annotations

from pathlib import Path
from itertools import islice

from .artifact_files import atomic_write_file, open_regular_file, private_io_directory

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 128 * 1024 * 1024


def run_fal_file_tool(client, name: str, arguments: dict, *, workdir: str, filesystem_mode: str) -> str:
    from .cc_backend_tools import _validate_read_path, _validate_write_path

    if not workdir:
        raise ValueError('A task workspace is required for file-bearing MCP tools')
    root = Path(workdir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('The task workspace is unavailable')
    args = dict(arguments)
    filename = str(args.get('filename') or '').strip()
    if filename and (Path(filename).is_absolute() or len(Path(filename).parts) != 1
                     or filename in {'.', '..'} or '\\' in filename or '\0' in filename):
        raise ValueError('MCP output filename must be a single basename')
    output = Path(str(args.get('output_dir') or root)).expanduser()
    output = output if output.is_absolute() else root / output
    output = Path(_validate_write_path(str(output), str(root), filesystem_mode))
    source = None
    if name == 'fal_edit_image':
        source = Path(str(args.get('image_path') or '')).expanduser()
        source = source if source.is_absolute() else root / source
        source = Path(_validate_read_path(str(source), str(root), filesystem_mode))
    with private_io_directory(root) as private:
        generated = private / 'output'
        generated.mkdir(mode=0o700)
        args['output_dir'] = str(generated)
        if filename:
            args['filename'] = filename
        if source is not None:
            snapshot = private / ('input' + source.suffix)
            with open_regular_file(source) as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError('MCP input image exceeds its byte limit')
            atomic_write_file(snapshot, data, mode=0o600, preserve_mode=False)
            args['image_path'] = str(snapshot)
        result = client.call_tool(name, args)
        files = list(islice(generated.iterdir(), 5))
        if len(files) > 4:
            raise ValueError('MCP output exceeds the image-count limit')
        total = 0
        for file in files:
            with open_regular_file(file) as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
            total += len(data)
            if len(data) > MAX_IMAGE_BYTES or total > MAX_OUTPUT_BYTES:
                raise ValueError('MCP output exceeds its byte limit')
            destination = _validate_write_path(str(output / file.name), str(root), filesystem_mode)
            atomic_write_file(destination, data)
        text = str(result).replace(str(generated), str(output))
        if source is not None:
            text = text.replace(str(snapshot), str(source))
        return text
