# -*- coding: utf-8 -*-
"""
생활꿀템연구소 대시보드 서버  (port 3333)

GET  /              -> dashboard.html
GET  /<path>        -> static file
POST /generate-png  -> JSON {slides, images(base64[]), productName, category}
                       -> carousel_1080x1350.zip  (PNG x10)

[필수 수치 - 절대 변경 금지]
  PNG  : 1080 x 1350
  상단  : 810px  (60%) - 제품이미지 cover
  하단  : 513px  (38%) - 반투명 흰 카드 alpha=220
  blur : GaussianBlur(28)
  dpi  : (72, 72)
  font : C:/Windows/Fonts/malgun.ttf / malgunbd.ttf
"""

import base64
import io
import json
import os
import random
import re
import subprocess
import traceback
import urllib.request
import wave
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, urlencode

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

# ──────────────────────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────────────────────
PORT     = 3333
BASE_DIR = Path(__file__).parent
BGM_DIR  = BASE_DIR / "bgm"

W           = 1080
H           = 1350
IMG_AREA_H  = 810       # 상단 60%  (제품이미지 끝 y)
CARD_Y      = 810       # 카드 시작 = 이미지 끝 (갭 제거)
CARD_H      = H - 810   # 540px
CARD_ALPHA  = 220
BLUR_RADIUS = 28
DPI         = (72, 72)

FONT_REG  = "C:/Windows/Fonts/malgun.ttf"
FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"

# 브랜드 컬러
C_BRAND     = (46, 139,  87)   # #2E8B57
C_BRAND_DRK = (27,  94,  59)   # #1B5E3B
C_WHITE     = (255, 255, 255)
C_GRAY_800  = ( 66,  66,  66)  # #424242
C_GRAY_600  = (117, 117, 117)  # #757575

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
# 폰트
# ──────────────────────────────────────────────────────────────
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    except Exception:
        return ImageFont.load_default()


# ──────────────────────────────────────────────────────────────
# 텍스트 래핑 (한국어 글자 단위)
# ──────────────────────────────────────────────────────────────
def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines = []
    for para in text.split("\n"):
        line = ""
        for ch in para:
            if draw.textlength(line + ch, font=font) <= max_w:
                line += ch
            else:
                if line:
                    lines.append(line)
                line = ch
        if line:
            lines.append(line)
    return lines or [text]


def draw_block(draw, text, x, y, font, fill, max_w, line_h, max_lines) -> int:
    lines = wrap_text(draw, text, font, max_w)[:max_lines]
    for i, line in enumerate(lines):
        draw.text((x, y + i * line_h), line, font=font, fill=fill)
    return len(lines)


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
# 슬라이드 1장 렌더  (1080 x 1350 PNG bytes)
# ──────────────────────────────────────────────────────────────
def render_slide(idx: int, slide: dict, product_img: Image.Image | None, product_name: str) -> bytes:

    # ── 1. 풀블리드 블러 배경 (cover, 1080x1350 전체) ──────────
    bg = Image.new("RGBA", (W, H), (30, 30, 30, 255))

    if product_img:
        src = product_img.convert("RGB")
        sw, sh = src.size
        scale = max(W / sw, H / sh)           # cover: 빈틈 없이 채움
        bw, bh = int(sw * scale), int(sh * scale)
        resized = src.resize((bw, bh), Image.LANCZOS)
        # 중앙 crop
        cx, cy = (bw - W) // 2, (bh - H) // 2
        blurred = resized.crop((cx, cy, cx + W, cy + H))
        blurred = blurred.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
        # brightness 0.55 (45% 어둡게)
        dark = Image.new("RGB", (W, H), (0, 0, 0))
        blurred = Image.blend(blurred, dark, 0.45)
        bg = blurred.convert("RGBA")

    # ── 2. 상단 810px: 제품이미지 cover ────────────────────────
    if product_img:
        src = product_img.convert("RGBA")
        sw, sh = src.size
        scale = max(W / sw, IMG_AREA_H / sh)  # cover: 810px 영역 꽉 채움
        dw, dh = int(sw * scale), int(sh * scale)
        cover = src.resize((dw, dh), Image.LANCZOS)
        cx = (dw - W) // 2
        cy = (dh - IMG_AREA_H) // 2
        crop = cover.crop((cx, cy, cx + W, cy + IMG_AREA_H))
        bg.paste(crop, (0, 0), crop)

    # ── 3. 하단 513px: 반투명 흰 카드 (alpha=220) ───────────────
    card_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(card_layer).rounded_rectangle(
        [0, CARD_Y, W, H], radius=32,
        fill=(255, 255, 255, CARD_ALPHA),
    )
    bg = Image.alpha_composite(bg.convert("RGBA"), card_layer)
    draw = ImageDraw.Draw(bg)

    # ── 4. 슬라이드 번호 뱃지 ──────────────────────────────────
    bx, by = 52, CARD_Y + 36
    draw.rounded_rectangle([bx, by, bx + 72, by + 36], radius=8, fill=C_BRAND)
    f_badge = _font(22, bold=True)
    label   = f"{idx + 1} / 10"
    lw      = draw.textlength(label, font=f_badge)
    draw.text((bx + (72 - lw) // 2, by + 7), label, font=f_badge, fill=C_WHITE)

    # ── 5. Headline (bold 54px) ─────────────────────────────────
    headline = slide.get("headline") or slide.get("title") or ""
    draw_block(draw, headline,
               x=52, y=CARD_Y + 108,
               font=_font(54, bold=True), fill=C_BRAND_DRK,
               max_w=W - 104, line_h=66, max_lines=2)

    # ── 7. Body (36px) ──────────────────────────────────────────
    body = slide.get("body") or ""
    draw_block(draw, body,
               x=52, y=CARD_Y + 200,
               font=_font(36), fill=C_GRAY_800,
               max_w=W - 104, line_h=52, max_lines=4)

    # ── 8. 제품명 서브텍스트 (30px) ─────────────────────────────
    pname = product_name or "제품명"
    if len(pname) > 22:
        pname = pname[:22] + "…"
    draw.text((52, CARD_Y + CARD_H - 80), pname, font=_font(30), fill=C_GRAY_600)

    # ── 9. 워터마크 (bold 26px, 우측) ───────────────────────────
    f_wm = _font(26, bold=True)
    wm   = "생활꿀템연구소"
    wm_w = draw.textlength(wm, font=f_wm)
    draw.text((W - 52 - wm_w, CARD_Y + CARD_H - 80), wm, font=f_wm,
              fill=(46, 139, 87, 191))   # rgba(46,139,87,0.75)

    # ── 10. 하단 진행바 (4px) ───────────────────────────────────
    draw.rectangle([0, H - 4, W, H], fill=(224, 224, 224))
    draw.rectangle([0, H - 4, int(W * (idx + 1) / 10), H], fill=C_BRAND)

    # ── 11. PNG bytes ────────────────────────────────────────────
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

    # base64 -> PIL
    pil_images = [img for img in (b64_to_pil(b) for b in images_b64) if img]

    # 원본 제품 이미지를 파일로 저장 (썸네일 테스트 및 재사용용)
    if pil_images:
        orig_path = os.path.join(os.path.dirname(__file__), "product_original.png")
        pil_images[0].convert("RGB").save(orig_path, format="PNG")
        print(f"[PNG] 원본 제품 이미지 저장: {orig_path}", flush=True)

    if not slides:
        slides = [{"title": f"슬라이드 {i+1}", "headline": "", "body": ""} for i in range(10)]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, slide in enumerate(slides[:10]):
            img = pil_images[i % len(pil_images)] if pil_images else None
            zf.writestr(f"slide_{i+1:02d}.png", render_slide(i, slide, img, product_name))

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
    text = re.sub(r"linktr\.ee/\S+", "", text)            # 링크트리 제거
    text = re.sub(r"[→#@·•※①②③④⑤]", " ", text)          # 기호 → 공백
    # 이모지 제거 (유니코드 카테고리 So/Cs)
    text = "".join(
        ch for ch in text
        if unicodedata.category(ch) not in ("So", "Cs", "Mn")
        and not (0x1F300 <= ord(ch) <= 0x1FAFF)
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text


# 슬라이드 인덱스별 완전 독립 나레이션 문장 (카피 텍스트 직접 삽입 안 함)
# p = 제품명, 각 문장은 구어체로 완결된 독립 문장
_NARRATION_TEMPLATES = [
    # 0 — 후킹: 시선 차단, 질문형
    lambda p: f"잠깐, 이거 한 번만 봐주세요. 오늘 소개할 제품 진짜예요.",
    # 1 — 추천 대상: 공감형
    lambda p: f"이런 고민 있으신 분들, 딱 기다리던 제품이에요.",
    # 2 — 핵심 성분·기능: 호기심 유발
    lambda p: f"어떤 성분이 들어 있는지 먼저 확인해볼게요. 생각보다 놀라워요.",
    # 3 — 효능·성능: 신뢰 구축
    lambda p: f"흡수력이랑 보습력이 동시에 올라가요. 직접 써보니까 확실히 달라요.",
    # 4 — 사용감·경험: 공감 + 실사용
    lambda p: f"발랐을 때 느낌이 정말 좋아요. 끈적임 없이 바로 스며들어요.",
    # 5 — 전후 효과·사용 시나리오: 기대감
    lambda p: f"꾸준히 쓰면 피부 결이 달라지는 게 느껴져요. 2주면 충분해요.",
    # 6 — 인증·수상: 권위·신뢰
    lambda p: f"피부과 테스트 완료 제품이에요. 인증이 신뢰를 말해주죠.",
    # 7 — 상품평 재가공: 사회적 증거
    lambda p: f"수만 명이 선택했고, 평점도 굉장히 높아요. 이유가 있는 거예요.",
    # 8 — 특가 안내: 긴박감 (가격 수치 없이)
    lambda p: f"지금 딱 좋은 타이밍이에요. 역대급 할인 진행 중이거든요.",
    # 9 — CTA: 행동 유도
    lambda p: f"구매 링크는 프로필에서 확인하세요. 놓치면 후회해요!",
]


def build_narration(slide: dict, product_name: str, idx: int) -> str:
    """슬라이드 카피는 참고만 하고, 완전 독립된 구어체 나레이션 문장을 반환."""
    import re
    p = product_name or "이 제품"

    template  = _NARRATION_TEMPLATES[idx] if idx < len(_NARRATION_TEMPLATES) else _NARRATION_TEMPLATES[-1]
    narration = template(p)

    # 기호·중복 공백 정리
    narration = re.sub(r"[→#\*_`]", "", narration)
    narration = re.sub(r"\s{2,}", " ", narration).strip()

    # 2.5초 기준 한국어 약 44자 이내 트림
    if len(narration) > 44:
        narration = narration[:43].rsplit(" ", 1)[0] + "…"

    return narration


def handle_generate_srt(body: bytes) -> bytes:
    payload      = json.loads(body)
    slides       = payload.get("slides", [])
    product_name = payload.get("productName", "")
    ms_per_slide = 2500  # 슬라이드당 2.5초

    lines = []
    for i, slide in enumerate(slides[:10]):
        start     = i * ms_per_slide
        end       = start + ms_per_slide
        narration = build_narration(slide, product_name, i)
        lines.append(str(i + 1))
        lines.append(f"{ms_to_srt_time(start)} --> {ms_to_srt_time(end)}")
        lines.append(narration)
        lines.append("")

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
# /generate-tts 핸들러  (Gemini TTS)
# ──────────────────────────────────────────────────────────────

# 슬라이드 인덱스별 감정 스타일 프리픽스
_EMOTION_PREFIX = {
    0: "(힘있고 강조하며, 잠깐 멈추고) ",   # 후킹 — [emphasis][pause=0.5]
    3: "(밝고 명랑하게) ",                  # 효능 — [cheerful]
    7: "(따뜻하고 친근하게) ",              # 상품평 — [warm]
    8: "(빠르고 긴박하게) ",                # 특가 — [urgent]
    9: "(힘있고 강조하며) ",                # CTA — [emphasis]
}


def _adjust_wav_speed(wav_path: Path, target_sec: float) -> tuple:
    """WAV 길이가 목표 초과 시 ffmpeg atempo로 속도 조정. (wav_bytes, 실제초) 반환."""
    actual_sec = _get_audio_duration(wav_path)
    print(f"[TTS] 실제 길이: {actual_sec:.2f}s  목표: {target_sec:.2f}s", flush=True)

    # 목표 이내면 그대로 반환
    if actual_sec <= target_sec * 1.02:
        print("[TTS] 속도 조정 불필요", flush=True)
        return wav_path.read_bytes(), actual_sec

    speed = min(round(actual_sec / target_sec, 4), 2.0)   # atempo 최대 2.0
    print(f"[TTS] 속도 {speed}x 조정 중 (atempo)...", flush=True)

    tmp = wav_path.parent / "_tts_atempo_tmp.wav"
    cmd = ["ffmpeg", "-y", "-i", str(wav_path), "-af", f"atempo={speed}", str(tmp)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        tmp.replace(wav_path)
        actual_sec = _get_audio_duration(wav_path)
        print(f"[TTS] 조정 후 길이: {actual_sec:.2f}s", flush=True)
    else:
        print(f"[TTS] atempo 실패, 원본 유지: {res.stderr[:300]}", flush=True)
    return wav_path.read_bytes(), actual_sec


def handle_generate_tts(body: bytes) -> tuple:
    payload      = json.loads(body)
    slides       = payload.get("slides", [])
    product_name = payload.get("productName", "")
    voice_name   = payload.get("voiceName", "Kore")

    # 나레이션 텍스트 빌드 (SRT와 동일한 build_narration 재사용)
    lines = []
    for i in range(10):
        slide     = slides[i] if i < len(slides) else {}
        narration = build_narration(slide, product_name, i)
        prefix    = _EMOTION_PREFIX.get(i, "")
        lines.append(f"{prefix}{narration}")
    full_text = "\n\n".join(lines)

    # Gemini TTS 호출
    api_key = _get_env("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

    try:
        from google import genai
        from google.genai import types as gtypes
    except ImportError:
        raise ImportError("google-genai 패키지가 필요합니다: pip install google-genai")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=full_text,
        config=gtypes.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=gtypes.SpeechConfig(
                voice_config=gtypes.VoiceConfig(
                    prebuilt_voice_config=gtypes.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            )
        )
    )

    try:
        part     = response.candidates[0].content.parts[0]
        raw      = part.inline_data.data
        raw_type = type(raw).__name__
        raw_len  = len(raw) if raw else 0
        print(f"[TTS] inline_data.data type={raw_type} len={raw_len}", flush=True)

        if isinstance(raw, bytes):
            pcm_data = raw                          # 이미 바이너리 PCM
        else:
            raw     += "=" * (-len(raw) % 4)       # 패딩 보정
            pcm_data = base64.b64decode(raw)        # base64 문자열 → PCM

        print(f"[TTS] PCM 크기: {len(pcm_data)} bytes", flush=True)
    except Exception:
        traceback.print_exc()
        raise

    # PCM(24000Hz, 16-bit, mono) → WAV
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm_data)
    wav_bytes = wav_buf.getvalue()

    # output_narration.wav 서버 저장
    out_path = BASE_DIR / "output_narration.wav"
    out_path.write_bytes(wav_bytes)
    print(f"[TTS] 저장 완료: {out_path} ({len(wav_bytes)//1024}KB)", flush=True)

    # 숏폼 최적 길이: 25~30초 랜덤 목표, 초과 시 atempo 조정
    target_sec = random.uniform(25.0, 30.0)
    wav_bytes, actual_sec = _adjust_wav_speed(out_path, target_sec)
    print(f"[TTS] 최종 길이: {actual_sec:.2f}s (목표 {target_sec:.2f}s)", flush=True)

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


def _mix_bgm(bgm_path: Path, bgm_volume: float, out_path: Path) -> None:
    """ffmpeg으로 BGM + 영상(+ 선택적 나레이션) 믹싱.
    나레이션 있을 때: 나레이션 길이 기준, BGM은 나레이션 끝 후 1초 페이드아웃.
    나레이션 없을 때: 영상 길이 기준으로 BGM 루프.
    """
    mp4_path = BASE_DIR / "output" / "shopping-shorts.mp4"
    if not mp4_path.exists():
        raise FileNotFoundError(
            f"MP4가 없습니다. render.bat를 먼저 실행하세요.\n경로: {mp4_path}"
        )

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

        # BGM: 나레이션 길이까지 루프 → 끝 1초 페이드아웃
        # amix: narration 끝날 때까지 혼합 후 BGM 페이드아웃 포함
        fc = (
            f"[0:a]volume={vol_str},{aformat},"
            f"atrim=end={total_dur},"
            f"afade=t=out:st={fade_start}:d=1.0[bgm];"
            f"[2:a]volume=1.0,{aformat}[narr];"
            f"[bgm][narr]amix=inputs=2:duration=longest:dropout_transition=2[a]"
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
        print("[BGM] 모드: 영상 + BGM + 나레이션 (나레이션 기준 길이)", flush=True)
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
    """랜덤 BGM 선택 → 믹싱 → (mp4_bytes, bgm_name) 반환."""
    payload    = json.loads(body)
    bgm_volume = float(payload.get("bgmVolume", 0.15))
    exclude    = payload.get("excludeBgm") or None

    bgm_path = _pick_random_bgm(exclude=exclude)
    out_path = BASE_DIR / "output_with_bgm.mp4"
    print(f"[BGM] 선택: {bgm_path.name}", flush=True)
    _mix_bgm(bgm_path, bgm_volume, out_path)
    return out_path.read_bytes(), bgm_path.name


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


def _tw_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    except Exception:
        return ImageFont.load_default()


def _tw_remove_bg(img: Image.Image) -> Image.Image:
    """rembg로 배경 제거 → 투명 RGBA 반환.
    알파 임계값(128) 처리로 반투명 잔상 완전 제거.
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
        # 1단계: 확실한 배경(α<30) 완전 제거 → 색번짐 잔상 차단
        a = a.point(lambda v: 0 if v < 30 else v)
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
    뱃지: 🔥 BEST 추천 (골드, 우측 상단 가운데 정렬)
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
    BADGE_LABEL = "🔥 BEST 추천"
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


# ──────────────────────────────────────────────────────────────
# Google Drive 업로드  (google-auth, google-api-python-client)
#
# 최초 실행: credentials.json 필요 → 브라우저 인증 → token.json 자동 저장
# 이후 실행: token.json으로 자동 로그인 (만료 시 자동 갱신)
#
# .env 선택 키:
#   GOOGLE_CREDENTIALS_JSON  = credentials.json 경로 (기본: 서버와 같은 폴더)
#   GOOGLE_TOKEN_JSON        = token.json 경로     (기본: 서버와 같은 폴더)
#   GOOGLE_DRIVE_FOLDER_ID   = 업로드 대상 Drive 폴더 ID
# ──────────────────────────────────────────────────────────────
import time
import mimetypes

_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _build_drive_service():
    """OAuth2 자격증명으로 Drive API 서비스 객체 반환.

    1. token.json 존재 → 로드 후 만료 시 자동 갱신
    2. token.json 없음 → 브라우저 인증 플로우 실행 → token.json 저장
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "Google API 패키지가 필요합니다:\n"
            "  pip install google-auth google-auth-oauthlib google-api-python-client"
        )

    creds_path = Path(_get_env("GOOGLE_CREDENTIALS_JSON") or BASE_DIR / "credentials.json")
    token_path = Path(_get_env("GOOGLE_TOKEN_JSON") or BASE_DIR / "token.json")

    creds = None

    # 저장된 토큰 로드
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _DRIVE_SCOPES)

    # 토큰 없거나 만료됐으면 갱신 또는 재인증
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[Drive] 토큰 만료 → 자동 갱신 중...", flush=True)
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"credentials.json을 찾을 수 없습니다: {creds_path}\n"
                    "Google Cloud Console에서 OAuth 클라이언트 ID(데스크톱 앱)를 생성하고\n"
                    "credentials.json을 서버 폴더에 저장하세요."
                )
            print("[Drive] 브라우저 인증 시작...", flush=True)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(creds_path), _DRIVE_SCOPES
            )
            creds = flow.run_local_server(port=0, open_browser=True)
            print("[Drive] 인증 완료.", flush=True)

        # 갱신된 토큰 저장
        token_path.write_text(creds.to_json(), encoding="utf-8")
        print(f"[Drive] 토큰 저장: {token_path}", flush=True)

    return build("drive", "v3", credentials=creds, cache_discovery=False)


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


def drive_upload_with_retry(file_paths: list[Path], max_retry: int = 3) -> list[dict]:
    """파일 목록을 Drive에 업로드. 실패 시 최대 max_retry번 재시도.

    반환: [{name, id, webViewLink, status}, ...]
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

        rel = path.lstrip("/")
        fp  = BASE_DIR / "dashboard.html" if rel in ("", "dashboard.html") else BASE_DIR / rel
        if fp.exists() and fp.is_file():
            self._send(200, MIME.get(fp.suffix.lower(), "application/octet-stream"), fp.read_bytes())
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
                mp4_bytes, bgm_name = handle_generate_bgm(body)
                self._send(200, "video/mp4", mp4_bytes, {
                    "Content-Disposition": 'attachment; filename="output_with_bgm.mp4"',
                    "X-BGM-Name": bgm_name,
                    "Access-Control-Expose-Headers": "X-BGM-Name",
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
            elif path == "/upload-drive":
                result_json = handle_upload_drive(body)
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
