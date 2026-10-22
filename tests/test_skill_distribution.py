"""Distribution contracts: no real accounts, files, or vision providers."""
import json
import os
import subprocess
import sys
from pathlib import Path

from deepsee import installer
from deepsee.mcp_server import TOOLS


def test_skill_install_isolated_and_repeatable(tmp_path, monkeypatch):
    root = tmp_path / 'codex profile'
    monkeypatch.setenv('CODEX_HOME', str(root))
    sentinel = root / 'config.toml'
    root.mkdir()
    sentinel.write_text('unrelated = true\n')
    assert installer.install_codex_skill()
    target = root / 'skills' / 'deepsee'
    for name in ('SKILL.md', 'LICENSE'):
        assert (target / name).read_bytes() == (installer.SKILL_DIR / name).read_bytes()
    assert installer.install_codex_skill()
    assert sentinel.read_text() == 'unrelated = true\n'
    assert not (tmp_path / '.deepsee').exists()
    assert installer.uninstall_codex_skill()
    assert sentinel.exists()


def test_documented_mcp_examples_match_schema():
    examples = {
        'analyze_image': {'source': '/image.png', 'question': 'What failed?', 'task': 'error'},
        'ocr_image': {'source': '/image.png'},
        'compare_images': {'sources': ['/before.png', '/after.png'], 'question': 'Changes?'},
        'vision_status': {},
    }
    schemas = {t['name']: t['inputSchema'] for t in TOOLS}
    for name, args in examples.items():
        assert name in installer.SKILL_MD
        assert set(args) <= set(schemas[name]['properties'])
        assert set(schemas[name].get('required', [])) <= set(args)
    assert installer.SKILL_MD.startswith('---\nname: deepsee\ndescription:')
    assert 'untrusted data' in installer.SKILL_MD
    assert 'DEEPSEE_HOME' in installer.SKILL_MD


def test_stdio_status_does_not_disclose_credentials(tmp_path):
    env = dict(os.environ, DEEPSEE_HOME=str(tmp_path), DEEPSEE_VISION_API_KEY='TEST-PRIVATE-SENTINEL')
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'vision_status', 'arguments': {}}},
    ]
    result = subprocess.run([sys.executable, '-m', 'deepsee', 'mcp'],
        input=''.join(json.dumps(m)+'\n' for m in messages), text=True,
        capture_output=True, env=env, timeout=10, check=True)
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r['id'] for r in responses] == [1, 2, 3]
    assert responses[-1]['result']['isError'] is False
    assert 'TEST-PRIVATE-SENTINEL' not in result.stdout + result.stderr


def test_cli_skill_only_leaves_other_clients_untouched(tmp_path):
    profile = tmp_path / 'codex'
    private = tmp_path / 'private'
    env = dict(os.environ, HOME=str(tmp_path), CODEX_HOME=str(profile),
               DEEPSEE_HOME=str(private), CLAUDE_CONFIG_DIR=str(tmp_path / 'claude'))
    result = subprocess.run([sys.executable, '-m', 'deepsee', 'install', '--skill'],
        text=True, capture_output=True, env=env, timeout=10, check=True)
    assert (profile / 'skills/deepsee/SKILL.md').read_text() == installer.SKILL_MD
    assert not (tmp_path / 'claude').exists()
    assert not (tmp_path / '.config/opencode').exists()
    assert not (private / 'config.json').exists()
