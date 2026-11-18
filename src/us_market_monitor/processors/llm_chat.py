"""Structured JSON chat backends for the caption processor.

* ClaudeChat  - the locally logged-in Claude Code CLI (`claude -p`, subscription, no API key).
                Runs headless in an empty temporary folder with no tools and a minimal environment,
                so it sees only the prompt and never this app's tokens. Prompt text goes through stdin
                (the Windows .cmd shim mangles non-ASCII arguments).
* OllamaChat  - the project-local Ollama server (previous default).

Both return the parsed JSON object, or raise ValueError(code) with a short snake_case code that
the caption processor records as the preparation failure.
"""
import json
import os
import shutil
import subprocess
import tempfile

_ENV_KEEP = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'HOME',
             'USERPROFILE', 'USERNAME', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES',
             'CLAUDE_CONFIG_DIR'}
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class ClaudeChat:
    def __init__(self, model='sonnet', timeout=300):
        self.cli_model = model
        self.model = f'claude-cli:{model}'      # part of the caption fingerprint
        self.timeout = timeout

    def chat(self, system, user, schema, error_code):
        exe = shutil.which('claude')
        if not exe:
            raise ValueError('claude_cli_missing')
        args = [exe, '-p', '--output-format', 'json', '--tools', '', '--strict-mcp-config',
                '--no-session-persistence', '--json-schema', json.dumps(schema, ensure_ascii=True),
                '--system-prompt', 'Follow the instructions at the top of the user message and answer only with the requested JSON.']
        if self.cli_model:
            args += ['--model', self.cli_model]
        prompt = '## Instructions\n' + system + '\n\n## Input (JSON)\n' + user
        env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEEP}
        with tempfile.TemporaryDirectory(prefix='radar-claude-') as cwd:
            try:
                proc = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding='utf-8',
                                      errors='replace', timeout=self.timeout, cwd=cwd, env=env,
                                      creationflags=_NO_WINDOW)
            except subprocess.TimeoutExpired:
                raise ValueError('claude_cli_timeout') from None
            except OSError:
                raise ValueError('claude_cli_start_failed') from None
        try:
            envelope = json.loads(proc.stdout)
        except ValueError:
            envelope = None
        if proc.returncode != 0 or not isinstance(envelope, dict) or envelope.get('is_error'):
            raise ValueError('claude_cli_failed')
        data = envelope.get('structured_output')
        if not isinstance(data, dict):
            text = str(envelope.get('result') or '')
            start, end = text.find('{'), text.rfind('}')
            try:
                data = json.loads(text[start:end + 1]) if 0 <= start < end else None
            except ValueError:
                data = None
        if not isinstance(data, dict):
            raise ValueError(error_code)
        return data


class OllamaChat:
    def __init__(self, preview):
        self.preview = preview
        self.model = preview.model
        self.timeout = preview.timeout

    def chat(self, system, user, schema, error_code, num_predict=600):
        response = self.preview.session.post(self.preview.base_url + '/api/chat', json={
            'model': self.preview.model, 'stream': False, 'think': False, 'format': schema,
            'keep_alive': '15m', 'options': {'temperature': 0, 'num_ctx': 16384, 'num_predict': num_predict},
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        }, timeout=(10, self.preview.timeout))
        response.raise_for_status()
        payload = response.json()
        if not payload.get('done') or payload.get('done_reason') == 'length':
            raise ValueError(error_code)
        return json.loads(payload['message']['content'])
