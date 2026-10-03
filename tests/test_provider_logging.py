"""Exercise application logging in isolation, including SDK import-time settings."""

import os
import subprocess
import sys

from cryptography.fernet import Fernet


def test_application_suppresses_sensitive_sdk_and_transport_logs() -> None:
    environment = {
        **os.environ,
        "OPENAI_LOG": "debug",
        "AI_WORKSPACE_JWT_SECRET_KEY": "test-jwt-secret-that-is-at-least-32-characters",
        "AI_WORKSPACE_CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
    script = """
import asyncio
import logging
from unittest.mock import patch

logging.basicConfig(level=logging.DEBUG)
for name in ('openai._base_client', 'httpx2._client', 'httpcore2.http11'):
    logging.getLogger(name).setLevel(logging.DEBUG)

from ai_workspace.main import app
from ai_workspace.providers import ModelMessage, ProviderError
from ai_workspace.providers import openrouter
import httpx2

key = 'sk-or-logging-secret-must-not-appear'
detail = 'private-upstream-logging-detail'
factory = openrouter.DefaultAsyncHttpxClient

async def run():
    for status in (200, 401):
        payload = (
            {
                'model': 'returned-model', 'status': 'completed',
                'output': [{
                    'type': 'message', 'role': 'assistant', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': 'Hello'}],
                }],
            }
            if status == 200 else {'error': {'message': detail + key}}
        )
        transport = httpx2.MockTransport(lambda request: httpx2.Response(
            status, json=payload, headers={'x-request-id': key}
        ))
        with patch.object(openrouter, 'DefaultAsyncHttpxClient',
                          lambda **kwargs: factory(transport=transport, **kwargs)):
            try:
                await openrouter.OpenRouterProvider().generate(
                    api_key=key, model='requested-model',
                    messages=[ModelMessage(role='user', content='Hello')],
                )
            except ProviderError:
                assert status == 401
    for name in ('openai', 'openai._base_client', 'httpx2._client', 'httpcore2.http11'):
        logging.getLogger(name).debug('%s %s', key, detail)
    logging.getLogger('ai_workspace').warning('Application logging remains enabled')

asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "Application logging remains enabled" in output
    assert "sk-or-logging-secret-must-not-appear" not in output
    assert "private-upstream-logging-detail" not in output
