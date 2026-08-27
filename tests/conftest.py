"""Default env so importing the app in tests does not require a real .env."""

from __future__ import annotations

import os

os.environ.setdefault("QQ_APP_ID", "test-app-id")
os.environ.setdefault("QQ_APP_SECRET", "secretsecretsecretsecret1234")
os.environ.setdefault("LLM_API_KEY", "")
