# -*- coding: utf-8 -*-
"""
Pixabay Music API 응답 테스트 스크립트
실행: python test_pixabay.py

확인 사항:
  1. .env에서 PIXABAY_API_KEY 로드
  2. https://pixabay.com/api/music/ 호출
  3. 응답 전체 출력 (hit keys, audio 필드 구조 확인)
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

# ── .env에서 API 키 로드 ──────────────────────────────────────
def load_env_key(key: str) -> str:
    val = __import__("os").environ.get(key, "")
    if val:
        return val.strip()
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return ""

api_key = load_env_key("PIXABAY_API_KEY")
if not api_key:
    print("❌ PIXABAY_API_KEY가 .env에 없습니다.")
    sys.exit(1)

print(f"✅ API 키 로드 완료: {api_key[:8]}...")

# ── API 호출 테스트 ──────────────────────────────────────────
TEST_CASES = [
    {"q": "calm relaxing beauty", "genre": "piano"},
    {"q": "happy upbeat",         "genre": "upbeat"},
    {"q": "background music",     "genre": ""},
]

for tc in TEST_CASES:
    params = {"key": api_key, "q": tc["q"], "per_page": 3}
    if tc["genre"]:
        params["genre"] = tc["genre"]
    url = "https://pixabay.com/api/music/?" + urlencode(params)
    print(f"\n{'='*60}")
    print(f"요청 URL: {url}")
    print(f"{'='*60}")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw  = resp.read()
            data = json.loads(raw)

        total = data.get("total", 0)
        hits  = data.get("hits", [])
        print(f"✅ 응답 성공 | total={total} hits={len(hits)}")

        for i, hit in enumerate(hits):
            print(f"\n  [hit {i}] keys: {list(hit.keys())}")
            print(f"  id    : {hit.get('id')}")
            print(f"  title : {hit.get('title','?')}")
            # audio 필드 상세 출력
            audio = hit.get("audio")
            print(f"  audio : (type={type(audio).__name__}) {repr(audio)[:120]}")
            if isinstance(audio, dict):
                print(f"  audio keys: {list(audio.keys())}")

    except urllib.error.HTTPError as e:
        body = e.read(500).decode(errors="replace")
        print(f"❌ HTTP {e.code} {e.reason}")
        print(f"   응답 본문: {body}")
    except Exception as e:
        print(f"❌ 오류: {e}")

print("\n완료.")
