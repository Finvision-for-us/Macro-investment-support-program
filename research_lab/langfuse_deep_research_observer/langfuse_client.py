from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


class LangfuseConfigError(RuntimeError):
    pass


def get_langfuse_client(required: bool = True):
    env_path = Path(__file__).with_name(".env")
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    missing = [name for name, value in {
        "LANGFUSE_PUBLIC_KEY": public_key,
        "LANGFUSE_SECRET_KEY": secret_key,
    }.items() if not value]

    if missing:
        message = (
            "Langfuse upload requires environment variables: "
            + ", ".join(missing)
            + ". Local comparison still works without Langfuse keys."
        )
        if required:
            raise LangfuseConfigError(message)
        print(message, file=sys.stderr)
        return None

    try:
        try:
            from langfuse import Langfuse
        except ImportError:
            from langfuse.otel import Langfuse
    except ImportError as exc:
        message = "Install dependencies first: pip install -r requirements.txt"
        if required:
            raise LangfuseConfigError(message) from exc
        print(message, file=sys.stderr)
        return None

    return Langfuse(public_key=public_key, secret_key=secret_key, host=host)
