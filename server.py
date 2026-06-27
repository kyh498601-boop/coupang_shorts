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

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
# 유튜브 썸네일 생성  (1280 × 720  A안 / B안)
# ──────────────────────────────────────────────────────────────
TW, TH = 1280, 720           # 유튜브 썸네일 해상도
C_GOLD  = (255, 215,   0)    # #FFD700 — 강조 골드
C_DARK  = ( 20,  60,  35)    # 진한 초록


def _thumb_outline(draw, text, x, y, font, fill, outline, ow=4):
    """텍스트 외곽선(outline) 렌더링."""
    for dx in range(-ow, ow + 1):
        for dy in range(-ow, ow + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def _shorten_hook(text: str, max_words: int = 5) -> str:
    """스킬 규칙: 텍스트는 최대 5단어 후킹 구. 초과 시 자동 단축."""
    words = text.split()
    if len(words) <= max_words:
        return text
    # 앞 max_words 단어만 — 끝 문장부호 제거 후 … 추가
    shortened = " ".join(words[:max_words]).rstrip("!?.,:;")
    return shortened + "…"


def _thumb_wrap(draw, text, font, max_w):
    """단어 단위 줄바꿈 (한글 포함)."""
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
    # 단어로 못 나눌 경우 글자 단위 fallback
    if not lines:
        lines = wrap_text(draw, text, font, max_w)
    return lines or [text]


def _render_thumb_a(product_img, hook_text, sub_text):
    """A안 — 좌측 제품이미지 / 우측 초록 그라디언트 + 대형 텍스트.
    스킬 규칙: Hook 최대 5단어, 최대 2줄, 우하단 금지, 포컬 엘리먼트 뱃지.
    """
    # Hook 단어 수 제한 (스킬: 5단어 이상이면 가독성 저하)
    hook_short = _shorten_hook(hook_text, max_words=5)

    img = Image.new("RGB", (TW, TH), C_DARK)

    # 좌→우 그라디언트 배경
    for x in range(TW):
        r = int(C_DARK[0] + (C_BRAND[0] - C_DARK[0]) * x / TW)
        g = int(C_DARK[1] + (C_BRAND[1] - C_DARK[1]) * x / TW)
        b_ch = int(C_DARK[2] + (C_BRAND[2] - C_DARK[2]) * x / TW)
        ImageDraw.Draw(img).line([(x, 0), (x, TH)], fill=(r, g, b_ch))

    # 좌측 제품이미지 (contain, 그림자)
    if product_img:
        pw, ph = 540, 600
        px, py = 30, (TH - ph) // 2
        src = product_img.convert("RGBA")
        sw, sh = src.size
        scale = min(pw / sw, ph / sh)
        nw, nh = int(sw * scale), int(sh * scale)
        resized = src.resize((nw, nh), Image.LANCZOS)
        ox, oy = px + (pw - nw) // 2, py + (ph - nh) // 2
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        shadow.paste(Image.new("RGBA", (nw, nh), (0, 0, 0, 120)), (ox + 8, oy + 8))
        img = Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGB")
        img_rgba = img.convert("RGBA")
        img_rgba.paste(resized, (ox, oy), resized)
        img = img_rgba.convert("RGB")

        # 포컬 엘리먼트: 제품이미지 위 긴박감 뱃지 (스킬: supporting element)
        draw_pre = ImageDraw.Draw(img)
        f_urg = _font(22, bold=True)
        urg = "⚡ 지금 특가"
        uw = int(draw_pre.textlength(urg, font=f_urg)) + 22
        uy = oy - 40
        if uy > 10:
            draw_pre.rounded_rectangle([ox, uy, ox + uw, uy + 32], radius=6,
                                       fill=(220, 50, 50))
            draw_pre.text((ox + 11, uy + 5), urg, font=f_urg, fill=C_WHITE)

    draw = ImageDraw.Draw(img)
    tx = 610

    # 상단 뱃지 (포컬 엘리먼트 강화)
    f_badge = _font(26, bold=True)
    badge = "🔥 BEST 추천"
    bw = int(draw.textlength(badge, font=f_badge)) + 26
    draw.rounded_rectangle([tx, 48, tx + bw, 92], radius=8, fill=C_GOLD)
    draw.text((tx + 13, 56), badge, font=f_badge, fill=(20, 20, 20))

    # Hook 텍스트 — 최대 2줄 (스킬: 소형 화면 가독성)
    f_hook = _font(82, bold=True)
    hook_lines = _thumb_wrap(draw, hook_short, f_hook, TW - tx - 44)[:2]
    hy = 108
    for line in hook_lines:
        _thumb_outline(draw, line, tx, hy, f_hook,
                       fill=C_WHITE, outline=(0, 0, 0), ow=4)
        hy += 100

    # 서브 텍스트 최대 1줄 (스킬: 작은 텍스트 최소화)
    if sub_text:
        f_sub = _font(32)
        sub_short = _shorten_hook(sub_text, max_words=8)  # 서브는 8단어까지
        sub_lines = _thumb_wrap(draw, sub_short, f_sub, TW - tx - 44)[:1]
        sy = hy + 14
        for line in sub_lines:
            draw.text((tx, sy), line, font=f_sub, fill=(210, 255, 210))

    # 하단 브랜드 바 (중앙 — 우하단 금지 준수)
    draw.rectangle([0, TH - 56, TW, TH], fill=C_GOLD)
    f_brand = _font(30, bold=True)
    brand = "🍀 생활꿀템연구소"
    bw2 = int(draw.textlength(brand, font=f_brand))
    draw.text(((TW - bw2) // 2, TH - 42), brand, font=f_brand, fill=C_DARK)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _render_thumb_b(product_img, hook_text, sub_text):
    """B안 — 풀블리드 블러 배경 + 중앙 대형 골드 텍스트 + 좌상단 제품썸네일.
    스킬 규칙: Hook 최대 5단어·2줄, 포컬 엘리먼트(숫자뱃지), 우하단 금지.
    제품이미지는 좌상단 배치 (우상단은 유튜브 시청시간 아이콘 영역 회피).
    """
    hook_short = _shorten_hook(hook_text, max_words=5)

    img = Image.new("RGB", (TW, TH), (15, 15, 15))

    # 풀블리드 블러 배경
    if product_img:
        src = product_img.convert("RGB")
        sw, sh = src.size
        sc = max(TW / sw, TH / sh)
        bw2, bh2 = int(sw * sc), int(sh * sc)
        resized = src.resize((bw2, bh2), Image.LANCZOS)
        cx2, cy2 = (bw2 - TW) // 2, (bh2 - TH) // 2
        cropped = resized.crop((cx2, cy2, cx2 + TW, cy2 + TH))
        blurred = cropped.filter(ImageFilter.GaussianBlur(radius=20))
        dark_layer = Image.new("RGB", (TW, TH), (0, 0, 0))
        img = Image.blend(blurred, dark_layer, 0.60)

        # 좌상단 제품이미지 (우상단 금지 — 유튜브 시청시간 아이콘 영역)
        pw, ph = 250, 250
        px2, py2 = 28, 28
        th_img = product_img.convert("RGBA")
        sw2, sh2 = th_img.size
        sc2 = min(pw / sw2, ph / sh2)
        nw2, nh2 = int(sw2 * sc2), int(sh2 * sc2)
        small = th_img.resize((nw2, nh2), Image.LANCZOS)
        img_rgba = img.convert("RGBA")
        # 흰 배경 프레임
        frame = Image.new("RGBA", (nw2 + 10, nh2 + 10), (255, 255, 255, 230))
        img_rgba.paste(frame, (px2 - 5, py2 - 5))
        img_rgba.paste(small, (px2 + (pw - nw2) // 2, py2 + (ph - nh2) // 2), small)
        img = img_rgba.convert("RGB")

    draw = ImageDraw.Draw(img)

    # 포컬 엘리먼트: 할인/긴박감 수치 뱃지 (스킬: bold number or prop)
    f_focal = _font(26, bold=True)
    focal_text = "최대 50% 할인"
    fw = int(draw.textlength(focal_text, font=f_focal)) + 28
    draw.rounded_rectangle([28, 290, 28 + fw, 334], radius=8, fill=(220, 50, 50))
    draw.text((28 + 14, 298), focal_text, font=f_focal, fill=C_WHITE)

    # 중앙 Hook 텍스트 — 최대 2줄 (스킬 핵심 규칙)
    f_hook = _font(96, bold=True)
    hook_lines = _thumb_wrap(draw, hook_short, f_hook, TW - 120)[:2]
    total_h = len(hook_lines) * 114
    hy = (TH - total_h) // 2 - 20
    for line in hook_lines:
        tw2 = int(draw.textlength(line, font=f_hook))
        _thumb_outline(draw, line, (TW - tw2) // 2, hy, f_hook,
                       fill=C_GOLD, outline=(0, 0, 0), ow=5)
        hy += 114

    # 서브 텍스트 최대 1줄
    if sub_text:
        f_sub = _font(34)
        sub_short = _shorten_hook(sub_text, max_words=8)
        sub_lines = _thumb_wrap(draw, sub_short, f_sub, TW - 160)[:1]
        sy = hy + 12
        for line in sub_lines:
            sw3 = int(draw.textlength(line, font=f_sub))
            draw.text(((TW - sw3) // 2, sy), line, font=f_sub, fill=(240, 240, 240))

    # 하단 초록 바 (중앙 — 우하단 금지)
    draw.rectangle([0, TH - 56, TW, TH], fill=C_BRAND)
    f_brand = _font(30, bold=True)
    brand = "🍀 생활꿀템연구소  |  프로필 링크에서 구매"
    bw3 = int(draw.textlength(brand, font=f_brand))
    draw.text(((TW - bw3) // 2, TH - 42), brand, font=f_brand, fill=C_WHITE)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def handle_generate_thumbnail(body: bytes) -> bytes:
    """슬라이드 카피 + 제품이미지로 유튜브 썸네일 A/B 2안 생성."""
    payload      = json.loads(body)
    slides       = payload.get("slides", [])
    images_b64   = payload.get("images", [])
    product_name = payload.get("productName", "")

    # 슬라이드 0 Hook 카피 추출 (customHook 있으면 우선 사용)
    s0          = slides[0] if slides else {}
    auto_hook   = s0.get("headline") or s0.get("title") or product_name or "오늘의 생활꿀템"
    hook_text   = payload.get("customHook", "").strip() or auto_hook
    sub_text    = (s0.get("body") or "").split(".")[0].strip()

    pil_images  = [img for img in (b64_to_pil(b) for b in images_b64) if img]
    product_img = pil_images[0] if pil_images else None

    print(f"[썸네일] hook={hook_text!r:.40}  sub={sub_text!r:.40}", flush=True)

    a_bytes = _render_thumb_a(product_img, hook_text, sub_text)
    b_bytes = _render_thumb_b(product_img, hook_text, sub_text)
    print(f"[썸네일] A={len(a_bytes)//1024}KB  B={len(b_bytes)//1024}KB", flush=True)

    result = {
        "a": "data:image/png;base64," + base64.b64encode(a_bytes).decode(),
        "b": "data:image/png;base64," + base64.b64encode(b_bytes).decode(),
    }
    return json.dumps(result).encode()


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
