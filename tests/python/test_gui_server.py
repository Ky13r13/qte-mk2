"""Launcher contracts without binding a real port or installing dependencies."""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from qte.gui import server


def test_headless_import_does_not_load_optional_gui_dependencies():
    result = subprocess.run(
        [sys.executable, '-c',
         'import qte,sys; assert not ({"starlette","uvicorn","markdown_it"} & set(sys.modules))'],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('port', [0, -1, 65536, True, '8765'])
def test_invalid_port_does_not_start_server(tmp_path, port):
    with pytest.raises(ValueError, match='port'):
        server.serve(tmp_path, port=port)


def test_missing_repository(tmp_path):
    with pytest.raises(ValueError, match='directory'):
        server.serve(tmp_path / 'missing')


def test_missing_optional_dependencies_is_actionable_without_install(monkeypatch, tmp_path):
    def unavailable(_name):
        raise ModuleNotFoundError('fixture missing dependency')
    monkeypatch.setattr(server.importlib, 'import_module', unavailable)
    with pytest.raises(ValueError, match='Nothing was installed'):
        server.serve(tmp_path)


def test_cli_gui_help_and_invalid_port():
    result = subprocess.run([sys.executable, '-m', 'qte', 'gui', '--help'],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert '--repo-root' in result.stdout
    assert '--host' not in result.stdout
    result = subprocess.run([sys.executable, '-m', 'qte', 'gui', '--port', '0'],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert 'GUI port' in result.stderr


def test_launcher_binds_loopback_before_code_and_uses_safe_server_settings(monkeypatch, tmp_path):
    events = []
    class Listener:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            events.append('closed')
        def setsockopt(self, *_):
            pass
        def bind(self, address):
            events.append(('bind', address))
        def listen(self, backlog):
            assert backlog == 128
        def setblocking(self, value):
            assert value is False
    listener = Listener()
    monkeypatch.setattr(server.socket, 'socket', lambda *_: listener)
    import qte.gui.app as app_module
    def make_app(repository, *, port, code_sink):
        assert repository == tmp_path
        assert events == [('bind', ('127.0.0.1', 9876))]
        code_sink('ephemeral-test-code')
        return 'test-app'
    monkeypatch.setattr(app_module, 'create_app', make_app)
    configs = []
    def config(app, **kwargs):
        assert app == 'test-app'
        configs.append(kwargs)
        return kwargs
    def run(*, sockets):
        assert sockets == [listener]
        events.append('ran')
    fake_uvicorn = SimpleNamespace(Config=config, Server=lambda _: SimpleNamespace(run=run))
    monkeypatch.setattr(server.importlib, 'import_module', lambda name: fake_uvicorn if name == 'uvicorn' else object())
    assert server.serve(tmp_path, port=9876, announce=events.append) == 0
    assert events[-2:] == ['ran', 'closed']
    assert configs[0]['host'] == '127.0.0.1'
    for option in ('reload', 'proxy_headers', 'access_log', 'server_header'):
        assert configs[0][option] is False
    assert configs[0]['workers'] == 1
    assert configs[0]['ws'] == 'none'


def test_occupied_port_fails_without_access_code_or_server(monkeypatch, tmp_path):
    class BusyListener:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def setsockopt(self, *_):
            pass
        def bind(self, address):
            assert address == ('127.0.0.1', 8765)
            raise OSError('fixture address already in use')
    monkeypatch.setattr(server.socket, 'socket', lambda *_: BusyListener())
    messages = []
    with pytest.raises(OSError, match='select --port explicitly'):
        server.serve(tmp_path, announce=messages.append)
    assert messages == []
