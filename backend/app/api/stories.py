import os
import sys
import json
import time
import threading
import subprocess
from collections import deque

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/stories")

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_STORIES_PATH = os.path.join(_ROOT, "data", "stories_latest.json")
_SCRIPT = os.path.join(_ROOT, "run_ingest2_web.py")

def _resolve_python() -> str:
    """파이프라인 실행용 파이썬 해석.

    백엔드는 전역 파이썬으로 뜨는 경우가 많아 수집 의존성(sklearn, genai 등)이
    없을 수 있다. 우선순위:
      1) INGEST2_PYTHON 환경변수 (명시 지정)
      2) 프로젝트 루트 .venv (의존성 설치된 곳)
      3) 백엔드와 동일 인터프리터 (sys.executable)
    """
    env = os.environ.get("INGEST2_PYTHON")
    if env:
        return env
    for rel in (os.path.join(".venv", "Scripts", "python.exe"),   # Windows
                os.path.join(".venv", "bin", "python")):           # POSIX
        cand = os.path.join(_ROOT, rel)
        if os.path.exists(cand):
            return cand
    return sys.executable


_PYTHON = _resolve_python()

_LOG_MAX = 40

# ── 백그라운드 잡 상태 ──────────────────────────────────────────────
_job_lock = threading.Lock()
_job: dict = {
    "status": "idle",        # idle | running | done | error
    "started_at": None,      # epoch sec
    "finished_at": None,     # epoch sec
    "returncode": None,
    "log": [],               # 최근 로그 줄 (최대 _LOG_MAX)
}


def _run_pipeline() -> None:
    """run_ingest2_web.py 를 서브프로세스로 돌리며 로그를 링버퍼에 스트리밍."""
    ring: deque = deque(maxlen=_LOG_MAX)
    try:
        proc = subprocess.Popen(
            [_PYTHON, "-u", _SCRIPT],
            cwd=_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except Exception as e:
        with _job_lock:
            _job["status"] = "error"
            _job["finished_at"] = time.time()
            _job["log"] = [f"실행 시작 실패: {e}"]
        return

    for line in proc.stdout:  # type: ignore[union-attr]
        line = line.rstrip()
        if not line:
            continue
        ring.append(line)
        with _job_lock:
            _job["log"] = list(ring)

    proc.wait()
    with _job_lock:
        _job["status"] = "done" if proc.returncode == 0 else "error"
        _job["returncode"] = proc.returncode
        _job["finished_at"] = time.time()


@router.get("/latest")
def get_stories_latest():
    try:
        with open(_STORIES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="stories_latest.json not found — 수집 파이프라인을 먼저 실행하세요",
        )


@router.post("/refresh")
def refresh_stories():
    """뉴스 수집 파이프라인 전체 재실행을 백그라운드로 시작."""
    with _job_lock:
        if _job["status"] == "running":
            raise HTTPException(status_code=409, detail="이미 수집이 실행 중입니다")
        _job.update(
            status="running",
            started_at=time.time(),
            finished_at=None,
            returncode=None,
            log=[],
        )
    threading.Thread(target=_run_pipeline, daemon=True).start()
    return {"status": "running"}


@router.get("/refresh/status")
def refresh_status():
    with _job_lock:
        elapsed = None
        if _job["started_at"]:
            end = _job["finished_at"] or time.time()
            elapsed = round(end - _job["started_at"], 1)
        return {
            "status": _job["status"],
            "returncode": _job["returncode"],
            "elapsed": elapsed,
            "log": _job["log"],
        }
