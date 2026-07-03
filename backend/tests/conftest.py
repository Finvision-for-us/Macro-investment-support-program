"""backend 테스트 공통 설정.

`app` 패키지를 어디서 pytest를 돌리든 import 가능하도록 backend 루트를 sys.path에 추가.
"""
import os
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)
