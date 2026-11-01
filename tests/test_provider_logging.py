"""在独立进程中验证应用日志策略，包括 SDK 导入时的配置。"""

import os
import subprocess
import sys

from cryptography.fernet import Fernet


def test_application_suppresses_sensitive_sdk_and_transport_logs() -> None:
    environment = {
        **os.environ,
        "OPENROUTER_DEBUG": "true",
        "AI_WORKSPACE_ENVIRONMENT": "development",
        "AI_WORKSPACE_DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:1/no_database_requests",
        "AI_WORKSPACE_JWT_SECRET_KEY": "test-jwt-secret-that-is-at-least-32-characters",
        "AI_WORKSPACE_CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
    script = """
import asyncio
import logging

logging.basicConfig(level=logging.DEBUG)
names = ('openrouter', 'httpx._client', 'httpcore.http11', 'httpx2._client', 'httpcore2.http11')
for name in names:
    logging.getLogger(name).setLevel(logging.DEBUG)

# 本测试只使用虚构配置，不加载本机 .env 或连接数据库。
from ai_workspace.core.config import Settings
from ai_workspace.learning.config import LearningSettings
Settings.model_config['env_file'] = None
LearningSettings.model_config['env_file'] = None
from ai_workspace.main import app
from ai_workspace.providers.errors import ProviderError
from ai_workspace.providers.openrouter import open_chat_model
import httpx

key = 'sk-or-logging-secret-must-not-appear'
detail = 'private-upstream-logging-detail'

async def run():
    for status in (200, 401):
        payload = (
            {
                'id': 'gen-fabricated', 'object': 'chat.completion', 'created': 1,
                'model': 'returned-model', 'system_fingerprint': None,
                'choices': [{
                    'index': 0, 'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': 'Hello'},
                }],
            }
            if status == 200 else {'error': {'message': detail + key}}
        )
        transport = httpx.MockTransport(lambda request: httpx.Response(
            status, json=payload, headers={'x-request-id': key}
        ))
        try:
            async with open_chat_model(
                api_key=key, model='requested-model', transport=transport
            ) as model:
                await model.ainvoke('Hello')
        except ProviderError:
            assert status == 401
    for name in names:
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
