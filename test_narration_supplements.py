# -*- coding: utf-8 -*-
"""나레이션 보충 문장 풀/중복 방지 + TTS 렌더 중 거부 가드 점검. 실행: python test_narration_supplements.py"""
import server

BANNED = ("진짜 대박이에요", "후회 없을 거예요", "인증된 제품이에요", "많은 분들이 좋아하세요", "특가 곧 끝나요")


def check_pools():
    for name, pool in (("일반", server._SUPPLEMENTS), ("식품", server._SUPPLEMENTS_FOOD)):
        lines = [t for ts in pool.values() for t in ts]
        assert 30 <= len(lines) <= 40, (name, len(lines))
        assert len(set(lines)) == len(lines), f"{name}: 풀 안에 중복 문장"
        assert not [t for t in lines if any(b in t for b in BANNED)], f"{name}: 금지 문구 남아있음"
        assert all(len(t) <= 17 for t in lines), f"{name}: 너무 긴 문장"


def check_pick():
    for food in (False, True):
        for n in (1, 4, 6, 7):
            for pname in ("닥터지 스네일 토너", "탐사 파워워시", "a", ""):
                got = server._pick_supplements(pname, n, food)
                assert len(got) == n and len(set(got)) == n, (food, n, pname, got)
                assert got == server._pick_supplements(pname, n, food)          # 같은 상품 = 같은 결과
    seven = {tuple(server._pick_supplements(f"상품{i}", 7, False)) for i in range(50)}
    assert len(seven) > 10, "상품별로 거의 안 달라짐"
    # 첫 장/마지막 장 역할
    got = server._pick_supplements("x", 7, False)
    assert got[0] in server._SUPPLEMENTS["궁금증"] and got[-1] in server._SUPPLEMENTS["행동유도"]


def check_narration_list():
    slides = [{"headline": h} for h in ("환절기 속당김 그만", "건조피부 속보습 3중케어", "은은한 비누꽃향 순함",
                                        "150ml+150ml 2종세트", "리얼 사용후기 모음", "지금 특가로 만나요", "지금 바로 확인하기")]
    short = server._build_narration_list(slides, "닥터지 스네일 토너", 7, 3.0)      # 3초 슬롯 = 보충 없음
    long_ = server._build_narration_list(slides, "닥터지 스네일 토너", 7, 4.5)      # 4초 이상 = 보충 붙음
    assert all(len(a) < len(b) for a, b in zip(short, long_)), (short, long_)
    tails = [b[len(a) + 1:] for a, b in zip(short, long_)]
    assert len(set(tails)) == 7, tails
    assert not any(b in t for t in long_ for b in BANNED)


def check_render_guard():
    """렌더링 중에는 TTS·SRT 둘 다 거부 (handlers는 가드가 맨 앞이라 body가 비어 있어도 거기서 멈춘다)."""
    for handler in (server.handle_generate_tts, server.handle_generate_srt):
        server._render_state = {"running": True, "done": False, "exitCode": None, "log": [], "finishedAt": None}
        try:
            handler(b"{}")
        except RuntimeError as e:
            assert "렌더링" in str(e), (handler.__name__, e)
        else:
            raise AssertionError(f"렌더링 중인데 {handler.__name__}이 거부되지 않음")
        finally:
            server._render_state = {"running": False, "done": False, "exitCode": None, "log": [], "finishedAt": None}


if __name__ == "__main__":
    check_pools()
    check_pick()
    check_narration_list()
    check_render_guard()
    print("test_narration_supplements OK")
