# -*- coding: utf-8 -*-
"""나레이션 보충 문장(풀/outro/부분 보충) + TTS·SRT 렌더 중 거부 가드 점검. 실행: python test_narration_supplements.py"""
import server

BANNED = ("진짜 대박이에요", "후회 없을 거예요", "인증된 제품이에요", "많은 분들이 좋아하세요", "특가 곧 끝나요")
HEADLINES = ("환절기 속당김 그만", "건조피부 속보습 3중케어", "은은한 비누꽃향 순함", "150ml+150ml 2종세트",
             "리얼 사용후기 모음", "지금 특가로 만나요", "지금 바로 확인하기")
SLIDES = [{"headline": h} for h in HEADLINES]


def check_pools():
    for name, pool in (("일반", server._SUPPLEMENTS), ("식품", server._SUPPLEMENTS_FOOD)):
        lines = [t for ts in pool.values() for t in ts]
        assert 30 <= len(lines) <= 40, (name, len(lines))
        assert len(set(lines)) == len(lines), f"{name}: 풀 안에 중복 문장"
        assert not [t for t in lines if any(b in t for b in BANNED)], f"{name}: 금지 문구 남아있음"
        assert all(len(t) <= 17 for t in lines), f"{name}: 너무 긴 문장"
        # 체감·평가 어미는 쓰지 않는다 (스킬 outro 규칙: '~이에요/예요' 객관 서술)
        assert not [t for t in lines if t.rstrip(".?").endswith(("좋아요", "편해요", "좋겠어요"))], f"{name}: 체감 어미"


def check_pick():
    for food in (False, True):
        for n in (1, 4, 6, 7):
            for pname in ("닥터지 스네일 토너", "탐사 파워워시", "a", ""):
                got = server._pick_supplements(pname, n, food)
                assert len(got) == n and len(set(got)) == n, (food, n, pname, got)
                assert got == server._pick_supplements(pname, n, food)          # 같은 상품 = 같은 결과
    seven = {tuple(server._pick_supplements(f"상품{i}", 7, False)) for i in range(50)}
    assert len(seven) > 10, "상품별로 거의 안 달라짐"
    got = server._pick_supplements("x", 7, False)                                # 첫 장/마지막 장 역할
    assert got[0] in server._SUPPLEMENTS["궁금증"] and got[-1] in server._SUPPLEMENTS["행동유도"]


def check_outro():
    v = server._valid_outro
    assert v("  이 부분 눈여겨보세요.  ") == "이 부분 눈여겨보세요."
    assert v(None) == "" and v(123) == "" and v("") == ""
    assert v("이 문장은 열일곱 글자를 훨씬 넘어가는 아주 긴 마무리 문장이에요.") == ""       # 너무 김
    assert v("한 줄이\n두 줄이에요.") == ""                                              # 줄바꿈
    for bad in ("진짜 대박이에요.", "후회 없을 거예요.", "인증된 제품이에요.", "많은 분들이 좋아해요.",
                "서두르세요 특가 곧 끝나요", "효과 보장해요.", "무조건 써보세요.", "마감 임박이에요.",
                "지금이 좋은 타이밍이에요", "지금이 기회예요"):
        assert v(bad) == "", bad
    for bad in ("가볍게 매일 바르기 편해요", "선물하기도 좋아요", "두 제품 같이 쓰기 편해요", "쓰기 편하죠.",   # 체감 어미 자체
                "흡수가 좋네요", "보기 좋습니다.", "선물하기 좋겠어요", "좋아요!", "정말 편해요~"):
        assert v(bad) == "", bad
    for ok in ("선물용 구성이에요", "세트 구성이에요.", "가격은 링크에 있어요", "고르는 게 고민이에요",
               "편해요라는 후기가 있어요"):                                                # 어미가 아니면 통과 (중간에 들어간 건 OK)
        assert v(ok) == ok, ok
    for word in server._PROHIBITED_WORD_MAP:                                              # 절대적/최상급 표현
        assert v(f"{word}예요.") == "", word

    slides = [{"headline": h, "outro": o} for h, o in zip(HEADLINES, (
        "속당김이 궁금하시죠?",          # 유효 -> 사용
        "무조건 좋아요.",                 # 금지어 -> 풀 폴백
        "속당김이 궁금하시죠?",          # 영상 내 중복 -> 풀 폴백
        "",                               # 빈 값 -> 풀 폴백
        "이 문장은 열일곱 글자를 훨씬 넘어가는 아주 긴 문장이에요.",   # 길이 초과 -> 풀 폴백
        "가격은 링크에서 봐요.",          # 유효 -> 사용
        "링크에서 확인해보세요.",         # 유효 -> 사용
    ))]
    supps = server._supplement_texts(slides, "닥터지", 7)
    pool = server._pick_supplements("닥터지", 7, False)
    assert [supps[i] for i in (0, 5, 6)] == ["속당김이 궁금하시죠?", "가격은 링크에서 봐요.", "링크에서 확인해보세요."]
    assert [supps[i] for i in (1, 2, 3, 4)] == [pool[i] for i in (1, 2, 3, 4)], supps      # 폴백은 풀 그대로
    assert len(set(supps)) == 7, supps                                                       # 영상 내 중복 없음
    plain = server._supplement_texts(SLIDES, "닥터지", 7)                                   # outro 필드 없는 옛 state
    assert plain == pool, plain


def check_build_list():
    base = server._build_narration_list(SLIDES, "닥터지", 7)
    assert all(b for b in base) and len(base) == 7
    supps = server._supplement_texts(SLIDES, "닥터지", 7)
    assert server._build_narration_list(SLIDES, "닥터지", 7, "", supps, ()) == base          # 선택 없으면 보충 없음
    part = server._build_narration_list(SLIDES, "닥터지", 7, "", supps, [2, 4])              # 부분 보충
    assert [p == b + " " + s if i in (2, 4) else p == b for i, (p, b, s) in enumerate(zip(part, base, supps))] == [True] * 7
    assert not any(b in t for t in part for b in BANNED)


def _fake_tts(target, n=7, pause=0.6):
    """글자당 rate초 + 슬라이드 사이 쉼(pause)으로 발화하는 가짜 TTS (실측: 평균 rate는 쉼을 포함해 추가 글자당
    시간보다 크다). 첫 합성이 목표의 ratio0배가 되도록 rate를 잡고, 합성 때마다 비율을 calls에 기록한다."""
    chars0 = sum(len(t) for t in server._build_narration_list(SLIDES, "닥터지", n))

    def make(ratio0):
        rate = (target * ratio0 - pause * (n - 1)) / chars0
        calls = []

        def synth(narrs):
            sec = rate * sum(len(t) for t in narrs) + pause * (n - 1)
            calls.append(sec / target)
            return sec
        return synth, calls
    return make


def check_partial_plan():
    """부분 보충: 부족량 큰 슬라이드부터, 실측 비율이 안전범위(0.85~1.15)에 들어오면 즉시 중단 -> 배속이 정상범위."""
    lo, hi = server._ATEMPO_SAFE_MIN, server._ATEMPO_SAFE_MAX
    target, n = 21.0, 7
    base = server._build_narration_list(SLIDES, "닥터지", n)
    make = _fake_tts(target)
    for ratio0 in (0.65, 0.69, 0.75, 0.84):                                   # 1차 합성이 목표보다 짧은 경우들
        synth, calls = make(ratio0)
        narrs, actual, chosen = server._fit_narration(SLIDES, "닥터지", n, "", target, synth)
        assert len(calls) <= 1 + server._MAX_SUPP_ROUNDS, (ratio0, calls)     # TTS 호출 상한
        assert lo <= actual / target <= hi, (ratio0, calls, chosen)            # 보충 후 필요한 배속이 정상범위
        assert 0 < len(chosen) < n, (ratio0, chosen)                            # 일괄이 아니라 일부만
        assert all(c < lo for c in calls[:-1]), (ratio0, calls)                 # 범위 안에 들어온 즉시 중단
        assert [i for i, (a, b) in enumerate(zip(narrs, base)) if a != b] == sorted(chosen)   # 고른 슬라이드에만 보충
        assert chosen[0] == min(range(n), key=lambda i: len(base[i])), (ratio0, chosen)         # 부족량 1순위 = 가장 짧은 나레이션
    for ratio0 in (0.9, 1.0, 1.3):                                             # 이미 범위 안 / 목표보다 김 -> 재합성 없음
        synth, calls = make(ratio0)
        narrs, actual, chosen = server._fit_narration(SLIDES, "닥터지", n, "", target, synth)
        assert chosen == [] and len(calls) == 1 and narrs == base, (ratio0, calls, chosen)
    synth, calls = make(0.3)                                         # 다 붙여도 모자란 경우도 상한 안에서 끝남
    server._fit_narration(SLIDES, "닥터지", n, "", target, synth)
    assert len(calls) <= 1 + server._MAX_SUPP_ROUNDS and calls[-1] > calls[0], calls
    # _plan_supplements 단독: 이미 붙인 슬라이드는 제외
    supps = server._supplement_texts(SLIDES, "닥터지", n)
    first = server._plan_supplements(base, supps, 12.0, target)
    again = server._plan_supplements(base, supps, 12.0, target, exclude=first)
    assert first and not set(first) & set(again), (first, again)


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
    check_outro()
    check_build_list()
    check_partial_plan()
    check_render_guard()
    print("test_narration_supplements OK")
