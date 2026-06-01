from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False


@dataclass
class LangfuseState:
    enabled: bool
    client: Any | None = None
    host: str | None = None
    message: str | None = None


def get_langfuse_state() -> LangfuseState:
    local_env = Path(__file__).with_name(".env")
    if local_env.exists():
        load_dotenv(local_env)
    load_dotenv()

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        return LangfuseState(
            enabled=False,
            host=host,
            message="Langfuse disabled: LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY is missing.",
        )

    try:
        try:
            from langfuse import Langfuse
        except ImportError:
            from langfuse.otel import Langfuse
    except ImportError:
        return LangfuseState(
            enabled=False,
            host=host,
            message="Langfuse disabled: install dependencies with pip install -r requirements.txt.",
        )

    try:
        client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    except Exception as exc:
        return LangfuseState(enabled=False, host=host, message=f"Langfuse disabled: {exc}")

    return LangfuseState(enabled=True, client=client, host=host, message="Langfuse enabled.")


def warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)
