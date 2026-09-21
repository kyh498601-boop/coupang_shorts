# -*- coding: utf-8 -*-
"""
생활꿀템연구소 대시보드 서버  (port 3333)

GET  /              -> dashboard.html
GET  /<path>        -> static file
POST /generate-png  -> JSON {slides, images(base64[]), productName, category}
                       -> carousel_1080x1350.zip  (PNG x7)

[필수 수치 - 절대 변경 금지]
  PNG  : 1080 x 1350
  dpi  : (72, 72)
  font : C:/Windows/Fonts/malgun.ttf / malgunbd.ttf

2026-07-XX: 카드/뱃지 있던 상단60%+하단40% 고정 레이아웃을 폐기하고, 제품 사진이
캔버스 전체를 채우는 카드 없는 레이아웃으로 재설계 (render_slide/_render_slide_hero
참고, _full_bleed_photo_bg 공용 배경 + _tw_outline_text 외곽선 텍스트 직접 배치).

2026-07-16: _full_bleed_photo_bg가 제품 사진을 cover-crop으로만 채우던 방식이,
영상 변환(9:16) 시 재크롭 + Ken Burns 줌(1.05~1.18)까지 누적되어 제품이 과도하게
확대/절단되는 문제(필립스 면도기 테스트에서 확인)를 일으켜, 블러 배경(cover-crop)
+ 원본 전경(contain, 잘리지 않음) 2계층 구조로 재설계.
"""

import base64
import datetime
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import time
import traceback
import urllib.error
import urllib.request
import wave
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, urlencode

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

import community_post_helper
from community_post_template import generate_post
import instagram_api
import facebook_api
import coupang_collector

# ──────────────────────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────────────────────
PORT     = 3333
BASE_DIR = Path(__file__).parent
BGM_DIR  = BASE_DIR / "bgm"

# ── 렌더링 전역 상태 ────────────────────────────────────────────
import threading as _threading
_render_lock  = _threading.Lock()
_render_state: dict = {"running": False, "done": False, "exitCode": None, "log": [], "finishedAt": None}
_ANSI_RE = __import__("re").compile(r"\x1b\[[0-9;]*[mK]")

SLIDE_TOTAL = 7         # 2026-07-06: 10장 → 7장 구조로 재작성 (지침 STEP5 참조)

W           = 1080
H           = 1350
DPI         = (72, 72)

# 영상 컴포지션(1080x1920, 9:16)이 이 PNG(1080x1350, 4:5)를 objectFit:"cover"로
# 표시하면서 좌우 각각 약 160px(= W*(1-H/1920)/2)씩 잘려나간다 (ShoppingShorts.tsx
# KenBurnsSlide). PNG/영상 캔버스 비율은 지침대로 유지하고, 텍스트만 이 크롭선
# 밖으로 벗어나지 않도록 안전 여백을 둔다. 크롭 경계(160px)에 여유버퍼(약 60px)를
# 더해 최종 영상에서 텍스트가 프레임 경계에 바짝 붙지 않도록 한다.
SAFE_MARGIN_X = 220

FONT_REG  = "C:/Windows/Fonts/malgun.ttf"
FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"

# 브랜드 컬러
C_BRAND     = (46, 139,  87)   # #2E8B57
C_WHITE     = (255, 255, 255)

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css" : "text/css",
    ".js"  : "application/javascript",
    ".json": "application/json",
    ".png" : "image/png",
    ".jpg" : "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico" : "image/x-icon",
    ".mp4" : "video/mp4",
    ".txt" : "text/plain; charset=utf-8",
}


# ──────────────────────────────────────────────────────────────
# base64 data-URL -> PIL Image
# ──────────────────────────────────────────────────────────────
def b64_to_pil(data: str) -> Image.Image | None:
    try:
        m = re.match(r"data:image/[^;]+;base64,(.+)", data, re.DOTALL)
        raw = base64.b64decode(m.group(1) if m else data)
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
# 카드 없는 공용 배경 — 2계층 구조(2026-07-16 재설계).
#   1) 배경 레이어: 제품 사진을 캔버스 전체(1080x1350)에 cover로 꽉 채우고 가우시안
#      블러 처리 (제품 원본이 여기서 잘려도 무방 — 어차피 흐려져서 식별 대상이 아님).
#   2) 전경 레이어: 제품 사진을 자르지 않고(contain) 비율 유지한 채 캔버스 중앙에 배치.
#      제품이 실제로 보여야 하는 레이어라 여기서는 절대 크롭하지 않는다.
# 이전에는 배경 하나만 cover-crop으로 채웠는데, 그 결과물을 영상 변환(9:16) 시
# 재크롭 + Ken Burns 줌(1.05~1.18)까지 누적 적용하면 제품이 과도하게 확대/절단되어
# 식별 불가능한 수준으로 잘리는 문제가 있었다(필립스 면도기 테스트에서 확인, 2026-07-16).
# contain 전경 덕분에 제품 전체 실루엣이 항상 PNG 안에 온전히 담기고, 남는 여백은
# 블러된 배경이 채워 자연스러운 "블러 패딩" 룩이 된다. 텍스트 가독성 보조 그라데이션은
# 옛날 '쿠팡파트너스 인기 상품 자동수집 프로그램' 프로젝트(Documents\쿠팡파트너스...\
# shorts_creator\card_generator.py의 make_thumbnail_card())의 하단 그라데이션 기법을
# 상/하 양쪽에 적용한 것. _render_slide_hero()와 render_slide()가 공유한다.
# ──────────────────────────────────────────────────────────────
_BG_BLUR_RADIUS = 45  # 배경 레이어 가우시안 블러 반경(px) — 전경(제품)과 시각적으로 구분되는 정도

# 전경(제품) contain 배치 시 캔버스 꽉 채우지 않고 살짝 인셋(여백)을 두는 비율.
# 제품 사진에 따라 contain이 세로/가로 중 한쪽을 여백 0으로 캔버스 끝까지 채우는 경우가
# 있는데(예: 필립스 면도기 테스트 — 세로 483px 원본이 캔버스 세로 1350px에 꽉 맞아 상/하
# 여백이 0이 됨), 이 상태에서 영상 Ken Burns 줌(최대 1.09, ShoppingShorts.tsx KB_PATTERNS)이
# 중심 기준으로 확대하면 프레임 가장자리 쪽 4%가량이 밀려나가 제품 꼭대기가 살짝 잘리는
# 문제가 실측으로 확인됐다(2026-07-16). 그 4.13%(=(1-1/1.09)/2) 여유보다 넉넉하게 12%
# 인셋을 둬서 어떤 원본 비율이 와도 Ken Burns 최대 줌에서까지 제품 전체가 프레임 안에
# 남도록 한다.
_FG_INSET = 0.88

# 전경 contain 계산 전에 원본 사진의 상/하단을 미리 살짝 걷어내는 비율(임시 완화책,
# 2026-07-16 추가). 업로드되는 제품 사진 중 상단에 "항상 새 날처럼 날카롭게!" 같은
# 인포그래픽성 문구가 이미 박혀있는 경우가 있는데, cover-crop 시절엔 이 부분이 잘려서
# 안 보였지만 contain으로 바뀌면서 전체가 노출되어 새로 그리는 헤드라인/본문 텍스트와
# 겹치는 문제가 실제 슬라이드(필립스 휴대용 면도기 슬라이드6)에서 확인됐다. 원본에서
# 문구가 있을 가능성이 높은 상/하단 일부를 contain 계산 전에 미리 잘라내는 방식으로
# 대응한다 — 완벽한 대응은 아니고(문구 위치를 추정만 함) 정확한 해결은 추후 OCR 기반
# 텍스트 위치 자동 감지로 업그레이드 가능(이번 스코프 아님). 세로로 긴 제품처럼 사진
# 상/하단 끝까지 제품이 걸쳐 있으면 이 크롭으로 제품 일부가 함께 잘릴 수 있어, 비율을
# 보수적으로 작게 잡는다. _FG_INSET(레터박스 여백)과는 별개 단계 — 이 크롭이 먼저
# 적용된 뒤, 그 결과에 대해 contain + _FG_INSET이 계산된다.
# 2026-07-16 실측: 상단 13%는 실제 슬라이드6 인포그래픽 문구(필립스 휴대용 면도기)는
# 깔끔히 지웠지만, 세로로 긴 제품(필립스 PQ206, 회전날이 원본 최상단 불과 5~10px
# 아래에서 시작하는 케이스)에서는 회전날 윗부분이 실제로 잘리는 게 확인됨. 8%로
# 낮춰봐도 여전히 회전날이 일부 잘리고, 반대로 인포그래픽 문구는 다 안 지워져 새
# 헤드라인 뒤로 잔상이 비치는 절충 상태였다 — 하나의 전역 비율로는 두 케이스를 동시에
# 만족시킬 수 없음을 실측으로 확인(정확한 해결은 OCR 기반 자동 감지, 이번 스코프 아님).
# 최종적으로 제품 보존을 우선하기로 결정(사용자 결정, 2026-07-16) — 3%로 낮춰 PQ206류
# 제품은 거의 온전히 보존하고, 인포그래픽성 원본 문구는 일부만 가려질 수 있음(트레이드오프
# 감수, 심하면 헤드라인 문구가 원본 문구와 겹쳐 보일 수 있음).
_FG_TOP_CROP_PCT = 0.03
_FG_BOTTOM_CROP_PCT = 0.05


def _full_bleed_photo_bg(product_img: Image.Image | None,
                          top_grad_h: int = 0, top_grad_alpha: int = 0,
                          bottom_grad_h: int = 0, bottom_grad_alpha: int = 0) -> Image.Image:
    bg = Image.new("RGBA", (W, H), (30, 30, 30, 255))

    if product_img:
        src = product_img.convert("RGBA")
        # 원본에 투명/반투명 영역(알파채널)이 있으면 흰 배경으로 먼저 합성(flatten)해
        # 완전 불투명하게 만든다. 안 그러면 아래 paste가 crop 자체를 마스크로 써서 투명한
        # 부분에 이 함수 초기 캔버스색(30,30,30, 짙은 회색)이 그대로 비쳐 "검은 여백"처럼
        # 보인다 — rembg로 배경 제거됐거나 투명 패딩이 있는 PNG 원본에서 재현됨.
        # 배경/전경 레이어 둘 다 이 flatten된 src를 공유해서 투명 PNG여도 양쪽 다 안전하다.
        if src.getextrema()[3][0] < 255:
            flat = Image.new("RGB", src.size, (255, 255, 255))
            flat.paste(src, (0, 0), src)
            src = flat.convert("RGBA")
        sw, sh = src.size

        # 1) 배경 레이어 — cover-crop(캔버스 꽉 채움) + 블러. 여기서 잘리는 건 흐려서 안 보임.
        scale_cover = max(W / sw, H / sh)
        cdw, cdh = int(sw * scale_cover), int(sh * scale_cover)
        cover = src.resize((cdw, cdh), Image.LANCZOS)
        ccx, ccy = (cdw - W) // 2, (cdh - H) // 2
        cover_crop = cover.crop((ccx, ccy, ccx + W, ccy + H))
        blurred = cover_crop.filter(ImageFilter.GaussianBlur(_BG_BLUR_RADIUS))
        bg.paste(blurred, (0, 0), blurred)

        # 2) 전경 레이어 — contain(비율 유지, 잘리지 않음) + 소폭 인셋, 캔버스 중앙에 배치.
        # 제품이 실제로 식별되어야 하는 레이어라 크롭하지 않는다. 단, contain 계산 전에
        # 원본 상/하단 일부를 _FG_TOP_CROP_PCT/_FG_BOTTOM_CROP_PCT만큼 먼저 걷어내
        # 원본에 박힌 인포그래픽 문구와 새 헤드라인/본문이 겹치는 걸 완화한다.
        top_cut = int(sh * _FG_TOP_CROP_PCT)
        bottom_cut = int(sh * _FG_BOTTOM_CROP_PCT)
        crop_bottom_y = max(top_cut + 1, sh - bottom_cut)  # 극단적으로 작은 원본 대비 가드
        fg_src = src.crop((0, top_cut, sw, crop_bottom_y))
        fsw, fsh = fg_src.size

        scale_fit = min(W / fsw, H / fsh) * _FG_INSET
        fw, fh = max(1, round(fsw * scale_fit)), max(1, round(fsh * scale_fit))
        fit = fg_src.resize((fw, fh), Image.LANCZOS)
        fx, fy = (W - fw) // 2, (H - fh) // 2
        bg.paste(fit, (fx, fy), fit)

    if top_grad_h and top_grad_alpha:
        top_grad = Image.new("RGBA", (W, top_grad_h), (0, 0, 0, 0))
        tgd = ImageDraw.Draw(top_grad)
        for i in range(top_grad_h):
            tgd.line([(0, i), (W, i)], fill=(0, 0, 0, int(top_grad_alpha * (1 - i / top_grad_h))))
        bg.paste(top_grad, (0, 0), top_grad)

    if bottom_grad_h and bottom_grad_alpha:
        bottom_grad = Image.new("RGBA", (W, bottom_grad_h), (0, 0, 0, 0))
        bgd = ImageDraw.Draw(bottom_grad)
        for i in range(bottom_grad_h):
            bgd.line([(0, i), (W, i)], fill=(0, 0, 0, int(bottom_grad_alpha * (i / bottom_grad_h))))
        bg.paste(bottom_grad, (0, H - bottom_grad_h), bottom_grad)

    return bg


# ──────────────────────────────────────────────────────────────
# 1번 슬라이드 전용 — 카드/뱃지 없이 제품 사진 전체를 배경으로 채우고 그 위에 외곽선
# 텍스트를 직접 얹는 "히어로" 인트로 디자인 (2026-07-XX 재설계). 옛날 '쿠팡파트너스
# 인기 상품 자동수집 프로그램' 프로젝트(Documents\쿠팡파트너스...\shorts_creator\
# card_generator.py의 make_thumbnail_card()/_draw_text())의 카드 없는 스타일을 참고 —
# 배경 사진을 그대로 노출하고, 검은 외곽선 + 흰색/골드 텍스트만으로 가독성을 확보한다.
# 영상 첫 프레임으로 쓰이므로, YouTube Shorts가 PC/API 커스텀 썸네일을 지원하지 않는 정책
# 제약을 대신 보완한다. (_tw_* 헬퍼는 썸네일 생성 코드와 공유)
# ──────────────────────────────────────────────────────────────
def _render_slide_hero(slide: dict, product_img: Image.Image | None, product_name: str) -> bytes:
    bg = _full_bleed_photo_bg(product_img,
                               top_grad_h=480, top_grad_alpha=110,
                               bottom_grad_h=320, bottom_grad_alpha=140)
    draw = ImageDraw.Draw(bg)

    # 그리드(Home/탐색/구독/검색) 4:5 크롭 시 이 PNG 좌표 기준 y≈[205, 1145] 밖은
    # 잘려나간다 (영상(9:16)이 이 PNG(4:5)를 object-fit:cover하는 1단계 크롭을 역산한
    # 범위에 여유버퍼를 더한 값). 헤드라인/제품명/워터마크 전부 이 범위 안에 배치한다.
    # (idx≥1 슬라이드는 영상 재생 중간 프레임이라 이 그리드 크롭과 무관 — render_slide 참고)
    GRID_SAFE_TOP    = 280
    # 2026-07-14 2차 진단: Caption autofit(최대 2줄) 도입 후에도, KenBurnsSlide가 배경
    # 전체(이 PNG에 구운 텍스트 포함)에 scale(1.05~1.18) 확대를 적용하기 때문에 화면
    # 중심(y=960)보다 아래에 있는 제품명이 줌인될수록 아래로(자막 쪽으로) 더 밀려나는
    # 문제가 있었다. 실제 remotion의 interpolate/Easing을 그대로 써서 4개 Ken Burns
    # 패턴 × 전체 프레임을 전수 스캔한 결과, 최악값(패턴2 대각선, t=1, scale=1.18)
    # 기준 자막 3줄 폴백(최소폰트 34px) 상단과 70px 이상 마진을 두려면 990 이하가 필요
    # — 990으로 설정(마진 약 81px). 1100→1060(1차)→990(2차)로 두 번째 조정.
    GRID_SAFE_BOTTOM = 990

    # Hook 텍스트 — 상단부, 카드 없이 사진 위에 대형 외곽선 텍스트로 직접 배치 (최대 2줄)
    headline   = slide.get("headline") or slide.get("title") or product_name or "오늘의 생활꿀템"
    hook_short = _shorten_hook(headline, max_words=8)
    text_x     = SAFE_MARGIN_X
    text_w     = W - SAFE_MARGIN_X * 2
    f_hook, lines, line_h = _tw_auto_font(draw, hook_short, text_w,
                                          max_lines=2, size_max=72, size_min=36)
    hy = GRID_SAFE_TOP
    for line in lines:
        _tw_outline_text(draw, line, text_x, hy, f_hook,
                         fill=C_WHITE, outline=(0, 0, 0), ow=4)
        hy += line_h

    # 제품명 — 하단부, 골드 외곽선 텍스트, 가운데 정렬 (뱃지 대신 이 텍스트 자체가
    # 포인트 컬러 역할). PNG에 직접 굽던 워터마크 텍스트는 제거함 — Remotion의
    # <Watermark /> 컴포넌트가 영상 오버레이로 이미 항상 표시하고 있어 중복이었다
    # (ShoppingShorts.tsx 참고, 그쪽 컴포넌트는 그대로 유지).
    pname_max_w = W - SAFE_MARGIN_X * 2
    f_pname = _tw_font(28, bold=True)
    pname   = _truncate_to_width(draw, product_name or "제품명", f_pname, pname_max_w)
    pname_w = draw.textlength(pname, font=f_pname)
    pname_x = SAFE_MARGIN_X + (pname_max_w - pname_w) / 2
    _tw_outline_text(draw, pname, pname_x, GRID_SAFE_BOTTOM, f_pname,
                     fill=_TC_GOLD, outline=(0, 0, 0), ow=3)

    # 하단 진행바 (4px — 카드가 아니라 진행 상태 표시용 얇은 바)
    draw.rectangle([0, H - 4, W, H], fill=(224, 224, 224))
    draw.rectangle([0, H - 4, int(W * 1 / SLIDE_TOTAL), H], fill=_TC_GOLD)

    img = bg.convert("RGB")
    print(f"[slide 1] 히어로 PNG size: {img.size}")
    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=DPI)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────
# 슬라이드 1장 렌더  (1080 x 1350 PNG bytes)
# ──────────────────────────────────────────────────────────────
def render_slide(idx: int, slide: dict, product_img: Image.Image | None, product_name: str) -> bytes:
    if idx == 0:
        return _render_slide_hero(slide, product_img, product_name)

    # ── 1. 카드 없는 공용 배경 — 제품 사진이 캔버스 전체를 채우고, 상/하단에
    # 텍스트 가독성 보조 그라데이션 (히어로와 동일 기법, _full_bleed_photo_bg 공유).
    # idx≥1은 헤드라인+본문이 함께 들어가 히어로보다 상단 텍스트 블록이 커질 수 있어
    # top_grad_h를 더 크게 잡는다.
    bg = _full_bleed_photo_bg(product_img,
                               top_grad_h=560, top_grad_alpha=120,
                               bottom_grad_h=260, bottom_grad_alpha=130)
    draw = ImageDraw.Draw(bg)

    # idx≥1은 영상 재생 중간 프레임이라 그리드 정지썸네일 크롭과는 무관하지만(그리드
    # 크롭은 첫 프레임=히어로만 해당), 대신 Remotion의 자막(Caption, ShoppingShorts.tsx
    # bottom:150)과 겹치지 않아야 한다.
    # 2026-07-14 1차 진단: 자막이 배속 보정으로 길어지면 3줄까지 넘어가는 경우가 있어(표준
    # 7장 흐름에서도 흔함), Caption에 autofit(최대 2줄, 44px→34px)을 추가하고 1100→1060으로
    # 낮췄다. 2차 진단: 그것만으로는 부족했다 — KenBurnsSlide가 이 PNG 전체(구운 텍스트
    # 포함)에 scale(1.05~1.18)을 프레임마다 적용해서, 화면 중심(960)보다 아래인 제품명이
    # 줌인될수록 더 아래로(자막 쪽으로) 밀려나는 별개의 문제가 있었다. 실제 remotion
    # interpolate/Easing으로 4개 패턴×전체 프레임을 전수 스캔해 최악값(패턴2 대각선,
    # t=1, scale=1.18, panY=1.5%)을 구했고, 자막 3줄 폴백(34px) 상단과 70px+ 마진을
    # 두려면 970 이하가 필요 — 970으로 설정(마진 약 86px). 1100→1060→970 두 번째 조정.
    TOP_TEXT_Y    = 260
    BOTTOM_TEXT_Y = 970
    text_x        = SAFE_MARGIN_X
    text_w        = W - SAFE_MARGIN_X * 2

    # ── 2. Headline — 상단부, 카드 없이 사진 위에 외곽선 텍스트로 직접 배치.
    # 슬라이드마다 길이가 다른 카피가 들어오므로 _tw_auto_font로 최대 2줄 안에
    # 들어올 때까지 폰트를 축소해 겹침을 방지한다.
    headline = slide.get("headline") or slide.get("title") or ""
    f_head, head_lines, head_line_h = _tw_auto_font(draw, headline, text_w,
                                                     max_lines=2, size_max=54, size_min=30)
    hy = TOP_TEXT_Y
    for line in head_lines:
        _tw_outline_text(draw, line, text_x, hy, f_head,
                         fill=C_WHITE, outline=(0, 0, 0), ow=3)
        hy += head_line_h

    # ── 3. Body 카피 — 헤드라인 바로 아래, 마찬가지로 autofit(최대 3줄)으로
    # 본문 길이에 따라 폰트를 줄여가며 헤드라인 블록과 겹치지 않게 그린다.
    body = (slide.get("body") or "").strip()
    if body:
        body_y = hy + 20
        f_body, body_lines, body_line_h = _tw_auto_font(draw, body, text_w,
                                                         max_lines=3, size_max=32, size_min=22)
        by_ = body_y
        for line in body_lines:
            _tw_outline_text(draw, line, text_x, by_, f_body,
                             fill=(255, 244, 214), outline=(0, 0, 0), ow=2)
            by_ += body_line_h

    # ── 4. 제품명 — 하단부, 골드 외곽선 텍스트, 가운데 정렬 (히어로와 동일 스타일).
    # PNG에 직접 굽던 워터마크 텍스트는 제거함 — Remotion의 <Watermark /> 컴포넌트가
    # 영상 오버레이로 이미 항상 표시하고 있어 중복이었다 (ShoppingShorts.tsx 참고,
    # 그쪽 컴포넌트는 그대로 유지).
    pname_max_w = W - SAFE_MARGIN_X * 2
    f_pname = _tw_font(26, bold=True)
    pname   = _truncate_to_width(draw, product_name or "제품명", f_pname, pname_max_w)
    pname_w = draw.textlength(pname, font=f_pname)
    pname_x = SAFE_MARGIN_X + (pname_max_w - pname_w) / 2
    _tw_outline_text(draw, pname, pname_x, BOTTOM_TEXT_Y, f_pname,
                     fill=_TC_GOLD, outline=(0, 0, 0), ow=3)

    # ── 5. 하단 진행바 (4px) ───────────────────────────────────
    draw.rectangle([0, H - 4, W, H], fill=(224, 224, 224))
    draw.rectangle([0, H - 4, int(W * (idx + 1) / SLIDE_TOTAL), H], fill=C_BRAND)

    # ── 6. PNG bytes ────────────────────────────────────────────
    img = bg.convert("RGB")
    print(f"[slide {idx+1}] PNG size: {img.size}")
    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=DPI)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────
# /generate-png 핸들러
# ──────────────────────────────────────────────────────────────
def handle_generate_png(body: bytes) -> bytes:
    payload      = json.loads(body)
    slides       = payload.get("slides", [])
    images_b64   = payload.get("images", [])
    product_name = payload.get("productName", "")
    ab_headline  = (payload.get("abHeadline") or "").strip()

    # base64 -> PIL
    pil_images = [img for img in (b64_to_pil(b) for b in images_b64) if img]

    # 원본 제품 이미지를 파일로 저장 (썸네일 테스트 및 재사용용)
    if pil_images:
        orig_path = os.path.join(os.path.dirname(__file__), "product_original.png")
        pil_images[0].convert("RGB").save(orig_path, format="PNG")
        print(f"[PNG] 원본 제품 이미지 저장: {orig_path}", flush=True)

    if not slides:
        slides = [{"title": f"슬라이드 {i+1}", "headline": "", "body": ""} for i in range(SLIDE_TOTAL)]

    # 2026-07-06 버그 수정: STEP3 "썸네일 카피 A/B 선택"에서 고른 문구가 실제 PNG 1번
    # 슬라이드(히어로)에 전혀 반영되지 않던 문제 — abChoice가 요청에 아예 실려오지
    # 않았고, 실려왔어도 이 핸들러가 읽지 않아 slides[0]의 SLIDE_COPY 기본 헤드라인만
    # 항상 쓰였다. abHeadline이 오면 1번 슬라이드 헤드라인을 그 값으로 덮어쓴다.
    if ab_headline and slides:
        slides = [dict(slides[0], headline=ab_headline)] + list(slides[1:])
        print(f"[PNG] 1번 슬라이드 헤드라인을 A/B 선택 문구로 교체: {ab_headline!r}", flush=True)

    input_dir = BASE_DIR / "public" / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    for f in input_dir.glob("slide_*.png"):
        f.unlink()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, slide in enumerate(slides[:SLIDE_TOTAL]):
            img = pil_images[i % len(pil_images)] if pil_images else None
            png_bytes = render_slide(i, slide, img, product_name)
            name = f"slide_{i+1:02d}.png"
            zf.writestr(name, png_bytes)
            (input_dir / name).write_bytes(png_bytes)

    return buf.getvalue()


# ──────────────────────────────────────────────────────────────
# /generate-srt 핸들러
# ──────────────────────────────────────────────────────────────
import unicodedata

def ms_to_srt_time(ms: int) -> str:
    h  = ms // 3_600_000; ms %= 3_600_000
    m  = ms //    60_000; ms %=    60_000
    s  = ms //     1_000; ms %=     1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _clean(text: str) -> str:
    """이모지·특수기호 제거, 링크 단축, 말하기 부적합 문자 정리."""
    import re
    text = re.sub(r"http\S+", "", text)                   # URL 제거
    text = re.sub(r"(linktr\.ee|link\.inpock\.co\.kr)/\S+", "", text)  # 링크트리/인포크링크 제거
    text = re.sub(r"[→#@·•※①②③④⑤]", " ", text)          # 기호 → 공백
    # 이모지 제거 (유니코드 카테고리 So/Cs)
    text = "".join(
        ch for ch in text
        if unicodedata.category(ch) not in ("So", "Cs", "Mn")
        and not (0x1F300 <= ord(ch) <= 0x1FAFF)
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── 슬라이드 수 → 타이밍 자동 산정 ────────────────────────────────────────
# 반환: (ms_per_slide, total_min_sec, total_max_sec)
# 2026-07-06: 표준 슬라이드 수가 10장 → 7장으로 바뀌면서, 7장 기준 목표를
# "슬라이드당 3초 × 7장 = 21초"로 고정(min==max)한다. 4~6장 구간은 그대로 유지.
def _slide_timing(n: int) -> tuple[int, float, float]:
    if n <= 3:   return 5000, 15.0, 15.0   # 1~3장: 슬라이드당 5초, 총 15초
    elif n <= 6: return 4000, 20.0, 25.0   # 4~6장: 슬라이드당 4초, 총 20~25초
    else:        return 3000, n * 3.0, n * 3.0   # 7장(표준): 슬라이드당 3초, 총 n×3초 (7장=21초)


_CHARS_PER_SEC = 11.0   # 한국어 TTS 기준 초당 문자 수 (자연 발화 기준)

# 식품/식품_신선 카테고리 판별 — 이 카테고리일 때만 "사용"류(써보다/사용하다/경험하다) 어휘
# 대신 "먹다/드시다"류 어휘를 쓴다. 나머지 카테고리는 기존 어휘 세트를 그대로 사용한다.
_FOOD_CATEGORIES = {"식품", "식품_신선"}

# 긴 슬롯(4초 이상)에서 짧은 나레이션을 보완하는 보충 문장 — 역할별 풀.
# 슬라이드 위치에 따라 역할이 정해지고(_supplement_role), 한 영상 안에서는 같은 문장이 두 번 나오지
# 않게 뽑는다(_pick_supplements). 표시광고 리스크가 있는 과장·보장·인증·근거 없는 인기/긴급성 주장
# 문구("진짜 대박이에요", "후회 없을 거예요", "인증된 제품이에요", "많은 분들이 좋아하세요",
# "서두르세요, 특가 곧 끝나요")는 2026-09-21 사용자 결정으로 풀에서 영구 제거했다 —
# 새 문장을 추가할 때도 최상급·효과 보장·인증/수상 주장·근거 없는 인기/마감 주장·수치는 넣지 말 것.
# 모든 카테고리 공통 문장
_SUPP_COMMON = {
    "궁금증": [
        "어떤 점이 다른지 볼까요?",
        "이유가 궁금하시죠?",
        "하나씩 살펴볼게요.",
        "어떤 제품인지 알려드릴게요.",
        "이 부분 눈여겨보세요.",
        "궁금하셨던 부분이에요.",
    ],
    "공감": [
        "이런 고민 있으셨죠?",
        "이런 경험 한 번쯤 있으시죠?",
        "은근히 신경 쓰이는 부분이죠.",
        "고르기 쉽지 않으셨을 거예요.",
        "꼼꼼히 따져보고 싶으시죠?",
    ],
    "혜택": [
        "구성을 꼼꼼히 확인해보세요.",
        "가격도 꼼꼼히 비교해보세요.",
        "부담 없이 시작하기 좋아요.",
        "혜택은 링크에서 확인하세요.",
        "정보는 링크에서 확인하세요.",
        "정말 추천드려요.",
    ],
    "행동유도": [
        "지금 바로 확인해보세요.",
        "놓치지 마세요.",
        "링크에서 확인해보세요.",
        "관심 있다면 지금 확인하세요.",
        "저장해두고 비교해보세요.",
    ],
}
# 사용하는 상품(식품 외) 전용 — "써보다/경험하다"류
_SUPP_USE = {
    "궁금증": ["한번 써보시면 알 거예요.", "직접 써보면 어떨까요?"],
    "공감":   ["매일 쓰니 신경 쓰이죠.", "써보기 전엔 망설여지죠."],
    "혜택":   ["직접 경험해보시길 바라요.", "써보시고 비교해보세요."],
    "행동유도": ["지금 바로 써보세요.", "일단 한번 써보세요."],
}
# 식품 전용 — "먹다/드시다"류 (공통 문장은 음식과 무관한 범용 문구라 그대로 재사용)
_SUPP_EAT = {
    "궁금증": ["한번 드셔보면 알 거예요.", "직접 먹어보면 어떨까요?"],
    "공감":   ["매일 먹으니 신경 쓰이죠.", "먹어보기 전엔 망설여지죠."],
    "혜택":   ["직접 드셔보시길 바라요.", "드셔보시고 비교해보세요."],
    "행동유도": ["지금 바로 드셔보세요.", "일단 한번 드셔보세요."],
}
_SUPPLEMENTS      = {r: _SUPP_COMMON[r] + _SUPP_USE[r] for r in _SUPP_COMMON}   # 역할당 8개, 총 32개
_SUPPLEMENTS_FOOD = {r: _SUPP_COMMON[r] + _SUPP_EAT[r] for r in _SUPP_COMMON}


def _supplement_role(idx: int, n: int) -> str:
    """슬라이드 위치 -> 보충 문장 역할: 첫 장 궁금증, 마지막 장 행동유도, 가운데는 공감/혜택 교대."""
    if idx == 0:
        return "궁금증"
    if idx == n - 1:
        return "행동유도"
    return ("공감", "혜택")[(idx - 1) % 2]


def _pick_supplements(product_name: str, n: int, is_food: bool) -> list:
    """슬라이드 n장에 붙일 보충 문장을 뽑는다. 역할별 풀을 product_name 시드로 섞어 순서대로 꺼내므로
    한 영상 안에서 중복이 없고(풀이 역할 간에도 겹치지 않음), 같은 상품이면 항상 같은 결과다.
    (한 역할이 쓰이는 횟수는 슬라이드 수의 절반 정도 — 표준 7장이면 최대 3회라 풀 8개로 충분)"""
    pool = _SUPPLEMENTS_FOOD if is_food else _SUPPLEMENTS
    rng  = random.Random(product_name)
    deck = {role: rng.sample(lines, len(lines)) for role, lines in pool.items()}
    return [deck[_supplement_role(i, n)].pop() for i in range(n)]


# 슬라이드에 headline/body가 전혀 없을 때만 쓰는 최후 폴백(범용 문구). 2026-07-06까지는
# 이 배열이 나레이션의 유일한 소스였고 슬라이드 실제 내용(headline/body)과 무관하게
# 인덱스 위치만으로 고정 문구를 내보내 카테고리별 카피와 나레이션이 어긋나는 버그가 있었다
# (예: 3번 슬라이드가 "핵심 특징 & 안전인증"이어도 나레이션은 항상 "성분도 안전도
# 확실해요"). 지금은 `_narration_from_slide()`가 실제 headline/body를 요약해 우선 사용하고,
# 이 배열은 그 요약이 불가능할 때(빈 슬라이드)만 폴백으로 쓰인다.
_NARRATION_TEMPLATES = [
    lambda p: f"잠깐, 이거 꼭 봐야 해요",
    lambda p: f"이런 분들께 딱 맞아요",
    lambda p: f"핵심 포인트를 확인해요",
    lambda p: f"써보면 바로 차이 알아요",
    lambda p: f"자세한 정보를 알려드려요",
    lambda p: f"다들 극찬하는 이유가 있어요",
    lambda p: f"지금 바로 가격 확인하세요",
]

# 식품 전용 폴백 나레이션 — "사용"류 표현(3번 항목 "써보면 바로 차이 알아요")만
# "먹다/드시다"류로 교체. 나머지는 음식 여부와 무관해 그대로 재사용한다.
_NARRATION_TEMPLATES_FOOD = [
    lambda p: f"잠깐, 이거 꼭 봐야 해요",
    lambda p: f"이런 분들께 딱 맞아요",
    lambda p: f"핵심 포인트를 확인해요",
    lambda p: f"한입 드셔보면 알아요",
    lambda p: f"자세한 정보를 알려드려요",
    lambda p: f"다들 극찬하는 이유가 있어요",
    lambda p: f"지금 바로 가격 확인하세요",
]

# headline에 남아있는 화면 전용 기호 제거 규칙 + "&"/"/" 로 연결된 두 구절을 나누는 패턴.
# 자를 때 항상 "구절" 단위로만 잘라(단어 중간 절단 금지) "믿을 원재료" 같은
# 관형어+명사 조합이 "믿을"만 남고 잘리는 문법 파손을 막는다.
_NARRATION_STRIP_PATTERN = re.compile(r"[①②③④⑤\"'“”‘’→↔]")
_NARRATION_CLAUSE_SPLIT  = re.compile(r"\s*[&/]\s*")


def _has_batchim(char: str) -> bool:
    """한글 완성형 음절 1글자의 받침(종성) 유무를 판별한다.
    (코드포인트 - 0xAC00) % 28 == 0 이면 종성이 없는 음절(받침 없음).
    한글 완성형 범위(가~힣) 밖의 문자(영문/숫자/기호 등)는 받침 없는 것으로 간주한다."""
    if not char:
        return False
    code = ord(char) - 0xAC00
    if not (0 <= code <= 11171):  # 0x0 ~ 0x2BA3 (가~힣) 범위 밖 → 한글 완성형 아님
        return False
    return code % 28 != 0


def _narration_from_slide(slide: dict) -> str:
    """슬라이드의 실제 headline/body(SLIDE_COPY, dashboard.html에서 전달됨)를 12~15자
    내외 구어체 나레이션으로 요약한다. 카테고리가 바뀌어 headline/body 내용이 달라지면
    나레이션도 그에 맞춰 자동으로 달라진다 — 고정 인덱스 템플릿이 아니라 슬라이드
    내용 자체가 소스이기 때문. headline/body가 비어 있으면 빈 문자열을 반환해
    호출부(build_narration)가 _NARRATION_TEMPLATES 폴백을 쓰도록 한다.
    """
    headline = (slide.get("headline") or slide.get("title") or "").strip()
    body     = (slide.get("body") or "").strip()
    if not headline:
        return ""

    headline = _NARRATION_STRIP_PATTERN.sub("", headline).strip()
    parts = [p.strip() for p in _NARRATION_CLAUSE_SPLIT.split(headline) if p.strip()]
    text = parts[0] if parts else headline

    # "A & B" 형태 헤드라인이면, 여유가 있을 때만 두 번째 구절까지 구절 단위로 이어붙임
    for extra in parts[1:]:
        candidate = f"{text}이랑 {extra}"
        if len(candidate) <= 14:
            text = candidate
        else:
            break

    # 헤드라인만으로 너무 짧으면(8자 미만) 본문 첫 구절을 짧게 보완
    if len(text) < 8 and body:
        first_clause = re.split(r"[\n,.]", body)[0].strip()
        first_clause = _NARRATION_STRIP_PATTERN.sub("", first_clause).strip()
        if first_clause:
            candidate = f"{text} {first_clause}"
            if len(candidate) <= 17:
                text = candidate

    # 자연스러운 구어체 종결어미 보정 (이미 종결어미면 원문 그대로 둠, "하기"는 "해요"로 자연화)
    # "?"/"!" 등 말끝 문장부호가 남아있으면 종결어미 판정 전에 먼저 떼어내야
    # "맞을까요?" 뒤에 "예요"가 중복으로 덧붙는 걸 막을 수 있다 (문장부호 자체는 보존).
    had_question = text.endswith("?")
    bare = text.rstrip("?!")
    if had_question:
        # 물음표로 끝나는 헤드라인은 그 앞이 "는/은/가/이/까" 등 어떤 조사·어미로 끝나든
        # 그 자체로 이미 완결된 구어체 질문이라 코퓰러를 붙이면 안 됨
        # (예: "후기는?"+"이에요" → "후기는이에요" 파손, "인정?"+"이에요" → "인정이에요" 어색).
        text = bare + "?"
    elif bare.endswith("하기"):
        text = bare[:-2] + "해요"
    elif bare and not re.search(r"(요|죠|다|까)$", bare):
        # 받침 유무에 따라 "이에요"/"예요" 자동 분기 (예: "법" → 이에요, "차이" → 예요)
        particle = "이에요" if _has_batchim(bare[-1]) else "예요"
        text = bare + particle

    return text


# 표시광고법상 객관적 근거 없이 쓰면 공정위 규제 대상이 될 수 있는 절대적/최상급 표현.
# `02_Knowledge_상세규칙.md.md`의 체크리스트에는 "절대금지확인" 항목명만 있고 구체적
# 목록이 문서화돼 있지 않아(2026-07-XX 진단), 여기 코드에 명문화한다. 카테고리별 카피
# 생성 단계(category-copy-rules 스킬)에서도 동일 목록을 참고해 애초에 안 쓰도록 하지만,
# 이 필터는 나레이션/자막(TTS·captions.json) 최종 텍스트에 한해 사후 안전망으로 작동한다
# — headline/body(화면 PNG 텍스트)는 이 필터를 거치지 않으므로 카피 생성 단계가 1차 방어선.
_PROHIBITED_WORD_MAP = {
    "최저가": "특가",
    "역대급": "인기 많은",
    "최다판매": "인기 많은",
    "1위": "인기",
    "최고": "아주 좋은",   # 아래 패턴에 안 걸리는 나머지 경우를 위한 최후 안전망
    "베스트": "인기",
}

# "최고"는 명사라 뒤에 붙는 서술격 조사(코퓰러)·어미에 따라 그 어미까지 그대로 붙어버려
# 단순 단어 치환("최고"→"아주 좋은")만 하면 "아주 좋은예요"처럼 어색해진다. 실제 나레이션에
# 자주 나오는 어미 패턴을 통째로 자연스러운 구어체 문구로 먼저 바꾸고, 여기 안 걸리는
# 나머지 "최고"만 위 _PROHIBITED_WORD_MAP의 단순 치환으로 마지막 안전망을 건다.
_CHOEGO_PATTERNS = [
    (re.compile(r"최고예요"), "정말 좋아요"),
    (re.compile(r"최고에요"), "정말 좋아요"),   # 흔한 비표준 표기 변형도 함께 처리
    (re.compile(r"최고입니다"), "정말 좋습니다"),
    (re.compile(r"최고죠"), "정말 좋죠"),
    (re.compile(r"최고의"), "뛰어난"),          # 관형형 (예: "최고의 성능" → "뛰어난 성능")
    (re.compile(r"최고인"), "정말 좋은"),
]


def _filter_prohibited_words(text: str) -> str:
    """나레이션/자막 최종 텍스트에서 금지 표현을 발견하면 안전한 표현으로 치환한다.
    build_narration()이 문장을 완성한 직후 마지막 단계에서 호출해, TTS 본문과
    captions.json이 항상 같은 소스(build_narration)를 쓰는 이 코드베이스 구조상
    양쪽 다 자동으로 필터를 통과하게 한다."""
    filtered = text

    if "최고" in filtered:
        before = filtered
        for pattern, repl in _CHOEGO_PATTERNS:
            filtered = pattern.sub(repl, filtered)
        if filtered != before:
            print(f"[금지표현] '최고+어미' 패턴 치환 (원문: {text!r} → {filtered!r})", flush=True)

    for banned, safe in _PROHIBITED_WORD_MAP.items():
        if banned in filtered:
            print(f"[금지표현] {banned!r} → {safe!r} 자동 치환 (원문: {text!r})", flush=True)
            filtered = filtered.replace(banned, safe)
    return filtered


def build_narration(slide: dict, product_name: str, idx: int,
                    sec_per_slide: float = 2.5, category: str = "", supplement: str = "") -> str:
    """슬라이드 나레이션 텍스트를 만든다.

    문장을 중간에 잘라내지 않는다(완성 문장 보장) — TTS와 자막이 항상 같은 함수 결과를
    공유하므로, 여기서 자르면 음성도 자막도 같이 잘려서 의미가 끊긴 채로 끝난다.
    타이밍 보정은 텍스트 길이가 아니라 atempo(배속) 쪽에서 전담한다.
    - 짧은 슬롯: 기본 템플릿 그대로(트림 없음)
    - 긴 슬롯(4s 이상)이고 여유가 있으면: 보충 문장 추가 → 시간을 자연스럽게 채움

    2026-07-06: 나레이션 소스를 슬라이드 실제 headline/body(_narration_from_slide)로
    우선 사용하도록 변경 — 이전에는 _NARRATION_TEMPLATES[idx] 고정 문구만 써서 카테고리별
    카피(SLIDE_COPY)와 나레이션 내용이 어긋났다. headline/body가 없는 경우에만
    _NARRATION_TEMPLATES로 폴백한다.

    category가 식품/식품_신선이면 폴백 템플릿·보충 문장을 "먹다/드시다"류 어휘 세트
    (_NARRATION_TEMPLATES_FOOD/_SUPPLEMENTS_FOOD)로 바꿔 "실사용자"·"써보시면" 같은
    기기 사용을 연상시키는 표현이 나가지 않도록 한다.
    """
    p = product_name or "이 제품"
    is_food = category in _FOOD_CATEGORIES

    narration = _narration_from_slide(slide)
    if not narration:
        templates = _NARRATION_TEMPLATES_FOOD if is_food else _NARRATION_TEMPLATES
        template  = templates[idx] if idx < len(templates) else templates[-1]
        narration = template(p)

    narration = re.sub(r"[→#\*_`]", "", narration)
    # 필러(말 멈춤 표현) 제거: 다른 한글 음절에 붙어있지 않고 독립된 "음/아/어"(반복·물결 포함)만 제거
    # 자막-음성 1:1 일치를 위해 필러가 텍스트에 섞이지 않도록 항상 방어적으로 정리한다.
    narration = re.sub(r"(?<![가-힣])[음아어]+[~]*(?![가-힣])", "", narration)
    narration = re.sub(r"\s*,\s*,", ",", narration)          # 필러 제거로 남은 중복 쉼표 정리
    narration = re.sub(r"^[,.\s~]+", "", narration)          # 문두에 남은 군더더기 정리
    narration = re.sub(r"\s{2,}", " ", narration).strip()

    max_chars = int(sec_per_slide * _CHARS_PER_SEC)  # 보충 문장 추가 여부 판단 전용 (트림에는 더 이상 쓰지 않음)

    # 슬롯이 넉넉하고 여백이 있으면 보충 문장 추가 (문장을 자르는 게 아니라 채워서 시간 맞춤).
    # 보충 문장은 _build_narration_list가 영상 단위로 미리 뽑아(_pick_supplements) 넘겨준다 —
    # 슬라이드마다 따로 뽑으면 한 영상에서 같은 문장이 반복된다(2026-09-21 확인: "많은 분들이 좋아하세요" 3회).
    if supplement and sec_per_slide >= 4.0 and len(narration) < max_chars - 8:
        candidate = narration + " " + supplement
        if len(candidate) <= max_chars:
            narration = candidate

    narration = _filter_prohibited_words(narration)
    return narration


def _load_json_if_matches(path: Path, expected_len: int):
    """path가 존재하고 list이며 길이가 expected_len과 같으면 그 값을 반환, 아니면 None."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, list) and len(data) == expected_len:
        return data
    return None


# captions.json/slide_durations.json은 render.ps1이 순수 JSON 배열로 파싱하므로(.Count 등)
# 상품명 메타를 그 안에 직접 넣을 수 없다 — 대신 별도 세션 메타 파일로 "이 캐시가 어느
# 상품 것인지"를 추적하고, 상품명이 바뀌면 캐시 3종(자막/타이밍/나레이션)을 통째로 삭제한다.
_TTS_SESSION_META_PATH = BASE_DIR / "tts_session_meta.json"
_TTS_CACHE_FILES = ("captions.json", "slide_durations.json", "output_narration.wav")


def _invalidate_stale_tts_cache(product_name: str) -> bool:
    """세션 메타에 기록된 상품명과 다르면 자막/타이밍/나레이션 캐시 파일을 모두 삭제하고
    메타를 현재 상품명으로 갱신한다. 반환값: 무효화(삭제)가 실제로 일어났는지 여부."""
    prev_name = None
    if _TTS_SESSION_META_PATH.exists():
        try:
            prev_name = json.loads(_TTS_SESSION_META_PATH.read_text(encoding="utf-8")).get("productName")
        except Exception:
            prev_name = None

    if prev_name == product_name:
        return False

    for fname in _TTS_CACHE_FILES:
        fpath = BASE_DIR / fname
        if fpath.exists():
            fpath.unlink()
            print(f"[세션] 상품 전환 감지({prev_name!r} → {product_name!r}) → {fname} 삭제", flush=True)

    _TTS_SESSION_META_PATH.write_text(
        json.dumps({"productName": product_name}, ensure_ascii=False), encoding="utf-8")
    return True


def _reject_if_rendering(what: str) -> None:
    """렌더링 중이면 거부한다. TTS/SRT는 output_narration.wav · captions.json · slide_durations.json을
    덮어쓰는데, 렌더링 중에 하면 영상(자막·컷 타이밍)과 이후 BGM 믹싱에 쓰이는 음성이 서로 다른
    실행분이 된다 (2026-09-21 로그로 확인). handle_generate_bgm과 같은 방식의 가드."""
    with _render_lock:
        if _render_state.get("running"):
            raise RuntimeError(
                f"렌더링이 아직 진행 중입니다. 렌더링이 끝난 뒤 {what} 다시 생성해주세요 — "
                "지금 생성하면 영상 자막·타이밍과 음성이 서로 다른 나레이션이 됩니다."
            )


def handle_generate_srt(body: bytes) -> bytes:
    _reject_if_rendering("자막을")

    payload      = json.loads(body)
    slides       = payload.get("slides", [])
    product_name = payload.get("productName", "")
    category     = payload.get("category", "")

    if _invalidate_stale_tts_cache(product_name):
        print(f"[SRT] 상품 전환으로 이전 캐시 무효화됨 → {product_name!r} 기준으로 새로 생성", flush=True)

    n_slides     = min(len(slides), SLIDE_TOTAL) or SLIDE_TOTAL
    ms_per_slide, _, _ = _slide_timing(n_slides)
    sec_per_slide = ms_per_slide / 1000.0

    # TTS가 이미 생성되어 있으면(captions.json/slide_durations.json) 그 결과를 그대로 재사용해
    # 다운로드되는 .srt가 실제 음성 텍스트·길이와 항상 일치하도록 한다.
    # 없으면(아직 TTS 생성 전) 화면 타이밍 기준 균등 분배로 잠정 생성한다.
    cached_captions  = _load_json_if_matches(BASE_DIR / "captions.json", n_slides)
    cached_durations = _load_json_if_matches(BASE_DIR / "slide_durations.json", n_slides)

    narrations = cached_captions if cached_captions is not None else \
        _build_narration_list(slides, product_name, n_slides, sec_per_slide, category)
    slide_sec  = cached_durations if cached_durations is not None else [sec_per_slide] * n_slides

    timing_src = "실제 음성 길이 비례(slide_durations.json)" if cached_durations is not None else "화면 타이밍 균등 분배"
    text_src   = "TTS 최종 텍스트(captions.json)" if cached_captions is not None else "새로 생성"
    print(f"[SRT] 슬라이드 {n_slides}장 → 타이밍: {timing_src} / 텍스트: {text_src}", flush=True)

    lines = []
    cursor_ms = 0.0
    for i, narration in enumerate(narrations):
        start = cursor_ms
        end   = start + slide_sec[i] * 1000.0
        cursor_ms = end
        lines.append(str(i + 1))
        lines.append(f"{ms_to_srt_time(int(round(start)))} --> {ms_to_srt_time(int(round(end)))}")
        lines.append(_clean_caption_text(narration))  # 괄호/말줄임표 제거 (캐시된 값이어도 안전하게 재적용)
        lines.append("")

    # captions.json이 아직 없을 때만(=TTS 생성 전) 잠정 자막으로 저장.
    # TTS가 이미 만든 captions.json은 더 정확한 소스이므로 여기서 덮어쓰지 않는다.
    if cached_captions is None:
        _write_captions_json(narrations)

    return "\n".join(lines).encode("utf-8")


# ──────────────────────────────────────────────────────────────
# 환경변수 헬퍼 (.env 파일 폴백)
# ──────────────────────────────────────────────────────────────
def _get_env(key: str) -> str:
    val = os.environ.get(key)
    if val:
        return val.strip()
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return ""


# ──────────────────────────────────────────────────────────────
# /generate-tts 핸들러  (Typecast TTS)
# ──────────────────────────────────────────────────────────────

# Typecast TTS 전체 스크립트에 적용할 감정 프리셋.
# 주의: Typecast API는 요청 1건(전체 스크립트)당 emotion_preset 1개만 받는다 — 문장별 개별
# 감정 지정은 지원하지 않는다. 과거에는 "(힘있고 강조하며...)" 같은 한국어 지시문을 본문에
# 직접 끼워넣어 TTS가 그 지시문 자체를 대사처럼 읽어버리는 버그가 있었음 → 완전히 제거하고
# 유효한 emotion_preset 값(happy/normal/sad/angry/whisper/toneup/tonedown) 중 하나를
# prompt 파라미터로 분리 전달한다. 후킹·CTA·특가 등 강조 구간이 많아 toneup을 기본값으로 사용.
_TTS_EMOTION_PRESET   = "toneup"
# intensity가 높을수록 엔진이 호흡/감탄사 같은 애드리브성 표현을 더 많이 끼워넣는 경향이 있어
# (자막에는 없는 "아~" 같은 소리가 음성에만 섞이는 원인) 기본값(1.0)으로 낮춰 자막-음성 불일치를 줄인다.
_TTS_EMOTION_INTENSITY = 1.0


def _adjust_wav_speed(wav_path: Path, target_sec: float) -> tuple:
    """atempo로 WAV 속도 조정. 빠르게(최대 2.0x)·느리게(최소 0.5x) 모두 지원.
    목표 대비 ±2% 이내면 조정 생략.
    반환: (wav_bytes, final_sec, ratio) — ratio는 조정 전 실제/목표 비율(원본 속도 편차).
    """
    actual_sec = _get_audio_duration(wav_path)
    ratio = actual_sec / target_sec
    print(f"[TTS] 실제 길이: {actual_sec:.2f}s  목표: {target_sec:.2f}s  비율: {ratio:.3f}", flush=True)

    if abs(ratio - 1.0) <= 0.02:
        print("[TTS] 속도 조정 불필요 (±2% 이내)", flush=True)
        return wav_path.read_bytes(), actual_sec, ratio

    # atempo 유효 범위: 0.5 ~ 2.0
    speed = round(max(0.5, min(ratio, 2.0)), 4)
    direction = "빠르게" if speed > 1.0 else "느리게"
    print(f"[TTS] 속도 {speed}x ({direction}) 조정 중...", flush=True)

    tmp = wav_path.parent / "_tts_atempo_tmp.wav"
    cmd = ["ffmpeg", "-y", "-i", str(wav_path), "-af", f"atempo={speed}", str(tmp)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        tmp.replace(wav_path)
        actual_sec = _get_audio_duration(wav_path)
        print(f"[TTS] 조정 후 길이: {actual_sec:.2f}s", flush=True)
    else:
        print(f"[TTS] atempo 실패, 원본 유지: {res.stderr[:300]}", flush=True)
    return wav_path.read_bytes(), actual_sec, ratio


def _build_narration_list(slides: list, product_name: str, n_slides: int, sec_per_slide_for_text: float,
                           category: str = "") -> list:
    """슬라이드별 순수 나레이션 텍스트(감정 프리픽스 없음) 리스트.
    TTS 본문과 자막(captions.json)이 항상 같은 소스를 쓰도록 이 함수 하나로 통일한다.
    category는 build_narration()의 식품 전용 어휘("먹다/드시다"류) 분기에 쓰인다."""
    supplements = _pick_supplements(product_name, n_slides, category in _FOOD_CATEGORIES)
    return [
        build_narration(slides[i] if i < len(slides) else {}, product_name, i, sec_per_slide_for_text, category,
                        supplements[i])
        for i in range(n_slides)
    ]


def _build_tts_text(slides: list, product_name: str, n_slides: int, sec_per_slide_for_text: float,
                     category: str = "") -> str:
    """슬라이드별 나레이션을 빌드해 합친다. sec_per_slide_for_text는 글자 수 산정 전용
    (화면 표시 길이인 _slide_timing 결과와는 별개 — 재생성 시 대본 길이만 조정하기 위함).
    톤/감정 지시문은 본문에 섞지 않는다 (TTS에 보낼 때는 prompt 파라미터로 별도 전달 — _call_typecast_tts 참고).
    """
    narrations = _build_narration_list(slides, product_name, n_slides, sec_per_slide_for_text, category)
    return "\n\n".join(narrations)


def _clean_caption_text(text: str) -> str:
    """자막(화면 표시) 전용 클린업. 괄호류·말줄임표(트림 마커) 등 '표시 문자'를 제거해
    깔끔한 문장만 보이도록 한다. TTS로 보내는 원본 텍스트(쉼/포즈 유지)는 건드리지 않는다 —
    이 함수는 captions.json/SRT를 쓸 때만 적용한다."""
    cleaned = re.sub(r"[()\[\]{}]", "", text)   # 괄호류는 문자만 제거, 안의 내용은 유지
    cleaned = cleaned.replace("…", "")           # build_narration의 트림 마커(말줄임표) 제거
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"[,\s]+$", "", cleaned)    # 제거 후 끝에 남은 쉼표·공백 정리
    return cleaned


def _write_captions_json(narrations: list) -> None:
    """자막 표시용으로 클린업한 텍스트를 captions.json에 저장한다.
    (TTS에 실제로 들어간 narrations 원본은 호출부에서 별도로 보존해서 사용한다.)"""
    clean_narrations = [_clean_caption_text(t) for t in narrations]
    captions_path = BASE_DIR / "captions.json"
    captions_path.write_text(json.dumps(clean_narrations, ensure_ascii=False), encoding="utf-8")
    print(f"[Captions] 저장: {captions_path} ({len(clean_narrations)}개, 괄호/말줄임표 제거됨)", flush=True)


def _save_captions(slides: list, product_name: str, n_slides: int, sec_per_slide_for_text: float,
                    category: str = "") -> None:
    """슬라이드별 나레이션 텍스트를 captions.json으로 저장 (Remotion 자막 컴포넌트가 읽는 파일).
    handle_generate_tts가 실제로 사용한 최종 sec_per_slide_for_text로 호출해야
    음성과 자막 텍스트가 항상 일치한다."""
    narrations = _build_narration_list(slides, product_name, n_slides, sec_per_slide_for_text, category)
    _write_captions_json(narrations)


def _save_slide_durations(narrations: list, total_sec: float) -> None:
    """슬라이드별 화면 노출 시간을 '실제 음성 글자수 비례'로 분배해 slide_durations.json에 저장.
    완전한 단어 단위 타임스탬프는 아니지만, 균등분배보다 실제 발화 길이에 훨씬 가깝다.
    render.ps1이 이 파일을 읽어 슬라이드별 프레임 수를 다르게 적용한다.
    """
    lengths   = [max(len(t), 1) for t in narrations]
    total_len = sum(lengths)
    durations_sec = [round(total_sec * (l / total_len), 3) for l in lengths]
    # 반올림 오차를 마지막 슬라이드에서 보정해 합계가 total_sec과 정확히 일치하도록 함
    diff = round(total_sec - sum(durations_sec), 3)
    durations_sec[-1] = round(durations_sec[-1] + diff, 3)

    path = BASE_DIR / "slide_durations.json"
    path.write_text(json.dumps(durations_sec), encoding="utf-8")
    print(f"[Captions] 슬라이드별 노출 시간(글자수 비례, 실제 음성 {total_sec:.2f}s 기준): "
          f"{durations_sec}", flush=True)


_TYPECAST_TTS_URL = "https://api.typecast.ai/v1/text-to-speech"


def _call_typecast_tts(full_text: str, voice_id: str, api_key: str) -> bytes:
    """Typecast TTS를 호출해 WAV bytes를 반환.
    audio_tempo는 항상 1.0로 고정 — 속도 보정은 _adjust_wav_speed(자체 atempo 로직)에서만 수행해
    "엔진 자체 배속"과 "우리 쪽 동기화 배속"이 이중으로 겹치지 않도록 한다.
    """
    req_body = json.dumps({
        "voice_id": voice_id,
        "text": full_text,
        "model": "ssfm-v30",
        "prompt": {
            "emotion_type": "preset",
            "emotion_preset": _TTS_EMOTION_PRESET,
            "emotion_intensity": _TTS_EMOTION_INTENSITY,
        },
        "output": {
            "audio_format": "wav",
            "audio_tempo": 1.0,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        _TYPECAST_TTS_URL,
        data=req_body,
        method="POST",
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            wav_bytes = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"[Typecast] API 오류 {e.code}: {detail}")

    print(f"[TTS] Typecast 응답 크기: {len(wav_bytes)} bytes", flush=True)
    return wav_bytes


_ATEMPO_SAFE_MIN = 0.85   # 이 범위를 벗어나면 부자연스러운 배속으로 간주
_ATEMPO_SAFE_MAX = 1.15


def handle_generate_tts(body: bytes) -> tuple:
    # 렌더링 중 TTS를 다시 돌리면 output_narration.wav / captions.json / slide_durations.json이
    # 덮어써져 영상(자막·컷 타이밍)과 이후 BGM 믹싱에 쓰이는 음성이 서로 다른 실행분이 된다
    # (2026-09-21 로그로 확인). handle_generate_bgm과 같은 방식으로 명시적으로 차단한다.
    _reject_if_rendering("나레이션을")

    payload      = json.loads(body)
    slides       = payload.get("slides", [])
    product_name = payload.get("productName", "")
    category     = payload.get("category", "")
    voice_id     = payload.get("voiceId") or payload.get("voiceName") or _get_env("TYPECAST_VOICE_ID")

    if _invalidate_stale_tts_cache(product_name):
        print(f"[TTS] 상품 전환으로 이전 캐시 무효화됨 → {product_name!r} 기준으로 새로 생성", flush=True)

    # 슬라이드 수 기반 타이밍 자동 산정 (화면 길이 — 건드리지 않음)
    n_slides      = min(len(slides), SLIDE_TOTAL) or SLIDE_TOTAL
    ms_per_slide, tgt_min, tgt_max = _slide_timing(n_slides)
    sec_per_slide = ms_per_slide / 1000.0
    target_sec    = round((tgt_min + tgt_max) / 2.0, 3)   # 목표 나레이션 길이 = 화면 목표 구간의 평균
    print(f"[TTS] 슬라이드 {n_slides}장 → 슬라이드당 {sec_per_slide}초 / 화면 목표 {tgt_min}~{tgt_max}초 / 나레이션 목표 {target_sec}초", flush=True)

    api_key = _get_env("TYPECAST_API_KEY")
    if not api_key:
        raise ValueError("TYPECAST_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    if not voice_id:
        raise ValueError(
            "voice_id가 필요합니다. 요청 body의 voiceId 또는 .env의 TYPECAST_VOICE_ID를 설정하세요 "
            "(예: tc_xxxxxxxx — 빌트인 보이스 / uc_xxxxxxxx — 커스텀 클론)."
        )

    out_path = BASE_DIR / "output_narration.wav"

    # 1차 생성 (글자 수 산정은 화면 슬롯 기준 sec_per_slide 그대로 사용)
    text_sec_per_slide = sec_per_slide
    full_text = _build_tts_text(slides, product_name, n_slides, text_sec_per_slide, category)
    wav_bytes = _call_typecast_tts(full_text, voice_id, api_key)
    out_path.write_bytes(wav_bytes)
    print(f"[TTS] 저장 완료: {out_path} ({len(wav_bytes)//1024}KB)", flush=True)

    actual_sec = _get_audio_duration(out_path)
    pre_ratio  = actual_sec / target_sec
    print(f"[TTS] 1차 생성 길이: {actual_sec:.2f}s / 목표 {target_sec}s (비율 {pre_ratio:.3f})", flush=True)

    # atempo 비율이 0.85~1.15 범위를 벗어나면 자연스럽지 않음 → 글자 수 조정해서 한 번만 재생성
    if pre_ratio < _ATEMPO_SAFE_MIN or pre_ratio > _ATEMPO_SAFE_MAX:
        text_sec_per_slide = round(sec_per_slide / pre_ratio, 4)
        print(f"[TTS] 배속 범위 초과({pre_ratio:.3f}) → 글자 수 기준 재조정: "
              f"sec_per_slide {sec_per_slide}s → {text_sec_per_slide}s 로 재생성", flush=True)

        full_text = _build_tts_text(slides, product_name, n_slides, text_sec_per_slide, category)
        wav_bytes = _call_typecast_tts(full_text, voice_id, api_key)
        out_path.write_bytes(wav_bytes)
        actual_sec = _get_audio_duration(out_path)
        print(f"[TTS] 재생성 길이: {actual_sec:.2f}s / 목표 {target_sec}s (비율 {actual_sec / target_sec:.3f})", flush=True)

    # 최종 미세 조정 (±2% 밖이면 atempo로 보정)
    wav_bytes, actual_sec, _ = _adjust_wav_speed(out_path, target_sec)
    print(f"[TTS] 최종 길이: {actual_sec:.2f}s (목표 {target_sec}s)", flush=True)

    # 자막은 실제로 TTS에 들어간 최종 텍스트(text_sec_per_slide 기준)와 항상 동일하게 저장
    narrations_final = _build_narration_list(slides, product_name, n_slides, text_sec_per_slide, category)
    _write_captions_json(narrations_final)
    # 화면 노출 시간도 균등분배 대신 실제 음성 길이(actual_sec)를 글자수 비례로 분배
    _save_slide_durations(narrations_final, actual_sec)

    return wav_bytes, actual_sec


# ──────────────────────────────────────────────────────────────
# /generate-bgm · /change-bgm · /list-bgm · /upload-bgm
# bgm 폴더에서 음원을 스캔해 랜덤 선택 후 ffmpeg 믹싱
# ──────────────────────────────────────────────────────────────

_BGM_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def _scan_bgm_files() -> list:
    """bgm 폴더 전체(하위 포함)에서 오디오 파일 목록 반환."""
    if not BGM_DIR.exists():
        return []
    return sorted(f for f in BGM_DIR.rglob("*")
                  if f.is_file() and f.suffix.lower() in _BGM_EXTS)


def _pick_random_bgm(exclude: str = None) -> Path:
    """랜덤 BGM 파일 선택. exclude는 현재 파일명(재선택 시 제외)."""
    files = _scan_bgm_files()
    if not files:
        raise FileNotFoundError(
            f"bgm 폴더에 음원이 없습니다.\n"
            f"'{BGM_DIR}' 안에 mp3/wav 파일을 추가하세요."
        )
    if exclude and len(files) > 1:
        files = [f for f in files if f.name != exclude]
    return random.choice(files)


def _get_audio_duration(path: Path) -> float:
    """ffprobe로 오디오 파일 길이(초) 반환."""
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=10
    )
    return float(result.stdout.strip())


# 나레이션 목표 라우드니스 (유튜브 숏폼/릴스 기준 권장값)
_NARRATION_TARGET_LUFS = -16.0
# BGM 볼륨 슬라이더(5~50%, 기본 15%)는 그대로 두되, 백엔드에서 "나레이션 대비 dB 차"로 재해석한다.
# 슬라이더 기본값(0.15)일 때 -14dB가 나오도록 기준선을 잡고,
# 슬라이더를 올리고/내리면 그 기준선 대비 상대적으로(로그 스케일) 커지고/작아진다.
# (과거 -22dB + ratio=8 더킹 + amix 기본 normalize=true 3중 감쇠로 BGM이 거의 안 들렸던 문제 보정.
#  -17dB에서도 부족하다는 피드백으로 한 단계 더 올림)
_BGM_SLIDER_DEFAULT  = 0.15
_BGM_BASE_OFFSET_DB  = -14.0


def _bgm_slider_to_db(bgm_volume: float) -> float:
    """0.05~0.50 선형 슬라이더 값을 '나레이션 대비 dB'로 환산.
    슬라이더 기본값(0.15) = -22dB(권장 -20~-25dB 중간값), 그 외 값은 로그 스케일로 비례 이동.
    """
    ratio = max(bgm_volume, 0.001) / _BGM_SLIDER_DEFAULT
    return _BGM_BASE_OFFSET_DB + 20 * math.log10(ratio)


def _mix_bgm(bgm_path: Path, bgm_volume: float, out_path: Path) -> None:
    """ffmpeg으로 BGM + 영상(+ 선택적 나레이션) 믹싱.
    나레이션 있을 때:
      - 나레이션은 loudnorm으로 -16 LUFS 정규화 (TTS 엔진/대본 길이에 따라 들쭉날쭉하던 음량을 통일)
      - BGM은 정규화된 나레이션 대비 일정 dB만큼 작게(기본 -14dB, 슬라이더로 조정 가능)
      - sidechaincompress(threshold=0.15, ratio=3, 완전히 묻히지 않는 수준)로 나레이션 구간엔 BGM을 추가로 더킹
      - BGM은 나레이션 끝 후 1초 페이드아웃
    나레이션 없을 때: 영상 길이 기준으로 BGM 루프 (기존 동작 유지).
    """
    mp4_path = BASE_DIR / "output" / "shopping-shorts.mp4"
    if not mp4_path.exists():
        raise FileNotFoundError(
            f"MP4가 없습니다. render.bat를 먼저 실행하세요.\n경로: {mp4_path}"
        )

    # 2026-07-06: mp4_path는 고정 경로지만 ffmpeg가 호출 시점마다 디스크에서 새로 읽으므로
    # "캐시" 문제는 아니다 — 실제 원인은 이 경로에 있는 파일 자체가 최신 렌더링인지
    # 확인하지 않고 그대로 믹싱한다는 것. mtime을 로그로 남겨 어떤 영상을 믹싱했는지
    # 서버 로그에서 바로 확인할 수 있게 한다 (handle_generate_bgm의 실행 중 가드와 세트).
    mp4_mtime = os.path.getmtime(mp4_path)
    print(f"[BGM] 믹싱 대상 영상: {mp4_path} (마지막 렌더링: {time.ctime(mp4_mtime)})", flush=True)

    narr_path = BASE_DIR / "output_narration.wav"
    has_narr  = narr_path.exists()
    vol_str   = str(round(bgm_volume, 3))
    aformat   = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"

    if has_narr:
        # 나레이션 길이 측정 → BGM 트림·페이드아웃 기준점 계산
        narr_dur    = _get_audio_duration(narr_path)
        fade_start  = round(narr_dur, 3)          # 나레이션 끝 시점
        total_dur   = round(narr_dur + 1.0, 3)    # 페이드아웃 1초 포함
        print(f"[BGM] 나레이션 길이: {narr_dur:.2f}s → 출력 길이: {total_dur:.2f}s", flush=True)

        bgm_db = round(_bgm_slider_to_db(bgm_volume), 2)
        print(f"[BGM] 슬라이더={bgm_volume} → 나레이션({_NARRATION_TARGET_LUFS} LUFS) 대비 {bgm_db}dB "
              f"+ 더킹(sidechaincompress) 적용", flush=True)

        # BGM: 슬라이더 기반 dB 게인 → 나레이션 길이까지 루프 → 끝 1초 페이드아웃
        # 나레이션: loudnorm으로 -16 LUFS 정규화 → 더킹용 사이드체인 신호와 실제 믹스용으로 분기
        # 사이드체인 신호는 BGM의 페이드아웃 꼬리(total_dur)까지 무음 패딩해 더킹 단계에서
        # sidechaincompress가 BGM을 나레이션 길이에 맞춰 일찍 잘라내지 않도록 한다.
        # amix는 기본적으로 normalize=true라서 합산 시 두 입력을 클리핑 방지용으로 추가로
        # 깎아버린다 — 우리가 dB로 맞춰둔 상대 밸런스가 또 한 번 줄어들어 BGM이 묻히는 원인이었음.
        # normalize=0으로 끄고, 대신 alimiter로 클리핑만 안전하게 막는다.
        # 주의: alimiter도 기본 level=true(자동 레벨링)라서 입력 음량과 무관하게 출력을
        # 항상 limit 근처로 끌어올려버려 dB 밸런스를 또 지워버린다 — level=0으로 반드시 꺼야
        # "피크 안전장치"로만 동작하고, 우리가 맞춘 상대 볼륨이 그대로 유지된다.
        # threshold=0.05(-26dB)는 loudnorm으로 커진 나레이션(-16 LUFS)이 거의 항상 넘어버려서
        # BGM을 추가로 ~7.8dB나 더 깎는 원인이었다 — threshold=0.15(-16.5dB), ratio=3으로
        # 완화해 진짜 큰 소리에만 반응하고 추가 감쇠는 ~2.2dB 수준으로 줄였다.
        fc = (
            f"[0:a]volume={bgm_db}dB,{aformat},"
            f"atrim=end={total_dur},"
            f"afade=t=out:st={fade_start}:d=1.0[bgm_pre];"
            f"[2:a]loudnorm=I={_NARRATION_TARGET_LUFS}:TP=-1.5:LRA=11,{aformat}[narr_pre];"
            f"[narr_pre]asplit=2[narr_mix][narr_sc0];"
            f"[narr_sc0]apad=whole_dur={total_dur}[narr_sc];"
            f"[bgm_pre][narr_sc]sidechaincompress=threshold=0.15:ratio=3:attack=20:release=400:makeup=1[bgm_ducked];"
            f"[bgm_ducked][narr_mix]amix=inputs=2:duration=longest:dropout_transition=2:normalize=0[mixed];"
            f"[mixed]alimiter=limit=0.97:level=0[a]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(bgm_path),
            "-i", str(mp4_path),
            "-i", str(narr_path),
            "-filter_complex", fc,
            "-map", "1:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", str(total_dur),
            str(out_path),
        ]
        print("[BGM] 모드: 영상 + BGM(더킹) + 나레이션(loudnorm -16 LUFS) (나레이션 기준 길이)", flush=True)
    else:
        # 나레이션 없음: 영상 길이 기준으로 BGM 루프
        fc = f"[0:a]volume={vol_str},{aformat}[a]"
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(bgm_path),
            "-i", str(mp4_path),
            "-filter_complex", fc,
            "-map", "1:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out_path),
        ]
        print("[BGM] 모드: 영상 + BGM (영상 길이 기준)", flush=True)

    print(f"[BGM] ffmpeg: ...{' '.join(cmd[-6:])}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[BGM] ffmpeg stderr:", result.stderr[-600:], flush=True)
        raise RuntimeError(f"ffmpeg 오류:\n{result.stderr[-800:]}")
    print(f"[BGM] 완료: {out_path} ({out_path.stat().st_size // 1024}KB)", flush=True)


def handle_generate_bgm(body: bytes) -> tuple:
    """랜덤 BGM 선택 → 믹싱 → (mp4_bytes, bgm_name, source_mtime_iso) 반환.

    2026-07-06 버그 수정: BGM 믹싱은 output/shopping-shorts.mp4를 고정 경로로 쓰지만
    매 호출마다 ffmpeg가 디스크에서 새로 읽어가므로 "캐시"가 원인은 아니었다. 실제 원인은
    (1) 새 렌더링이 아직 진행 중인데 그 사이에 BGM 버튼을 눌러 이전 mp4가 믹싱되거나,
    (2) 이번 세션에서 렌더링을 아예 실행하지 않아 예전 mp4가 그대로 남아있는 경우였다.
    (1)은 여기서 명시적으로 차단하고, (2)는 소스 영상의 mtime을 응답에 실어 대시보드가
    "이 영상은 언제 렌더링됐는지" 사용자에게 보여줄 수 있게 한다."""
    with _render_lock:
        if _render_state.get("running"):
            raise RuntimeError(
                "렌더링이 아직 진행 중입니다. 렌더링이 끝난 뒤(STEP4 '✅ 렌더링 완료!') "
                "BGM을 추가해주세요 — 지금 추가하면 이전 영상에 BGM이 섞입니다."
            )

    payload    = json.loads(body)
    bgm_volume = float(payload.get("bgmVolume", 0.15))
    exclude    = payload.get("excludeBgm") or None

    bgm_path = _pick_random_bgm(exclude=exclude)
    out_path = BASE_DIR / "output_with_bgm.mp4"
    print(f"[BGM] 선택: {bgm_path.name}", flush=True)
    _mix_bgm(bgm_path, bgm_volume, out_path)

    source_mp4 = BASE_DIR / "output" / "shopping-shorts.mp4"
    source_mtime_iso = datetime.datetime.fromtimestamp(os.path.getmtime(source_mp4)).isoformat()
    return out_path.read_bytes(), bgm_path.name, source_mtime_iso


def handle_list_bgm() -> bytes:
    """bgm 폴더 파일 목록과 개수를 JSON으로 반환."""
    files = _scan_bgm_files()
    return json.dumps(
        {"count": len(files), "files": [f.name for f in files]},
        ensure_ascii=False
    ).encode()


def handle_upload_bgm(body: bytes, filename: str) -> bytes:
    """업로드된 음원을 bgm 폴더에 저장."""
    safe_name = Path(filename).name          # path traversal 방지
    if Path(safe_name).suffix.lower() not in _BGM_EXTS:
        raise ValueError(f"지원하지 않는 형식: {safe_name}")
    BGM_DIR.mkdir(parents=True, exist_ok=True)
    dest = BGM_DIR / safe_name
    dest.write_bytes(body)
    print(f"[BGM] 업로드 저장: {dest} ({len(body)//1024}KB)", flush=True)
    count = len(_scan_bgm_files())
    return json.dumps({"ok": True, "name": safe_name, "count": count},
                      ensure_ascii=False).encode()


# ──────────────────────────────────────────────────────────────
# 유튜브 썸네일 생성 — PIL 기반  (A안 / B안)  1280×720
#
# 스킬 적용:
#   youtube-thumbnail : Hook 최대 5단어, 2색 지배, 포컬 엘리먼트(뱃지), 우하단 금지
#   graphic-designer  : 고대비 텍스트, 1개 액센트 컬러, 의도적 레이아웃
#   frontend-design   : 대담한 구성, 비대칭 레이아웃, 브랜드 일관성
# ──────────────────────────────────────────────────────────────

TW, TH = 1280, 720   # 유튜브 썸네일 고정 해상도

# 썸네일 전용 컬러 팔레트
_TC_GREEN     = (46, 139,  87)   # #2E8B57 브랜드 그린
_TC_GREEN_DRK = (13,  43,  26)   # #0D2B1A 딥 다크 그린
_TC_GOLD      = (255, 215,   0)  # #FFD700 골드
_TC_RED       = (220,  38,  38)  # #DC2626 임팩트 레드
_TC_WHITE     = (255, 255, 255)
_TC_BLACK     = (  0,   0,   0)
_TC_CREAM     = (252, 250, 245)  # 크림 흰색 (A안 좌측 배경)


def _shorten_hook(text: str, max_words: int = 5) -> str:
    """Hook 문구 최대 5단어로 자동 단축 (youtube-thumbnail 스킬 규칙)."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip("!?.,:;") + "…"


def _truncate_to_width(draw, text: str, font, max_w: int) -> str:
    """글자수가 아니라 실측 픽셀 폭 기준으로 말줄임. 제품명처럼 옆에 워터마크 등
    다른 요소와 같은 줄을 공유하는 텍스트가 굵은 폰트에서 예상보다 넓게 그려져
    옆 요소를 침범하는 것을 방지한다. 공백이 있으면 단어 경계에서 먼저 잘라
    "인체..."처럼 단어 중간이 어정쩡하게 끊기지 않게 하고, 공백이 없거나 첫
    단어부터 안 들어가면 글자 단위로 자른다."""
    if draw.textlength(text, font=font) <= max_w:
        return text

    words = text.split(" ")
    if len(words) > 1:
        candidate = ""
        for w in words:
            trial = (candidate + " " + w).strip()
            if draw.textlength(trial + "…", font=font) <= max_w:
                candidate = trial
            else:
                break
        if candidate:
            return candidate + "…"

    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return (text + "…") if text else "…"


def _tw_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    except Exception:
        return ImageFont.load_default()


def _tw_remove_bg(img: Image.Image) -> Image.Image:
    """rembg로 배경 제거 → 투명 RGBA 반환.
    알파 임계값(200) 처리로 반투명 잔상 완전 제거.
    rembg 미설치 또는 오류 시 원본 그대로 반환 (폴백).
    """
    try:
        from rembg import remove as _rembg_remove
        print("[썸네일] rembg 배경 제거 시작...", flush=True)
        buf_in = io.BytesIO()
        img.convert("RGBA").save(buf_in, format="PNG")
        result = _rembg_remove(buf_in.getvalue())
        out = Image.open(io.BytesIO(result)).convert("RGBA")
        r, g, b, a = out.split()
        # 1단계: 확실한 배경뿐 아니라 rembg가 애매하게(중간 알파) 인식한 잔상까지
        # 완전 제거 (예: 분리된 벗겨진 껍질처럼 낮은 신뢰도로 반투명 인식된 영역).
        # 실측: 잔상 알파 33~153(평균 107) vs 실제 제품 코어 250+ → 200이면 잔상은
        # 전부 제거되면서 제품 경계 안쪽은 영향받지 않음.
        a = a.point(lambda v: 0 if v < 200 else v)
        # 2단계: 알파 채널만 미세 블러 → 계단 현상 없는 매끄러운 엣지
        a = a.filter(ImageFilter.GaussianBlur(radius=0.8))
        # 3단계: 블러로 낮아진 최대값 복원 (엣지 안쪽은 완전 불투명 유지)
        a = a.point(lambda v: min(255, int(v * 1.15)))
        print("[썸네일] rembg 완료", flush=True)
        return Image.merge("RGBA", (r, g, b, a))
    except ImportError:
        print("[썸네일] rembg 미설치 → 원본 사용 (pip install rembg[cpu])", flush=True)
        return img.convert("RGBA")
    except Exception as e:
        print(f"[썸네일] rembg 실패 → 원본 사용: {e}", flush=True)
        return img.convert("RGBA")


def _tw_enhance(img: Image.Image) -> Image.Image:
    """제품 이미지 밝기·대비·선명도 자동 보정 (고선명)."""
    img = ImageEnhance.Brightness(img).enhance(1.05)
    img = ImageEnhance.Contrast(img).enhance(1.25)
    img = ImageEnhance.Sharpness(img).enhance(2.50)   # 선명도 대폭 강화
    # UnsharpMask로 엣지 추가 강화
    rgb = img.convert("RGB")
    rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.2, percent=180, threshold=2))
    if img.mode == "RGBA":
        r, g, b, a = img.split()
        nr, ng, nb = rgb.split()
        return Image.merge("RGBA", (nr, ng, nb, a))
    return rgb


def _tw_fit(src: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """비율 유지 contain 리사이즈."""
    sw, sh = src.size
    scale  = min(box_w / sw, box_h / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    return src.resize((nw, nh), Image.LANCZOS)


def _tw_outline_text(draw, text, x, y, font, fill, outline, ow=5):
    """텍스트 + 외곽선 렌더링 (고대비 가독성)."""
    for dx in range(-ow, ow + 1):
        for dy in range(-ow, ow + 1):
            if dx == 0 and dy == 0:
                continue
            if abs(dx) + abs(dy) <= ow + 2:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def _tw_wrap(draw, text: str, font, max_w: int) -> list[str]:
    """단어 단위 줄바꿈 (한글/영어 혼합 대응)."""
    words = text.split()
    lines, line = [], ""
    for w in words:
        test = (line + " " + w).strip()
        if draw.textlength(test, font=font) <= max_w:
            line = test
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines or [text]


def _tw_badge_size(font_size: int, label: str) -> tuple[int, int]:
    """뱃지 (width, height)만 계산 — 드로잉 없음. 위치 결정용."""
    font   = _tw_font(font_size, bold=True)
    tmp    = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    tw     = int(tmp.textlength(label, font=font))
    pad_x, pad_y = 20, 10
    return tw + pad_x * 2, font_size + pad_y * 2


def _tw_badge(draw, x: int, y: int, label: str,
              bg_color, text_color=_TC_WHITE, font_size: int = 26):
    """둥근 모서리 뱃지 렌더링 (텍스트 정중앙 정렬). 반환: (width, height)."""
    font     = _tw_font(font_size, bold=True)
    tw       = int(draw.textlength(label, font=font))
    pad_x, pad_y = 20, 10
    w, h     = tw + pad_x * 2, font_size + pad_y * 2
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=bg_color)
    # 수평 중앙
    text_x  = x + (w - tw) // 2
    # 수직 중앙 — textbbox로 실제 글자 높이 측정 후 offset 보정
    bbox    = draw.textbbox((0, 0), label, font=font)
    th      = bbox[3] - bbox[1]
    text_y  = y + (h - th) // 2 - bbox[1]
    draw.text((text_x, text_y), label, font=font, fill=text_color)
    return w, h


def _tw_auto_font(draw, text: str, max_w: int, max_lines: int = 2,
                  size_max: int = 92, size_min: int = 38) -> tuple:
    """텍스트 길이에 맞는 폰트 크기 자동 결정.
    size_max → size_min 순으로 줄여가며 max_lines 이내로 들어오는 크기 반환.
    반환: (font, lines, line_height)
    """
    for size in range(size_max, size_min - 1, -4):
        font  = _tw_font(size, bold=True)
        lines = _tw_wrap(draw, text, font, max_w)
        if len(lines) <= max_lines:
            return font, lines[:max_lines], int(size * 1.18)
    # 최소 크기로 강제
    font  = _tw_font(size_min, bold=True)
    lines = _tw_wrap(draw, text, font, max_w)[:max_lines]
    return font, lines, int(size_min * 1.18)


def _tw_center_text(draw, line: str, font, area_x: int, area_w: int) -> int:
    """텍스트를 area_x~area_x+area_w 내 가운데 정렬한 x 좌표 반환."""
    tw = int(draw.textlength(line, font=font))
    return area_x + max(0, (area_w - tw) // 2)


def _render_thumb_a(product_img: Image.Image | None,
                    hook_text: str, sub_text: str) -> bytes:
    """A안 — 제품 중심 스플릿 레이아웃.

    좌측 55%: 흰색 배경 + 제품 이미지 크고 선명 (밝기/대비 보정)
    우측 45%: #2E8B57 브랜드 그린 + 대형 흰색 Hook 텍스트 (가운데 정렬)
    뱃지: 🔥 오늘의 추천 (골드, 우측 상단 가운데 정렬)
    하단: 전체 폭 골드 브랜드 바
    """
    img  = Image.new("RGB", (TW, TH), _TC_WHITE)
    draw = ImageDraw.Draw(img)

    BAR_H   = 58
    split_x = int(TW * 0.55)

    # 우측 브랜드 그린 배경
    draw.rectangle([split_x, 0, TW, TH], fill=_TC_GREEN)

    # 좌우 경계 — 골드 포인트 라인 (3px)
    draw.rectangle([split_x, 0, split_x + 3, TH], fill=_TC_GOLD)

    # ── 제품 이미지 (좌측 영역, contain 방식, 최대 크기) ─────
    if product_img:
        prod   = _tw_enhance(_tw_remove_bg(product_img))
        PAD    = 12                          # 최소 여백으로 이미지 최대화
        box_w  = split_x - PAD * 2          # 좌우 PAD 제외
        box_h  = TH - BAR_H - PAD * 2       # 상하 PAD 모두 제외
        fitted = _tw_fit(prod, box_w, box_h) # contain: 비율 유지, 잘림 없음
        fw, fh = fitted.size
        ox     = PAD + (box_w - fw) // 2     # 좌우 중앙
        oy     = PAD + (box_h - fh) // 2     # 상하 중앙 (상단 PAD 기준)

        if fitted.mode != "RGBA":
            fitted = fitted.convert("RGBA")
        img.paste(fitted, (ox, oy), fitted)
        draw = ImageDraw.Draw(img)

    # ── 우측 텍스트 영역 ──────────────────────────────────────
    MARGIN  = 20
    tx      = split_x + MARGIN        # 텍스트 영역 시작 x
    text_w  = TW - tx - MARGIN        # 텍스트 영역 너비

    # 뱃지 — 텍스트 영역 좌상단 고정 (좌측 여백 20px, 상단 여백 20px)
    BADGE_LABEL = "🔥 오늘의 추천"
    _, bh = _tw_badge(draw, tx + 20, 20, BADGE_LABEL,
                      bg_color=_TC_GOLD, text_color=(20, 60, 20), font_size=28)

    # Hook 텍스트 — 폰트 크기 자동 조절, 최대 2줄, 가운데 정렬
    f_hook, lines, line_h = _tw_auto_font(draw, hook_text, text_w,
                                          max_lines=2, size_max=88, size_min=38)
    total_h = len(lines) * line_h
    # 뱃지 아래 여백 확보 후 수직 중앙 정렬
    text_top    = 28 + bh + 16
    avail_h     = TH - BAR_H - text_top
    hy          = text_top + max(0, (avail_h - total_h) // 2)

    for line in lines:
        lx = _tw_center_text(draw, line, f_hook, tx, text_w)
        _tw_outline_text(draw, line, lx, hy, f_hook,
                         fill=_TC_WHITE, outline=_TC_BLACK, ow=4)
        hy += line_h

    # 서브 텍스트 (최대 1줄, 가운데 정렬)
    if sub_text:
        f_sub  = _tw_font(28, bold=False)
        sub_s  = _shorten_hook(sub_text, max_words=8)
        sub_ln = _tw_wrap(draw, sub_s, f_sub, text_w)[:1]
        for sl in sub_ln:
            slx = _tw_center_text(draw, sl, f_sub, tx, text_w)
            draw.text((slx, hy + 10), sl, font=f_sub, fill=(200, 245, 215))

    # ── 하단 브랜드 바 (전체 폭, 골드) ───────────────────────
    draw.rectangle([0, TH - BAR_H, TW, TH], fill=_TC_GOLD)
    f_brand = _tw_font(32, bold=True)
    brand   = "🍀 생활꿀템연구소"
    bw2     = int(draw.textlength(brand, font=f_brand))
    draw.text(((TW - bw2) // 2, TH - BAR_H + 13), brand,
              font=f_brand, fill=(20, 60, 35))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _render_thumb_b(product_img: Image.Image | None,
                    hook_text: str, sub_text: str) -> bytes:
    """B안 — 텍스트 임팩트 레이아웃.

    배경: 다크 그라디언트 (좌 딥블랙 → 우 딥그린)
    제품 이미지: 우측 60% 크게 배치, 좌측 소프트 페이드
    좌측: 골드 Hook 텍스트 + 검정 외곽선 (가운데 정렬)
    뱃지: ⚡ 지금 특가 (레드, 좌측 상단 가운데 정렬)
    하단: 전체 폭 브랜드 그린 바
    """
    # 배경 — 좌→우 다크 그라디언트
    img  = Image.new("RGB", (TW, TH), _TC_GREEN_DRK)
    draw = ImageDraw.Draw(img)
    r0, g0, b0 = _TC_GREEN_DRK
    r1, g1, b1 = (27, 94, 59)
    for x in range(TW):
        t = x / (TW - 1)
        c = (int(r0 + (r1 - r0) * t),
             int(g0 + (g1 - g0) * t),
             int(b0 + (b1 - b0) * t))
        draw.line([(x, 0), (x, TH)], fill=c)

    BAR_H       = 58
    img_start_x = int(TW * 0.42)   # 제품이미지 영역 시작 x
    img_area_w  = TW - img_start_x  # 제품이미지 영역 너비

    # ── 제품 이미지 (우측 영역, contain 방식) ───────────────
    if product_img:
        prod   = _tw_enhance(_tw_remove_bg(product_img))
        PAD    = 16
        box_w  = img_area_w - PAD * 2       # 좌우 PAD 제외
        box_h  = TH - BAR_H - PAD * 2       # 상하 PAD 모두 제외
        fitted = _tw_fit(prod, box_w, box_h) # contain: 비율 유지, 잘림 없음
        fw, fh = fitted.size
        # 우측 영역 좌우·상하 중앙 (우측 경계 클램프)
        ox     = img_start_x + PAD + (box_w - fw) // 2
        ox     = min(ox, TW - fw - PAD)   # 우측 잘림 방지
        oy     = PAD + (box_h - fh) // 2

        if fitted.mode != "RGBA":
            fitted = fitted.convert("RGBA")

        # 좌측 25% 소프트 페이드
        img.paste(fitted, (ox, oy), fitted)
        draw = ImageDraw.Draw(img)

    # ── 좌측 텍스트 영역 ──────────────────────────────────────
    MARGIN  = 28
    tx      = MARGIN
    text_w  = img_start_x - MARGIN * 2

    # 뱃지 — 텍스트 영역 좌상단 고정 (좌측 여백 20px, 상단 여백 20px)
    BADGE_LABEL = "⚡ 지금 특가"
    _, bh = _tw_badge(draw, tx + 20, 20, BADGE_LABEL,
                      bg_color=_TC_RED, text_color=_TC_WHITE, font_size=26)

    # Hook 텍스트 — 폰트 크기 자동 조절, 최대 2줄, 가운데 정렬, 골드
    f_hook, lines, line_h = _tw_auto_font(draw, hook_text, text_w,
                                          max_lines=2, size_max=92, size_min=38)
    total_h  = len(lines) * line_h
    text_top = 26 + bh + 18
    avail_h  = TH - BAR_H - text_top
    hy       = text_top + max(0, (avail_h - total_h) // 2)

    for line in lines:
        lx = _tw_center_text(draw, line, f_hook, tx, text_w)
        _tw_outline_text(draw, line, lx, hy, f_hook,
                         fill=_TC_GOLD, outline=_TC_BLACK, ow=5)
        hy += line_h

    # 서브 텍스트 (최대 1줄, 가운데 정렬)
    if sub_text:
        f_sub  = _tw_font(26, bold=False)
        sub_s  = _shorten_hook(sub_text, max_words=8)
        sub_ln = _tw_wrap(draw, sub_s, f_sub, text_w)[:1]
        for sl in sub_ln:
            slx = _tw_center_text(draw, sl, f_sub, tx, text_w)
            draw.text((slx, hy + 10), sl, font=f_sub, fill=(200, 255, 220))

    # ── 하단 브랜드 바 (전체 폭, 브랜드 그린) ────────────────
    draw.rectangle([0, TH - BAR_H, TW, TH], fill=_TC_GREEN)
    f_brand = _tw_font(30, bold=True)
    brand   = "🍀 생활꿀템연구소  |  프로필 링크에서 구매"
    bw2     = int(draw.textlength(brand, font=f_brand))
    draw.text(((TW - bw2) // 2, TH - BAR_H + 14), brand,
              font=f_brand, fill=_TC_WHITE)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def handle_generate_thumbnail(body: bytes) -> bytes:
    """PIL 기반 유튜브 썸네일 A/B 2안 생성 (1280×720)."""
    payload      = json.loads(body)
    slides       = payload.get("slides", [])
    images_b64   = payload.get("images", [])
    product_name = payload.get("productName", "")

    s0          = slides[0] if slides else {}
    auto_hook   = s0.get("headline") or s0.get("title") or product_name or "오늘의 생활꿀템"
    hook_text   = payload.get("customHook", "").strip() or auto_hook
    hook_short  = _shorten_hook(hook_text, max_words=5)
    sub_text    = (s0.get("body") or "").split(".")[0].strip()

    # 이미지 소스 우선순위:
    #   1. product_original.png (PNG 생성 시 자동 저장된 원본 제품 이미지)
    #   2. 요청 body의 images[] 첫 번째
    product_img = None
    orig_path   = os.path.join(os.path.dirname(__file__), "product_original.png")

    if os.path.isfile(orig_path):
        try:
            product_img = Image.open(orig_path).convert("RGBA")
            print(f"[썸네일] 원본 이미지 사용: product_original.png {product_img.size}", flush=True)
        except Exception as e:
            print(f"[썸네일] product_original.png 로드 실패({e}) — images[] 로 폴백", flush=True)

    if product_img is None:
        pil_images  = [img for img in (b64_to_pil(b) for b in images_b64) if img]
        product_img = pil_images[0] if pil_images else None
        if product_img:
            print(f"[썸네일] images[0] 사용: {product_img.size}", flush=True)
        else:
            print("[썸네일] 제품 이미지 없음 — 텍스트만으로 생성", flush=True)

    print(f"[썸네일] hook={hook_short!r}  PIL 렌더링 시작...", flush=True)

    a_bytes = _render_thumb_a(product_img, hook_short, sub_text)
    b_bytes = _render_thumb_b(product_img, hook_short, sub_text)
    print(f"[썸네일] 완료  A={len(a_bytes)//1024}KB  B={len(b_bytes)//1024}KB", flush=True)

    result = {
        "a": "data:image/png;base64," + base64.b64encode(a_bytes).decode(),
        "b": "data:image/png;base64," + base64.b64encode(b_bytes).decode(),
    }
    return json.dumps(result).encode()


def handle_generate_community_post(body: bytes) -> bytes:
    """템플릿 기반 커뮤니티 게시물 문구 생성 (AI 미사용, community_post_template.py)."""
    payload = json.loads(body)
    post = generate_post(payload["title"], payload["url"])
    return json.dumps({"post": post}, ensure_ascii=False).encode("utf-8")


# ──────────────────────────────────────────────────────────────
# Google OAuth2 공통  (Drive + YouTube 통합 인증)
#
# 최초 실행: credentials.json → 브라우저 인증 → token.json 자동 저장
# 이후 실행: token.json 자동 로드·갱신
#
# .env 선택 키:
#   GOOGLE_CREDENTIALS_JSON = credentials.json 경로 (기본: 서버 폴더)
#   GOOGLE_TOKEN_JSON       = token.json 경로       (기본: 서버 폴더)
#   GOOGLE_DRIVE_FOLDER_ID  = Drive 업로드 폴더 ID
# ──────────────────────────────────────────────────────────────
import time
import mimetypes

_OAUTH_SCOPES = [
    # drive.file(제한 스코프)은 앱이 직접 만들었거나 사용자가 Picker로 명시적으로 연 파일만
    # API로 볼 수 있어, .env에 수동으로 넣은 GOOGLE_DRIVE_FOLDER_ID가 계정 소유여도 404가 났음
    # (실측: 같은 계정인데 API로 보이는 폴더 0개). 전체 drive 스코프로 넓혀서 해결.
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # 고정 댓글 자동 작성(commentThreads.insert)에 필요
]


def _get_oauth_creds():
    """OAuth2 자격증명 반환. token.json 자동 저장·갱신.
    스코프 변경(YouTube 추가 등) 감지 시 자동 재인증.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise ImportError(
            "Google API 패키지가 필요합니다:\n"
            "  pip install google-auth google-auth-oauthlib google-api-python-client"
        )

    creds_path = Path(_get_env("GOOGLE_CREDENTIALS_JSON") or BASE_DIR / "credentials.json")
    token_path = Path(_get_env("GOOGLE_TOKEN_JSON") or BASE_DIR / "token.json")

    creds = None
    if token_path.exists():
        # from_authorized_user_file(path, scopes)는 creds.scopes를 "요청한" scopes로
        # 덮어써버려서, 파일에 실제 저장된 스코프와 비교하려면 원본 JSON을 따로 읽어야 함.
        saved_scopes = json.loads(token_path.read_text(encoding="utf-8")).get("scopes") or []
        creds = Credentials.from_authorized_user_file(str(token_path), _OAUTH_SCOPES)
        # 저장된 토큰에 필요한 스코프가 없으면 재인증
        if creds and not set(_OAUTH_SCOPES).issubset(set(saved_scopes)):
            print(f"[OAuth] 스코프 변경 감지 (저장됨: {saved_scopes}) → 재인증 필요", flush=True)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request as _Req
            print("[OAuth] 토큰 만료 → 자동 갱신 중...", flush=True)
            creds.refresh(_Req())
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"credentials.json을 찾을 수 없습니다: {creds_path}\n"
                    "Google Cloud Console → OAuth 클라이언트 ID(데스크톱 앱) 생성 후\n"
                    "credentials.json을 서버 폴더에 저장하세요."
                )
            print("[OAuth] 브라우저 인증 시작 (Drive + YouTube)... (180초 내 미완료 시 자동 취소)", flush=True)
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _OAUTH_SCOPES)
            try:
                creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=180)
            except Exception as e:
                raise TimeoutError(
                    "OAuth 인증이 180초 내에 완료되지 않아 취소되었습니다. "
                    "브라우저에서 구글 계정 로그인/동의를 완료한 뒤 다시 시도하세요."
                ) from e
            print("[OAuth] 인증 완료.", flush=True)

        token_path.write_text(creds.to_json(), encoding="utf-8")
        print(f"[OAuth] 토큰 저장: {token_path}", flush=True)

    return creds


def _build_drive_service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_get_oauth_creds(), cache_discovery=False)


def _build_youtube_service():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=_get_oauth_creds(), cache_discovery=False)


def _drive_upload_single(service, file_path: Path, folder_id: str) -> dict:
    """파일 1개를 Drive에 업로드. 성공 시 {id, name, webViewLink} 반환."""
    from googleapiclient.http import MediaFileUpload

    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    metadata = {"name": file_path.name, "parents": [folder_id]}
    media = MediaFileUpload(str(file_path), mimetype=mime, resumable=True)

    file = (
        service.files()
        .create(body=metadata, media_body=media, fields="id,name,webViewLink")
        .execute()
    )
    return file


def drive_upload_with_retry(file_paths: list[Path], max_retry: int = 3, make_public: bool = False) -> list[dict]:
    """파일 목록을 Drive에 업로드. 실패 시 최대 max_retry번 재시도.

    make_public=True면 업로드 성공한 파일마다 "링크가 있는 모든 사용자(뷰어)" 권한을 부여하고
    결과에 publicUrl(직접 다운로드 링크)을 추가한다. Instagram Graph API처럼 외부 서버가
    직접 접근 가능한 URL이 필요한 경우에 사용.

    반환: [{name, id, webViewLink, status, publicUrl?}, ...]
    """
    folder_id = _get_env("GOOGLE_DRIVE_FOLDER_ID")
    if not folder_id:
        raise ValueError(
            "GOOGLE_DRIVE_FOLDER_ID가 .env에 없습니다.\n"
            "Drive 폴더 ID를 설정하세요."
        )

    service = _build_drive_service()
    results = []

    for fp in file_paths:
        if not fp.exists():
            print(f"[Drive] 파일 없음 — 건너뜀: {fp}", flush=True)
            results.append({"name": fp.name, "status": "skipped", "reason": "file not found"})
            continue

        last_err = None
        for attempt in range(1, max_retry + 1):
            try:
                print(f"[Drive] 업로드 시도 {attempt}/{max_retry}: {fp.name} ({fp.stat().st_size // 1024}KB)", flush=True)
                info = _drive_upload_single(service, fp, folder_id)
                print(f"[Drive] 업로드 완료: {info.get('name')}  id={info.get('id')}", flush=True)

                if make_public:
                    service.permissions().create(
                        fileId=info["id"],
                        body={"role": "reader", "type": "anyone"},
                    ).execute()
                    info["publicUrl"] = f"https://drive.google.com/uc?export=download&id={info['id']}"
                    print(f"[Drive] 공개 권한 설정 완료 → {info['publicUrl']}", flush=True)

                results.append({**info, "status": "ok"})
                last_err = None
                break
            except Exception as e:
                last_err = str(e)
                print(f"[Drive] 시도 {attempt} 실패: {last_err}", flush=True)
                if attempt < max_retry:
                    time.sleep(2 ** attempt)   # 지수 백오프: 2s, 4s

        if last_err is not None:
            print(f"[Drive] 최종 실패: {fp.name}", flush=True)
            results.append({"name": fp.name, "status": "failed", "reason": last_err})

    return results


# ──────────────────────────────────────────────────────────────
# /render-video  (Remotion 렌더링 — 백그라운드 스레드)
# /render-status (폴링용 상태 조회)
# ──────────────────────────────────────────────────────────────

def _run_render_worker(platform: str = "") -> None:
    global _render_state
    ps1 = BASE_DIR / "render.ps1"
    cmd = [
        "powershell", "-ExecutionPolicy", "Bypass",
        "-File", str(ps1),
    ]
    if platform:
        cmd += ["-Platform", platform]
    print("[Render] 렌더링 시작...", flush=True)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR),
        )
        for raw_line in proc.stdout:
            line = _ANSI_RE.sub("", raw_line).rstrip()
            if not line:
                continue
            print(f"[Render] {line}", flush=True)
            with _render_lock:
                _render_state["log"].append(line)
        proc.wait()
        rc = proc.returncode
    except Exception as e:
        rc = -1
        with _render_lock:
            _render_state["log"].append(f"[오류] {e}")
        print(f"[Render] 예외: {e}", flush=True)

    with _render_lock:
        _render_state["running"] = False
        _render_state["done"]    = True
        _render_state["exitCode"] = rc
        _render_state["finishedAt"] = time.time() if rc == 0 else None
    print(f"[Render] 완료 (exitCode={rc})", flush=True)


def handle_render_video(body: bytes) -> bytes:
    """POST /render-video — render.ps1 백그라운드 실행.

    Body JSON (선택):
      platforms  list[str]  STEP 1에서 선택한 콘텐츠 타겟 플랫폼 (예: ["유튜브"], ["전체"]).
                 Remotion이 9~10번 슬라이드(CTA 구간)에 표시할 링크 안내 문구를 이 값으로 분기한다.
                 단일 플랫폼일 때만 구체적으로 분기하고, 복수 선택되었거나 "전체"인 경우
                 render.ps1/ShoppingShorts.tsx가 범용 문구로 폴백한다.
    """
    global _render_state
    with _render_lock:
        if _render_state.get("running"):
            raise RuntimeError("이미 렌더링이 진행 중입니다. /render-status 로 확인하세요.")
        _render_state = {"running": True, "done": False, "exitCode": None, "log": [], "finishedAt": None}

    payload   = json.loads(body) if body else {}
    platforms = payload.get("platforms") or []
    platform  = platforms[0] if len(platforms) == 1 and platforms[0] != "전체" else ""

    t = _threading.Thread(target=_run_render_worker, args=(platform,), daemon=True)
    t.start()
    return json.dumps({"ok": True, "message": "렌더링 시작됨"}).encode()


def handle_render_status() -> bytes:
    """GET /render-status — 현재 렌더링 상태와 누적 로그 반환."""
    with _render_lock:
        snap = {
            "running":    _render_state["running"],
            "done":       _render_state["done"],
            "exitCode":   _render_state["exitCode"],
            "log":        list(_render_state["log"]),
            "finishedAt": _render_state.get("finishedAt"),
        }
    return json.dumps(snap, ensure_ascii=False).encode()


# ──────────────────────────────────────────────────────────────
# STEP0 — 쿠팡 URL 자동 수집
#   ※ 가격/할인율 수치는 절대 응답에 포함하지 않는다 (coupang_collector.py 참고)
# ──────────────────────────────────────────────────────────────

def handle_collect_products(body: bytes) -> bytes:
    """POST /collect-products  { "urls": ["https://...", ...] }
    URL별로 상품명/평점/리뷰수/카테고리 추정/썸네일(1장)을 스크래핑해 반환.
    전체 이미지는 다운로드하지 않고 URL만 넘겨 선택 확정(/confirm-selection) 시에만 내려받는다.
    """
    payload = json.loads(body) if body else {}
    urls = [u.strip() for u in payload.get("urls", []) if isinstance(u, str) and u.strip()]
    urls = list(dict.fromkeys(urls))[:10]  # 중복 제거 + 최대 10개

    if not urls:
        return json.dumps({"products": [], "errors": [{"url": "", "message": "URL을 입력해주세요."}]},
                           ensure_ascii=False).encode()

    products, errors = [], []
    for url in urls:
        print(f"[STEP0 수집] {url}", flush=True)
        item = coupang_collector.collect_product(url)
        if item.error or not item.name:
            errors.append({"url": url, "message": item.error or "상품 정보를 찾을 수 없습니다."})
            continue

        thumb = coupang_collector.download_image_b64(item.image_urls[0]) if item.image_urls else None
        products.append({
            "sourceUrl":    item.source_url,
            "pageKey":      item.page_key,
            "name":         item.name,
            "rating":       item.rating,
            "reviewCount":  item.review_count,
            "category":     item.category,
            "thumbnail":    thumb,
            "imageUrls":    item.image_urls,
        })
        print(f"  → {item.name!r}  이미지 {len(item.image_urls)}개  카테고리 추정={item.category}", flush=True)

    return json.dumps({"products": products, "errors": errors}, ensure_ascii=False).encode()


def handle_confirm_selection(body: bytes) -> bytes:
    """POST /confirm-selection  { "name", "category", "imageUrls": [...] }
    선택 확정된 상품 1건의 이미지(최대 10장)를 실제로 다운로드해 base64로 반환.
    STEP1의 제품명/이미지 자동 채움에 사용된다 (가격/할인율은 다루지 않음).
    """
    payload    = json.loads(body) if body else {}
    name       = (payload.get("name") or "").strip()
    category   = payload.get("category") or "기타"
    image_urls = [u for u in payload.get("imageUrls", []) if isinstance(u, str)][:10]

    images = []
    for url in image_urls:
        b64 = coupang_collector.download_image_b64(url)
        if b64:
            images.append(b64)

    print(f"[STEP0 확정] {name!r}  이미지 {len(images)}/{len(image_urls)}개 다운로드 완료", flush=True)
    return json.dumps({"name": name, "category": category, "images": images}, ensure_ascii=False).encode()


# ──────────────────────────────────────────────────────────────
# STEP2 — 카피 생성 요청 큐 (비동기, Claude Code 세션이 직접 처리)
#
# 이 서버는 카피를 생성하지 않는다. /request-copy 는 제품명·카테고리·
# 제품 이미지·상품평 캡처를 public/pending/{requestId}.json + 이미지 파일로
# 저장할 뿐이다. 실제 카피 작성(제품 이미지 특징 파악, 상품평 캡처 내용
# 반영, 카테고리별 슬롯 구성에 맞춰 슬라이드 1~7 작성)은 Claude Code
# 세션이 이 파일을 열어서 수행하고, 결과를 public/completed/{requestId}.json
# 으로 저장한다 — 슬롯 구성 규칙은 category-copy-rules 스킬 참고.
#
# completed JSON 스키마 (Claude Code 세션이 작성):
#   { "requestId": "...", "slides": [ {"headline": "...", "body": "..."}, ... 7개 ] }
#
# 대시보드는 /copy-status?requestId=... 를 폴링해 완료 여부를 확인한다.
# 트리거는 수동 — pending 파일이 생겼다고 서버가 자동으로 뭔가를 실행하지
# 않는다(사용자가 Claude Code 세션에 처리를 요청해야 함).
# ──────────────────────────────────────────────────────────────
import uuid as _uuid

PENDING_DIR   = BASE_DIR / "public" / "pending"
COMPLETED_DIR = BASE_DIR / "public" / "completed"


def handle_request_copy(body: bytes) -> bytes:
    """POST /request-copy
    { productName, partnerLink, category, productImages: base64[], reviewImages: base64[] }

    productName/category/productImages 가 비어있으면 저장하지 않고 에러를 반환한다.
    (재발 버그: 카테고리 불일치·상품평 미반영 카피가 조용히 생성되던 문제 방지 —
    입력이 불충분하면 예시 문구로 조용히 대체하지 않고 막는다.)
    reviewImages는 STEP1에서 선택 입력이라 비어있을 수 있다 — 비어있으면 그대로
    빈 배열로 저장해, Claude Code 세션이 실제 리뷰 인용을 지어내지 않도록 한다.
    """
    payload      = json.loads(body) if body else {}
    product_name = (payload.get("productName") or "").strip()
    category     = (payload.get("category") or "").strip()
    partner_link = (payload.get("partnerLink") or "").strip()
    product_imgs_b64 = [b for b in payload.get("productImages", []) if isinstance(b, str) and b.strip()]
    review_imgs_b64  = [b for b in payload.get("reviewImages", [])  if isinstance(b, str) and b.strip()]

    errors = []
    if not product_name:
        errors.append("제품명을 입력해주세요.")
    if not category:
        errors.append("카테고리를 선택해주세요.")
    if not product_imgs_b64:
        errors.append("제품 이미지를 최소 1장 업로드해주세요.")
    if errors:
        return json.dumps({"ok": False, "errors": errors}, ensure_ascii=False).encode()

    request_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{_uuid.uuid4().hex[:8]}"
    req_img_dir = PENDING_DIR / request_id
    req_img_dir.mkdir(parents=True, exist_ok=True)

    def _save_images(items: list[str], prefix: str) -> list[str]:
        paths = []
        for i, b64 in enumerate(items):
            img = b64_to_pil(b64)
            if img is None:
                continue
            fp = req_img_dir / f"{prefix}_{i+1}.png"
            img.convert("RGB").save(fp, format="PNG")
            paths.append(str(fp))
        return paths

    product_paths = _save_images(product_imgs_b64, "product")
    review_paths  = _save_images(review_imgs_b64,  "review")

    if not product_paths:
        shutil.rmtree(req_img_dir, ignore_errors=True)  # 빈 폴더 잔여 방지
        return json.dumps({"ok": False, "errors": ["제품 이미지 저장에 실패했습니다. 다시 업로드해주세요."]},
                           ensure_ascii=False).encode()

    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    pending_payload = {
        "requestId":     request_id,
        "createdAt":     datetime.datetime.now().isoformat(timespec="seconds"),
        "productName":   product_name,
        "partnerLink":   partner_link,
        "category":      category,
        "productImages": product_paths,
        "reviewImages":  review_paths,
    }
    (PENDING_DIR / f"{request_id}.json").write_text(
        json.dumps(pending_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[카피생성요청] {request_id} 저장됨 — product={product_name!r} category={category!r} "
          f"productImgs={len(product_paths)} reviewImgs={len(review_paths)}", flush=True)

    # 2026-07-09: pending 저장 직후 헤드리스 Claude Code 호출을 백그라운드로 바로 띄운다.
    # 실패해도(claude CLI 없음/타임아웃/completed 파일 미생성) 사람이 "pending 처리해줘"라고
    # 수동으로 요청하는 기존 경로는 그대로 유효하다 — pending 파일을 지우지 않기 때문.
    t = _threading.Thread(target=_run_headless_copy_worker, args=(request_id,), daemon=True)
    t.start()

    return json.dumps({"ok": True, "requestId": request_id}, ensure_ascii=False).encode()


# ── 헤드리스 자동 트리거 ──────────────────────────────────────────
#
# 전제: ANTHROPIC_API_KEY가 환경변수/코드 어디에도 없다는 걸 확인하고(2026-07-09
# 조사) 이 자동화를 붙인다 — claude 헤드리스 실행(-p)은 이 키가 있으면 그걸
# 우선 사용해 API 종량제로 청구될 수 있다. 나중에 ANTHROPIC_API_KEY를 코드/환경
# 어디에든 추가하게 되면, 이 자동화 경로(구독 사용량으로 처리된다는 전제)부터
# 다시 점검해야 한다 — community_post_helper.py의 ANTHROPIC_API_KEY 사용처럼
# .env에만 추가해도 이 서버 프로세스의 os.environ에 노출되면 subprocess가 그대로
# 상속한다.
CLAUDE_CLI_TIMEOUT_SEC = 300  # claude -p는 내장 타임아웃이 없어 여기서 강제 종료한다.

_copy_jobs_lock = _threading.Lock()
_copy_jobs: dict = {}  # requestId -> {"status": "processing"|"failed", "startedAt": float, "error": str|None, "proc": Popen|None}


def _resolve_claude_cli_path() -> str | None:
    """claude CLI 실행파일의 절대경로를 찾는다 (shell=True 없이 바로 실행 가능한 .exe만 반환).

    주의(2026-07-09 조사): Claude 데스크톱 앱(Windows, MSIX 패키지)이 띄운 프로세스에서는
    Windows AppContainer의 AppData 가상화 때문에 %APPDATA% 접근이 그 앱 패키지 전용 격리
    폴더로 조용히 리다이렉트된다. 이 함수는 사용자가 실제 셸(cmd.exe/PowerShell)에서 직접
    `python server.py`를 실행하는 정상 배포 상황을 전제로 한다 — 그 경우엔 리다이렉트가
    적용되지 않아 아래 경로가 실제 npm 전역 설치 위치를 정확히 가리킨다. (Claude Code 세션의
    Bash/PowerShell 도구로 이 경로를 검증하면 격리된 가짜 사본을 볼 수 있으니, 실제 동작
    확인은 항상 사용자의 실제 터미널에서 해야 한다.)
    """
    which_path = shutil.which("claude")
    if which_path:
        npm_bin = Path(which_path).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if npm_bin.exists():
            return str(npm_bin)

    appdata = os.environ.get("APPDATA")
    if appdata:
        fallback = Path(appdata) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if fallback.exists():
            return str(fallback)

    return None


def _run_headless_copy_worker(request_id: str) -> None:
    """pending/{requestId}.json을 claude -p 헤드리스 호출로 자동 처리한다.

    render.ps1을 돌리는 _run_render_worker와 동일한 백그라운드 스레드 + Popen 패턴.
    완료 판정은 claude 프로세스의 종료 코드가 아니라 completed/{requestId}.json 파일이
    실제로 생겼는지로 한다 — 헤드리스 호출이 "성공"으로 끝나도 지시를 안 따르고 파일을
    안 만들 가능성을 배제하지 않기 위함.
    """
    with _copy_jobs_lock:
        _copy_jobs[request_id] = {"status": "processing", "startedAt": time.time(), "error": None, "proc": None}

    pending_fp = PENDING_DIR / f"{request_id}.json"
    if not pending_fp.exists():
        with _copy_jobs_lock:
            _copy_jobs[request_id] = {"status": "failed", "startedAt": time.time(), "error": "pending 파일이 이미 삭제됨(취소됨)."}
        return

    claude_path = _resolve_claude_cli_path()
    if not claude_path:
        with _copy_jobs_lock:
            _copy_jobs[request_id] = {
                "status": "failed", "startedAt": time.time(),
                "error": "claude CLI를 찾을 수 없습니다. 'npm install -g @anthropic-ai/claude-code' 설치와 "
                         "PATH 등록을 확인하거나, 수동으로 \"pending 요청 처리해줘\"라고 요청하세요.",
            }
        print(f"[헤드리스 카피생성] {request_id} 실패 — claude CLI 경로를 찾을 수 없음", flush=True)
        return

    completed_fp = COMPLETED_DIR / f"{request_id}.json"
    prompt = (
        f"public/pending/{request_id}.json 요청을 처리해줘. category-copy-rules 스킬의 "
        f"\"비동기 카피 생성 요청 처리\" 절차를 그대로 따라서 제품 이미지와 상품평 캡처를 "
        f"직접 읽고 실제 카피를 작성한 뒤, public/completed/{request_id}.json 에 그 스킬에 "
        f"문서화된 스키마 그대로 저장해. 다른 걸 묻지 말고 바로 처리하고 끝내."
    )
    cmd = [
        claude_path, "-p", prompt,
        "--allowedTools", "Read,Write",
        "--permission-mode", "dontAsk",
        "--output-format", "json",
    ]

    print(f"[헤드리스 카피생성] {request_id} 시작 — {claude_path}", flush=True)
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(BASE_DIR),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        with _copy_jobs_lock:
            if request_id in _copy_jobs:
                _copy_jobs[request_id]["proc"] = proc

        try:
            stdout, _ = proc.communicate(timeout=CLAUDE_CLI_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            with _copy_jobs_lock:
                _copy_jobs[request_id] = {
                    "status": "failed", "startedAt": _copy_jobs.get(request_id, {}).get("startedAt", time.time()),
                    "error": f"{CLAUDE_CLI_TIMEOUT_SEC}초 타임아웃 — 자동 처리가 끝나지 않아 강제 종료했습니다. "
                             f"수동으로 \"pending 요청 처리해줘\"라고 요청하세요.",
                }
            print(f"[헤드리스 카피생성] {request_id} 타임아웃 — 프로세스 강제 종료", flush=True)
            return

        if completed_fp.exists():
            with _copy_jobs_lock:
                _copy_jobs.pop(request_id, None)  # completed 파일이 상태의 근거이므로 job 기록은 정리만
            print(f"[헤드리스 카피생성] {request_id} 완료 (exit={proc.returncode})", flush=True)
            return

        snippet = (stdout or "").strip()[-2000:]
        with _copy_jobs_lock:
            _copy_jobs[request_id] = {
                "status": "failed", "startedAt": _copy_jobs.get(request_id, {}).get("startedAt", time.time()),
                "error": f"자동 처리가 completed 파일을 만들지 못했습니다 (exit={proc.returncode}). "
                         f"수동으로 \"pending 요청 처리해줘\"라고 요청하세요. 출력 일부: {snippet}",
            }
        print(f"[헤드리스 카피생성] {request_id} 실패 — completed 파일 없음 (exit={proc.returncode})\n{snippet}", flush=True)
    except Exception as e:
        with _copy_jobs_lock:
            _copy_jobs[request_id] = {"status": "failed", "startedAt": time.time(), "error": str(e)}
        print(f"[헤드리스 카피생성] {request_id} 예외: {e}", flush=True)


def handle_copy_status(request_id: str) -> bytes:
    """GET /copy-status?requestId=xxx — pending(대기)/processing(자동처리중)/done(완료)/failed(실패) 반환."""
    if not request_id:
        return json.dumps({"status": "error", "message": "requestId가 없습니다."}, ensure_ascii=False).encode()

    completed_fp = COMPLETED_DIR / f"{request_id}.json"
    if completed_fp.exists():
        data = json.loads(completed_fp.read_text(encoding="utf-8"))
        return json.dumps({"status": "done", "data": data}, ensure_ascii=False).encode()

    with _copy_jobs_lock:
        job = _copy_jobs.get(request_id)
    if job:
        if job["status"] == "processing":
            return json.dumps({"status": "processing", "startedAt": job["startedAt"]}, ensure_ascii=False).encode()
        if job["status"] == "failed":
            return json.dumps({"status": "failed", "error": job.get("error") or "알 수 없는 오류"}, ensure_ascii=False).encode()

    pending_fp = PENDING_DIR / f"{request_id}.json"
    if pending_fp.exists():
        return json.dumps({"status": "pending"}, ensure_ascii=False).encode()

    return json.dumps({"status": "not_found"}, ensure_ascii=False).encode()


def handle_cancel_copy_request(body: bytes) -> bytes:
    """POST /cancel-copy-request  { requestId } — 대기/처리 중인 요청을 취소.
    자동 처리가 돌고 있었다면 그 프로세스도 강제 종료한다."""
    payload    = json.loads(body) if body else {}
    request_id = (payload.get("requestId") or "").strip()
    if not request_id:
        return json.dumps({"ok": False, "error": "requestId가 없습니다."}, ensure_ascii=False).encode()

    with _copy_jobs_lock:
        job = _copy_jobs.pop(request_id, None)
    if job and job.get("proc"):
        try:
            job["proc"].kill()
        except Exception:
            pass

    pending_json = PENDING_DIR / f"{request_id}.json"
    pending_dir  = PENDING_DIR / request_id
    if pending_json.exists():
        pending_json.unlink()
    if pending_dir.exists():
        shutil.rmtree(pending_dir, ignore_errors=True)

    return json.dumps({"ok": True}, ensure_ascii=False).encode()


def handle_upload_drive(body: bytes) -> bytes:
    """POST /upload-drive  { "files": ["mp4"|"thumbnail_a"|"thumbnail_b"|"all"] }
    지정 파일을 Drive에 업로드하고 결과 JSON 반환.
    """
    payload   = json.loads(body) if body else {}
    targets   = payload.get("files", ["all"])

    mp4_path   = BASE_DIR / "output_with_bgm.mp4"
    thumb_a    = BASE_DIR / "thumbnail_test_a.png"
    thumb_b    = BASE_DIR / "thumbnail_test_b.png"

    _all = {"mp4": mp4_path, "thumbnail_a": thumb_a, "thumbnail_b": thumb_b}

    if "all" in targets:
        paths = list(_all.values())
    else:
        paths = [_all[t] for t in targets if t in _all]

    if not paths:
        raise ValueError(f"유효한 대상이 없습니다. targets={targets}")

    results = drive_upload_with_retry(paths)
    ok   = [r for r in results if r.get("status") == "ok"]
    fail = [r for r in results if r.get("status") == "failed"]
    print(f"[Drive] 결과: 성공 {len(ok)}개  실패 {len(fail)}개", flush=True)

    return json.dumps({"results": results}, ensure_ascii=False).encode()


# ──────────────────────────────────────────────────────────────
# YouTube 업로드  (YouTube Data API v3)
# ──────────────────────────────────────────────────────────────

def handle_upload_youtube(body: bytes) -> bytes:
    """POST /upload-youtube  — output_with_bgm.mp4를 YouTube에 업로드.

    Body JSON:
      title             str   영상 제목 (최대 100자)
      description       str   설명 (최대 5000자)
      tags              list  태그 목록
      privacyStatus     str   "private" | "unlisted" | "public"
      thumbnailVariant  str   "a" | "b" | null  — 선택한 썸네일 자동 업로드
      pinnedComment     str   업로드 직후 자동으로 남길 댓글 (Linktree 등 클릭 가능한 링크용).
                              YouTube Shorts는 설명란 URL을 자동 링크화하지 않지만 댓글은 링크화하므로,
                              설명란 대신 댓글로 안내하면 실제로 클릭 가능하다.
                              단, "고정(핀)"은 YouTube Data API가 지원하지 않아 댓글 작성까지만 자동화되고
                              핀 고정은 YouTube Studio에서 수동으로 해야 한다.
    """
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        raise ImportError("pip install google-api-python-client")

    payload           = json.loads(body) if body else {}
    title             = (payload.get("title") or "생활꿀템 추천")[:100]
    description       = (payload.get("description") or "")[:5000]
    tags              = payload.get("tags") or []
    privacy           = payload.get("privacyStatus", "private")
    thumb_variant     = payload.get("thumbnailVariant")  # "a" | "b" | None
    pinned_comment    = (payload.get("pinnedComment") or "").strip()[:10000]

    mp4_path = BASE_DIR / "output_with_bgm.mp4"
    if not mp4_path.exists():
        raise FileNotFoundError(
            "output_with_bgm.mp4가 없습니다. STEP 4에서 BGM을 먼저 추가하세요."
        )

    youtube = _build_youtube_service()

    body_req = {
        "snippet": {
            "title":       title,
            "description": description,
            "tags":        tags,
            "categoryId":  "22",          # People & Blogs
            "defaultLanguage": "ko",
        },
        "status": {"privacyStatus": privacy},
    }

    size_mb = mp4_path.stat().st_size / 1024 / 1024
    print(f"[YouTube] 업로드 시작: {mp4_path.name} ({size_mb:.1f}MB) privacy={privacy}", flush=True)

    last_err = None
    for attempt in range(1, 4):
        try:
            media = MediaFileUpload(
                str(mp4_path), mimetype="video/mp4",
                resumable=True, chunksize=5 * 1024 * 1024,
            )
            req = youtube.videos().insert(
                part="snippet,status", body=body_req, media_body=media
            )
            response = None
            while response is None:
                status, response = req.next_chunk()
                if status:
                    print(f"[YouTube] {int(status.progress()*100)}%", flush=True)

            video_id = response.get("id", "")
            url      = f"https://www.youtube.com/watch?v={video_id}"
            print(f"[YouTube] 업로드 완료: {url}", flush=True)

            # 썸네일 업로드 (선택된 경우)
            thumb_uploaded = False
            if thumb_variant in ("a", "b"):
                thumb_path = BASE_DIR / f"thumbnail_test_{thumb_variant}.png"
                if thumb_path.exists():
                    try:
                        print(f"[YouTube] 썸네일 업로드 중: {thumb_path.name} ({thumb_variant}안)", flush=True)
                        youtube.thumbnails().set(
                            videoId=video_id,
                            media_body=MediaFileUpload(
                                str(thumb_path), mimetype="image/png", resumable=False
                            )
                        ).execute()
                        thumb_uploaded = True
                        print(f"[YouTube] 썸네일 업로드 완료 ({thumb_variant}안)", flush=True)
                    except Exception as te:
                        print(f"[YouTube] 썸네일 업로드 실패 (영상은 정상): {te}", flush=True)
                else:
                    print(f"[YouTube] 썸네일 파일 없음: {thumb_path}", flush=True)

            # 고정 댓글 자동 작성 (Linktree 링크 등 — 설명란과 달리 댓글은 클릭 가능한 링크로 표시됨)
            comment_posted = False
            if pinned_comment:
                try:
                    print(f"[YouTube] 댓글 작성 중... ({len(pinned_comment)}자)", flush=True)
                    youtube.commentThreads().insert(
                        part="snippet",
                        body={
                            "snippet": {
                                "videoId": video_id,
                                "topLevelComment": {
                                    "snippet": {"textOriginal": pinned_comment}
                                },
                            }
                        },
                    ).execute()
                    comment_posted = True
                    print("[YouTube] 댓글 작성 완료 (핀 고정은 YouTube Studio에서 수동으로 해주세요)", flush=True)
                except Exception as ce:
                    print(f"[YouTube] 댓글 작성 실패 (영상은 정상): {ce}", flush=True)

            # 커뮤니티 게시물 문구 생성 + 텔레그램 알림.
            # /upload-youtube 요청에는 productName이 별도로 오지 않으므로(대시보드가 안 보냄)
            # title을 그대로 상품명 대신 사용한다. 실패해도 업로드 자체는 이미 성공했으니 무시.
            try:
                notify_thumb_path = (
                    thumb_path if thumb_variant in ("a", "b") and thumb_path.exists()
                    else BASE_DIR / "thumbnail_test_a.png"
                )
                community_post_helper.handle_community_post_for_upload(
                    video_title=title,
                    video_url=url,
                    product_name=title,
                    thumbnail_path=str(notify_thumb_path),
                )
            except Exception:
                print(f"[community_post] 알림 단계 실패 (업로드 자체는 성공): {traceback.format_exc()}", flush=True)

            return json.dumps(
                {"ok": True, "videoId": video_id, "url": url,
                 "thumbnailUploaded": thumb_uploaded, "thumbnailVariant": thumb_variant,
                 "commentPosted": comment_posted},
                ensure_ascii=False
            ).encode()

        except Exception as e:
            last_err = str(e)
            print(f"[YouTube] 시도 {attempt}/3 실패: {last_err}", flush=True)
            if attempt < 3:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"YouTube 업로드 최종 실패: {last_err}")


# ──────────────────────────────────────────────────────────────
# Instagram 릴스 업로드  (Graph API — Drive 공개 URL 경유)
# ──────────────────────────────────────────────────────────────
IG_CAPTION_MAX = 2200
# 쿠팡 파트너스 활동 표기 의무 해시태그. 대시보드 STEP 6 "설명" 필드에 사용자가 빠뜨려도
# 업로드 시 자동으로 보강한다 (공정위 유료광고 표시 가이드 대응).
REQUIRED_IG_HASHTAGS = ["#광고", "#유료광고", "#쿠팡파트너스"]


def _ensure_required_hashtags(caption: str) -> str:
    """필수 해시태그 중 캡션에 없는 것만 뒤에 덧붙인다. 2200자 제한을 넘으면
    해시태그는 보존하고 본문 쪽을 잘라낸다."""
    missing = [tag for tag in REQUIRED_IG_HASHTAGS if tag not in caption]
    if not missing:
        return caption[:IG_CAPTION_MAX]

    suffix = " ".join(missing)
    body_text = caption.strip()
    combined = f"{body_text}\n\n{suffix}" if body_text else suffix

    if len(combined) > IG_CAPTION_MAX:
        overflow = len(combined) - IG_CAPTION_MAX
        body_text = body_text[:-overflow] if overflow < len(body_text) else ""
        combined = f"{body_text}\n\n{suffix}" if body_text else suffix

    return combined


def handle_upload_instagram(body: bytes) -> bytes:
    """POST /upload-instagram — output_with_bgm.mp4를 Drive에 업로드해 공개 다운로드 URL을
    만든 뒤, instagram_api.upload_reel()로 릴스를 게시한다.

    Body JSON:
      caption  str  릴스 캡션 (대시보드 STEP 6 "설명" 필드 값을 기반으로,
                     필수 해시태그(#광고 #유료광고 #쿠팡파트너스)가 없으면 자동 보강)
    """
    payload = json.loads(body) if body else {}
    caption = _ensure_required_hashtags((payload.get("caption") or "").strip())

    mp4_path = BASE_DIR / "output_with_bgm.mp4"
    if not mp4_path.exists():
        raise FileNotFoundError(
            "output_with_bgm.mp4가 없습니다. STEP 4에서 BGM을 먼저 추가하세요."
        )

    print(f"[Instagram] Drive 업로드 시작 (공개 URL 생성용): {mp4_path.name}", flush=True)
    drive_results = drive_upload_with_retry([mp4_path], make_public=True)
    info = drive_results[0]
    if info.get("status") != "ok":
        raise RuntimeError(f"Instagram 업로드용 Drive 업로드 실패: {info.get('reason')}")

    video_url = info["publicUrl"]
    print(f"[Instagram] 릴스 게시 시작 (video_url={video_url})", flush=True)

    try:
        result = instagram_api.upload_reel(video_url, caption)
    except Exception as e:
        print(f"[Instagram] 릴스 게시 실패: {e}", flush=True)
        raise

    print(f"[Instagram] 릴스 게시 완료: media_id={result['id']}", flush=True)

    return json.dumps(
        {"ok": True, "mediaId": result["id"], "creationId": result["creation_id"]},
        ensure_ascii=False
    ).encode()


# ──────────────────────────────────────────────────────────────
# Facebook 페이지 게시  (Graph API — Instagram과 페이지/토큰 재사용)
# ──────────────────────────────────────────────────────────────
def handle_upload_facebook(body: bytes) -> bytes:
    """POST /upload-facebook — output_with_bgm.mp4를 Drive에 업로드해 공개 다운로드 URL을
    만든 뒤, facebook_api.upload_video_post()로 "생활꿀템연구소" 페이지에 게시한다.
    facebook_auth.py로 별도 발급받은 FACEBOOK_PAGE_ACCESS_TOKEN을 사용한다
    (Instagram 업로드용 META_PAGE_ACCESS_TOKEN과는 다른 토큰 — 이유는 facebook_auth.py 참고).

    Body JSON:
      caption  str  게시물 캡션 (대시보드가 쿠팡 파트너스 링크를 클릭 가능한 형태로 직접 포함해 전달)
    """
    payload = json.loads(body) if body else {}
    caption = (payload.get("caption") or "").strip()

    mp4_path = BASE_DIR / "output_with_bgm.mp4"
    if not mp4_path.exists():
        raise FileNotFoundError(
            "output_with_bgm.mp4가 없습니다. STEP 4에서 BGM을 먼저 추가하세요."
        )

    print(f"[Facebook] Drive 업로드 시작 (공개 URL 생성용): {mp4_path.name}", flush=True)
    drive_results = drive_upload_with_retry([mp4_path], make_public=True)
    info = drive_results[0]
    if info.get("status") != "ok":
        raise RuntimeError(f"Facebook 업로드용 Drive 업로드 실패: {info.get('reason')}")

    video_url = info["publicUrl"]
    print(f"[Facebook] 페이지 게시 시작 (video_url={video_url})", flush=True)

    try:
        result = facebook_api.upload_video_post(video_url, caption)
    except Exception as e:
        print(f"[Facebook] 게시 실패: {e}", flush=True)
        raise

    print(f"[Facebook] 게시 완료: video_id={result['id']}", flush=True)

    return json.dumps(
        {"ok": True, "videoId": result["id"], "url": result["url"]},
        ensure_ascii=False
    ).encode()


# ──────────────────────────────────────────────────────────────
# HTTP Handler
# ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt % args}")

    def _send(self, code: int, ctype: str, data: bytes, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path == "/list-bgm":
            self._send(200, "application/json", handle_list_bgm())
            return
        if path == "/render-status":
            self._send(200, "application/json", handle_render_status())
            return
        if path == "/copy-status":
            from urllib.parse import parse_qs
            qs = parse_qs(parsed.query)
            request_id = qs.get("requestId", [""])[0]
            self._send(200, "application/json", handle_copy_status(request_id))
            return

        rel = path.lstrip("/")
        fp  = BASE_DIR / "dashboard.html" if rel in ("", "dashboard.html") else BASE_DIR / rel
        if fp.exists() and fp.is_file():
            # dashboard.html/js/css는 자주 고치는 "소스" 파일이라 브라우저가 옛날 버전을
            # 캐시해서 계속 보여주는 사고(2026-07-14 진단: 고정댓글 링크가 옛날 버전으로
            # 복사되던 버그의 원인 중 하나 — 탭을 오래 열어두면 fix 이전 DOM이 남아있었음)를
            # 막기 위해 no-cache를 강제한다. 이미지/영상 등 대용량 미디어는 자주 안 바뀌므로
            # 그대로 기본 캐시 동작을 둔다.
            extra = {"Cache-Control": "no-cache"} if fp.suffix.lower() in (".html", ".js", ".css") else None
            self._send(200, MIME.get(fp.suffix.lower(), "application/octet-stream"), fp.read_bytes(), extra)
        else:
            self._send(404, "text/plain", b"Not Found")

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        try:
            if path == "/generate-png":
                zip_bytes = handle_generate_png(body)
                self._send(200, "application/zip", zip_bytes,
                           {"Content-Disposition": 'attachment; filename="carousel_1080x1350.zip"'})
            elif path == "/generate-srt":
                srt_bytes = handle_generate_srt(body)
                self._send(200, "text/plain; charset=utf-8", srt_bytes,
                           {"Content-Disposition": 'attachment; filename="output.srt"'})
            elif path == "/generate-tts":
                wav_bytes, narr_dur = handle_generate_tts(body)
                self._send(200, "audio/wav", wav_bytes, {
                    "Content-Disposition": 'attachment; filename="output_narration.wav"',
                    "X-Narration-Duration": str(round(narr_dur, 2)),
                    "Access-Control-Expose-Headers": "X-Narration-Duration",
                })
            elif path in ("/generate-bgm", "/change-bgm"):
                mp4_bytes, bgm_name, source_mtime_iso = handle_generate_bgm(body)
                self._send(200, "video/mp4", mp4_bytes, {
                    "Content-Disposition": 'attachment; filename="output_with_bgm.mp4"',
                    "X-BGM-Name": bgm_name,
                    "X-Source-Video-Mtime": source_mtime_iso,
                    "Access-Control-Expose-Headers": "X-BGM-Name, X-Source-Video-Mtime",
                })
            elif path == "/upload-bgm":
                from urllib.parse import parse_qs
                qs       = parse_qs(urlparse(self.path).query)
                filename = qs.get("name", ["bgm.mp3"])[0]
                result   = handle_upload_bgm(body, filename)
                self._send(200, "application/json", result)
            elif path == "/generate-thumbnail":
                thumb_json = handle_generate_thumbnail(body)
                self._send(200, "application/json", thumb_json)
            elif path == "/render-video":
                result_json = handle_render_video(body)
                self._send(200, "application/json", result_json)
            elif path == "/upload-drive":
                result_json = handle_upload_drive(body)
                self._send(200, "application/json", result_json)
            elif path == "/upload-youtube":
                result_json = handle_upload_youtube(body)
                self._send(200, "application/json", result_json)
            elif path == "/upload-instagram":
                result_json = handle_upload_instagram(body)
                self._send(200, "application/json", result_json)
            elif path == "/upload-facebook":
                result_json = handle_upload_facebook(body)
                self._send(200, "application/json", result_json)
            elif path == "/generate-community-post":
                result_json = handle_generate_community_post(body)
                self._send(200, "application/json", result_json)
            elif path == "/collect-products":
                result_json = handle_collect_products(body)
                self._send(200, "application/json", result_json)
            elif path == "/confirm-selection":
                result_json = handle_confirm_selection(body)
                self._send(200, "application/json", result_json)
            elif path == "/request-copy":
                result_json = handle_request_copy(body)
                self._send(200, "application/json", result_json)
            elif path == "/cancel-copy-request":
                result_json = handle_cancel_copy_request(body)
                self._send(200, "application/json", result_json)
            else:
                self._send(404, "text/plain", b"Not Found")
        except Exception:
            self._send(500, "text/plain", traceback.format_exc().encode())


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    # stdout 버퍼링 비활성화 — print가 즉시 터미널에 출력되도록
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    print(f"[서버 시작 중] 포트 {PORT} 바인딩...", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[OK] http://localhost:{PORT}", flush=True)
    print("     대시보드: http://localhost:3333/dashboard.html", flush=True)
    print("     종료: Ctrl+C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[종료] 서버를 정지합니다.", flush=True)
