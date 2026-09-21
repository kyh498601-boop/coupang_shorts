# -*- coding: utf-8 -*-
"""STEP1 전처리: AiPrice 원본(메인+설명 이미지) -> 사람이 고를 후보 풀. server.py와 무관한 독립 모듈.

사용법:
  python step1_image_selector/selector.py --main <메인폴더> --desc <설명폴더> [--out <출력폴더>]
  python step1_image_selector/selector.py --selftest

출력: <out>/candidates/*.png|jpg (후보 풀) + <out>/report.json (통과/배제/경고 사유)
      + <out>/contact_sheet.png (후보 전체 썸네일 그리드, 경고 후보는 빨간 테두리+라벨).
설명 이미지는 파일명 순으로 세로 이어붙인 뒤(1~3개 분할 대응) 하나로 처리한다.

업로드 순서: candidates/ 파일명은 "NN_" 두 자리 순번으로 시작한다 (기본 순서 = 메인 -> 설명).
컨택트시트를 보고 이 번호만 원하는 순서로 고치면(예: 01_main_..., 02_desc_...) 파일명 정렬 = 업로드 순서.
반드시 두 자리로 쓸 것 — "2_"는 "10_"보다 뒤로 정렬된다. 클릭 순서는 브라우저가 보장 안 하므로 쓰지 않는다.
"""
import argparse
import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import select_and_crop_sections as scs   # 텍스트 행 판정 상수 재사용 (EDGE_DIFF_THRESH 등)
import split_detail_page as sdp          # find_gaps 재사용 (여백 임계값은 2026-09-18 실측치 그대로)

# ── 임시 임계값 (세제+에어컨 2개 상품 기준 — 실제 상품으로 튜닝 필요) ─────────────
BG_BRIGHT_PIXEL = 240      # 이 밝기 이상이면 "밝은 픽셀"
BG_BRIGHT_RATIO = 0.15     # 밝은 픽셀 비율이 이 이상이면 A(흰 여백형), 미만이면 B(연속형). 실측 A=25.6%(세제), B=6.7%(에어컨) 중간값
COLOR_JUMP_THRESH = 120    # B: 행 평균색 RGB 절대차 합이 이 이상이면 색 급변 (에어컨 스윕: 30은 사진 한복판을 가름, 200은 과소분할)
COLOR_JUMP_WIN = 4         # B: 급변 판정에 쓰는 위/아래 행 수(노이즈 완화)
MIN_SECTION_H = 150        # 이보다 짧은 섹션은 배제
BAND_MIN_H = 12            # 스무딩 후 연속 텍스트 행이 이 이상이면 "텍스트 밴드" (창 전체를 스캔 — 상/하단 존만 보면 존 경계 바로 옆 글씨를 놓침).
                           # 라벨 11장 스윕: 20은 작은 글씨(부제·면책문구·피라미드)를 놓치고, 엣지 40 이하는 커튼/통풍구 질감을 텍스트로 오탐
BAND_PAD = 40              # 밴드 위아래 여유. 큰 글씨는 획 안쪽 행이 감지 안 돼 실제 글자가 밴드보다 넓음 (에어컨 헤드라인: 감지 430~551, 실제 약 420~590)
RATIO_MAX = 1.3            # 가로/세로 경고 기준 (임시). server.py가 contain이라 PNG는 안 잘려 경고만
RATIO_HARD_MAX = 2.0       # 이 초과는 배제. 에어컨 실측: 쓸만한 컷 1.7~1.8, 카드조각/캡션바 조각 3.0 이상
TEXT_CARD_RATIO = 0.30     # 메인: 텍스트 행이 세로의 이 비율 이상이면 텍스트카드로 배제
TEXT_EDGE_DIFF = 80        # 텍스트 행 엣지 기준. scs 값(25/0.015)은 라벨·일러스트·질감까지 텍스트로 잡음
TEXT_ROW_DENSITY = 0.05    # (세제: 메인 실사 최대 14%, 설명 섹션 1~17%로 하락, 0.10은 텍스트도 놓침)
DESC_EDGE_DIFF = 50        # 설명 섹션 텍스트존 검사 전용 (파란 배경 흰 글씨는 80으로 못 잡음 — 세제 스윕 40~60 중 50)
DESC_ROW_DENSITY = 0.03    # 왼쪽 정렬 짧은 글줄은 행 밀도가 낮아 0.05로는 놓침. 0.02 이하는 병 라벨까지 텍스트로 잡아 제품컷 배제
CROP_ASPECT = 0.8         # 세로크롭 목표 윈도우 = 가로/0.8 (4:5). 못 맞추면 80%·60% 높이까지 축소
DUP_HAMMING = 4            # 메인: dHash 해밍거리가 이 이하면 중복

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _pixels(img: Image.Image) -> list:
    """행 우선 픽셀 리스트 (getdata 대체 — Pillow 14에서 제거 예정, 구버전에서도 동작)."""
    px = img.load()
    return [px[x, y] for y in range(img.height) for x in range(img.width)]


# ── 공통: 텍스트 행 감지 (select_and_crop_sections.row_text_flags의 C-속도 버전) ──
def _smooth(flags: list[bool]) -> list[bool]:
    n, f = len(flags), list(flags)

    def runs():
        i = 0
        while i < n:
            j = i
            while j < n and f[j] == f[i]:
                j += 1
            yield i, j, f[i]
            i = j

    for i, j, v in list(runs()):                      # 짧은 끊김은 한 문단으로 메움
        if not v and 0 < i and j < n and j - i <= scs.FILL_GAP:
            f[i:j] = [True] * (j - i)
    for i, j, v in list(runs()):                      # 너무 짧은 덩어리는 노이즈
        if v and j - i < scs.MIN_RUN:
            f[i:j] = [False] * (j - i)
    return f


def text_rows(img: Image.Image, edge: int = TEXT_EDGE_DIFF,
              dens_min: float = TEXT_ROW_DENSITY) -> list[bool]:
    g = img.convert("L")
    w, h = g.size
    if w < 2:
        return [False] * h
    d = ImageChops.difference(g.crop((1, 0, w, h)), g.crop((0, 0, w - 1, h)))
    d = d.point(lambda p: 255 if p > edge else 0)
    dens = _pixels(d.resize((1, h), Image.BOX))    # 행별 엣지 밀도 (0~255)
    return _smooth([v / 255 >= dens_min for v in dens])


def text_bands(flags: list[bool]) -> list[tuple[int, int]]:
    """연속 텍스트 행 BAND_MIN_H 이상을 (시작, 끝)으로. 위아래로 BAND_PAD씩 넓힘."""
    n, out, i = len(flags), [], 0
    while i < n:
        if flags[i]:
            j = i
            while j < n and flags[j]:
                j += 1
            if j - i >= BAND_MIN_H:
                out.append((max(i - BAND_PAD, 0), min(j + BAND_PAD, n)))
            i = j
        else:
            i += 1
    return out


def hits_band(bands: list[tuple[int, int]], top: int, hh: int) -> bool:
    """윈도우 [top, top+hh)가 텍스트 밴드와 한 행이라도 겹치는가."""
    return any(a < top + hh and b > top for a, b in bands)


# ── 메인 이미지: 텍스트카드 배제 + 해시 중복 제거 ────────────────────────────────
def dhash(img: Image.Image) -> int:
    p = _pixels(img.convert("L").resize((9, 8), Image.LANCZOS))
    return sum(1 << (r * 8 + c) for r in range(8) for c in range(8) if p[r * 9 + c] > p[r * 9 + c + 1])


def classify_main(items: list[tuple[str, Image.Image]]) -> list[tuple[str, bool, str]]:
    """items=[(이름, 이미지)] -> [(이름, 통과여부, 사유)]"""
    out, hashes = [], []
    for name, img in items:
        flags = text_rows(img)
        ratio = sum(flags) / max(len(flags), 1)
        if ratio >= TEXT_CARD_RATIO:
            out.append((name, False, f"텍스트카드 (텍스트 행 {ratio:.0%})"))
            continue
        h = dhash(img)
        dup = next((n for n, hh in hashes if bin(h ^ hh).count("1") <= DUP_HAMMING), None)
        if dup:
            out.append((name, False, f"중복 ({dup}와 동일)"))
            continue
        hashes.append((name, h))
        out.append((name, True, f"텍스트 행 {ratio:.0%}"))
    return out


# ── 설명 이미지: A/B 판정 -> 분할 -> 텍스트 밴드 회피 크롭 -> 비율 체크 ───────────────────
def bright_ratio(img: Image.Image) -> float:
    return sum(img.convert("L").histogram()[BG_BRIGHT_PIXEL:]) / (img.width * img.height)


def split_a(img: Image.Image) -> list[tuple[int, int]]:
    """흰 여백 구간(split_detail_page.find_gaps) 사이를 섹션으로. 여백이 부족하면 [] (분할 실패)."""
    h = img.height
    bright = _pixels(img.convert("L").resize((1, h), Image.BOX))
    gaps = sdp.find_gaps(bright)
    if len(gaps) < sdp.MIN_GAPS_REQUIRED:
        return []
    spans, prev = [], 0
    for g0, g1 in gaps:
        if g0 > prev:
            spans.append((prev, g0))
        prev = g1
    if h > prev:
        spans.append((prev, h))
    return spans


def split_b(img: Image.Image) -> list[tuple[int, int]]:
    """행 평균색이 급변하는 지점(color-jump)을 경계로 분할."""
    m = _pixels(img.convert("RGB").resize((1, img.height), Image.BOX))
    h, k = len(m), COLOR_JUMP_WIN
    score = [0.0] * h
    for y in range(k, h - k + 1):
        a = [sum(m[y - k + i][c] for i in range(k)) / k for c in range(3)]
        b = [sum(m[y + i][c] for i in range(k)) / k for c in range(3)]
        score[y] = sum(abs(a[c] - b[c]) for c in range(3))
    peaks, y = [], 0
    while y < h:                                       # 연속된 급변 구간마다 최대점 하나
        if score[y] >= COLOR_JUMP_THRESH:
            j = y
            while j < h and score[j] >= COLOR_JUMP_THRESH:
                j += 1
            peaks.append(max(range(y, j), key=score.__getitem__))
            y = j
        else:
            y += 1
    cuts, last = [], 0
    for p in peaks:                                    # 너무 붙은 경계는 무시
        if p - last >= MIN_SECTION_H and h - p >= MIN_SECTION_H:
            cuts.append(p)
            last = p
    edges = [0, *cuts, h]
    return list(zip(edges, edges[1:]))


def fit_section(sec: Image.Image) -> tuple[Image.Image | None, str]:
    """텍스트 밴드가 창에 걸치면 세로크롭(밴드 피하기) -> 가로크롭 순으로 시도. 실패 시 (None, 사유)."""
    w, h = sec.size
    bands = text_bands(text_rows(sec, DESC_EDGE_DIFF, DESC_ROW_DENSITY))
    h0 = min(h, round(w / CROP_ASPECT))                # 통이미지는 4:5 윈도우로, 짧은 섹션은 통째로
    # 세로크롭: 큰 윈도우 우선(4:5 -> 80% -> 비율 상한에 딱 맞는 최소 높이), 중앙에 가까운 위치 우선
    for hh in [x for x in dict.fromkeys([h0, int(h0 * 0.8), int(w / RATIO_MAX) + 1]) if MIN_SECTION_H <= x <= h0]:
        for t in sorted(range(0, h - hh + 1, 10), key=lambda t: abs(t - (h - hh) // 2)):
            if not hits_band(bands, t, hh):
                return sec.crop((0, t, w, t + hh)), ("그대로" if hh == h else f"세로크롭 {hh}px (y={t})")
    t0 = (h - h0) // 2                                 # 가로크롭: 중앙 윈도우에서 폭을 줄이고 열 위치를 옮겨가며 재판정
    mid = sec.crop((0, t0, w, t0 + h0))
    for frac in (0.9, 0.8, 0.7, 0.6):
        cw = int(w * frac)
        for x in sorted(range(0, w - cw + 1, max(w // 10, 1)), key=lambda x: abs(x - (w - cw) // 2)):
            c = mid.crop((x, 0, x + cw, h0))
            if not text_bands(text_rows(c, DESC_EDGE_DIFF, DESC_ROW_DENSITY)):
                return c, f"가로크롭 {frac:.0%}"
    return None, "텍스트 밴드 겹침 (세로/가로 크롭 실패)"


def desc_candidates(img: Image.Image) -> tuple[str, float, list[dict]]:
    """반환: (타입, 밝은픽셀비율, [{span, ok, reason, warn, img}])"""
    br = bright_ratio(img)
    kind = "A" if br >= BG_BRIGHT_RATIO else "B"
    spans = split_a(img) if kind == "A" else []
    if kind == "A" and not spans:
        kind = "A→B폴백"
    if not spans:
        spans = split_b(img)
    res = []
    for t, b in spans:
        e = {"span": [t, b], "ok": False, "reason": "", "warn": [], "img": None}
        res.append(e)
        if b - t < MIN_SECTION_H:
            e["reason"] = f"너무 짧음 ({b - t}px)"
            continue
        c, how = fit_section(img.crop((0, t, img.width, b)))
        e["reason"] = how
        if c is None:
            continue
        r = c.width / c.height
        if r > RATIO_HARD_MAX:
            e["reason"] = f"비율 {r:.2f} > {RATIO_HARD_MAX} (조각)"
            continue
        if r > RATIO_MAX:
            e["warn"].append(f"비율 {r:.2f} > {RATIO_MAX} (contain이라 PNG는 안 잘림, 영상 좌우 크롭/텍스트 겹침 주의)")
        e["ok"], e["img"] = True, c
    return kind, br, res


def stitch(paths: list[Path]) -> Image.Image:
    ims = [Image.open(p).convert("RGB") for p in paths]
    w = ims[0].width
    ims = [i if i.width == w else i.resize((w, round(i.height * w / i.width)), Image.LANCZOS) for i in ims]
    out = Image.new("RGB", (w, sum(i.height for i in ims)), (255, 255, 255))
    y = 0
    for i in ims:
        out.paste(i, (0, y))
        y += i.height
    return out


# ── 실행 ────────────────────────────────────────────────────────────────────
def _files(d: str | None) -> list[Path]:
    return sorted(p for p in Path(d).iterdir() if p.suffix.lower() in IMG_EXT) if d else []


CELL, LABEL_H, COLS, PAD = 240, 44, 5, 10         # 컨택트시트: 썸네일 칸/라벨 높이/열 수/여백
WARN_RED = (220, 40, 40)


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("malgun.ttf", size)
    except OSError:                                    # ponytail: Windows 외엔 한글 경고 사유가 깨짐 (빨간 테두리+[!]는 유지). 필요하면 폰트 경로 추가
        return ImageFont.load_default()


def contact_sheet(cand: Path, pool: list[str], warns: dict[str, list[str]], path: Path) -> None:
    """후보 전체를 한 장에. 라벨=파일명(순번 포함), 경고 후보는 빨간 테두리 + 경고 첫 문구."""
    rows = -(-len(pool) // COLS)
    sheet = Image.new("RGB", (COLS * (CELL + PAD) + PAD, rows * (CELL + LABEL_H + PAD) + PAD), "white")
    d, f = ImageDraw.Draw(sheet), _font(14)
    for i, name in enumerate(pool):
        x, y = PAD + i % COLS * (CELL + PAD), PAD + i // COLS * (CELL + LABEL_H + PAD)
        t = Image.open(cand / name).convert("RGB")
        t.thumbnail((CELL, CELL))
        d.rectangle([x, y, x + CELL - 1, y + CELL - 1], fill=(235, 235, 235))
        sheet.paste(t, (x + (CELL - t.width) // 2, y + (CELL - t.height) // 2))
        d.text((x, y + CELL + 4), name, fill="black", font=f)
        if warns.get(name):
            d.rectangle([x, y, x + CELL - 1, y + CELL - 1], outline=WARN_RED, width=3)
            d.text((x, y + CELL + 22), "[!] " + warns[name][0].split(" (")[0], fill=WARN_RED, font=f)
    sheet.save(path)


def run(main_dir: str | None, desc_dir: str | None, out: Path) -> None:
    cand = out / "candidates"
    cand.mkdir(parents=True, exist_ok=True)
    if any(cand.iterdir()):
        print("경고: candidates/에 기존 파일이 있음 — 새 순번 파일과 섞임. 비우고 다시 돌릴 것")
    report = {"main": [], "desc": {}, "pool": []}
    warns = {}

    # 풀 파일명 = "NN_" 순번 + 원래 이름. 두 자리 고정 (ponytail: 99장 초과면 정렬 깨짐, 후보 풀은 십수 장 수준)
    mains = _files(main_dir)
    for p, (name, ok, why) in zip(mains, classify_main([(p.name, Image.open(p).convert("RGB")) for p in mains])):
        report["main"].append({"file": name, "ok": ok, "reason": why})
        if ok:
            pn = f"{len(report['pool']) + 1:02d}_main_{name}"
            shutil.copy2(p, cand / pn)
            report["pool"].append(pn)

    descs = _files(desc_dir)
    if descs:
        img = stitch(descs)
        kind, br, entries = desc_candidates(img)
        report["desc"] = {"files": [p.name for p in descs], "size": list(img.size), "type": kind,
                          "bright_ratio": round(br, 3), "sections": []}
        n = 0
        for e in entries:
            row = {k: e[k] for k in ("span", "ok", "reason", "warn")}
            if e["ok"]:
                n += 1
                row["file"] = f"{len(report['pool']) + 1:02d}_desc_{n:02d}.png"
                e["img"].save(cand / row["file"])
                report["pool"].append(row["file"])
                if row["warn"]:
                    warns[row["file"]] = row["warn"]
            report["desc"]["sections"].append(row)

    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    contact_sheet(cand, report["pool"], warns, out / "contact_sheet.png")
    print(f"메인 {sum(m['ok'] for m in report['main'])}/{len(report['main'])} 통과")
    if report["desc"]:
        d = report["desc"]
        print(f"설명 타입={d['type']} (밝은픽셀 {d['bright_ratio']:.1%}, 임계 {BG_BRIGHT_RATIO:.0%}) "
              f"섹션 {sum(s['ok'] for s in d['sections'])}/{len(d['sections'])} 통과")
        for s in d["sections"]:
            print(f"  {s['span']} {'OK ' if s['ok'] else 'X  '}{s['reason']} {' | '.join(s['warn'])}")
    print(f"후보 풀 {len(report['pool'])}장 -> {cand}\nreport -> {out / 'report.json'}\n컨택트시트 -> {out / 'contact_sheet.png'}")


def _band(img: Image.Image, y0: int, y1: int) -> None:
    d = ImageDraw.Draw(img)
    for x in range(0, img.width, 6):                   # 세로 줄무늬 = 텍스트 흉내
        d.rectangle([x, y0, x + 2, y1 - 1], fill=(0, 0, 0))


def selftest() -> None:
    # A: 흰 배경 + 블록 3개(사이 여백 100px+)
    a = Image.new("RGB", (780, 2000), "white")
    d = ImageDraw.Draw(a)
    for t, b in ((20, 500), (620, 1200), (1320, 1980)):
        d.rectangle([100, t, 680, b], fill=(100, 150, 200))
    kind, _, res = desc_candidates(a)
    assert kind == "A" and len(res) == 3 and all(e["ok"] for e in res), (kind, res)
    assert [bool(e["warn"]) for e in res] == [True, True, False]   # 780x500(1.56), 780x580(1.34) 경고, 780x680 정상

    # B: 색 띠 3개(흰 여백 없음)
    b = Image.new("RGB", (780, 1200))
    d = ImageDraw.Draw(b)
    for i, c in enumerate(((200, 60, 60), (60, 200, 60), (60, 60, 200))):
        d.rectangle([0, i * 400, 780, i * 400 + 399], fill=c)
    kind, _, res = desc_candidates(b)
    assert kind == "B" and len(res) == 3, (kind, res)
    assert abs(res[0]["span"][1] - 400) <= COLOR_JUMP_WIN and abs(res[1]["span"][1] - 800) <= COLOR_JUMP_WIN
    b2 = Image.new("RGB", (780, 900))                  # 300px 띠 3개 -> 비율 2.6 > 2.0 -> 전부 배제
    d = ImageDraw.Draw(b2)
    for i, c in enumerate(((200, 60, 60), (60, 200, 60), (60, 60, 200))):
        d.rectangle([0, i * 300, 780, i * 300 + 299], fill=c)
    r2 = desc_candidates(b2)[2]
    assert len(r2) == 3 and not any(e["ok"] for e in r2), r2

    # 텍스트 밴드: 가장자리 밴드 -> 세로크롭, 전면 텍스트 -> 배제, 창 한복판 밴드 -> 밴드를 피하는 창 or 배제
    s = Image.new("RGB", (780, 800), "white")
    _band(s, 20, 80)
    c, how = fit_section(s)
    assert c is not None and how.startswith("세로크롭"), how
    s2 = Image.new("RGB", (780, 800), "white")
    _band(s2, 0, 800)
    assert fit_section(s2)[0] is None
    s3 = Image.new("RGB", (780, 1200), "white")        # 한복판 밴드(존 밖) — 예전 상/하단 존 검사는 통과시켰던 케이스
    _band(s3, 520, 600)
    assert fit_section(s3)[0] is None
    s4 = Image.new("RGB", (780, 2400), "white")        # 긴 섹션 한복판 밴드 -> 밴드를 피해 위쪽 창을 고름
    _band(s4, 1200, 1280)
    c, _ = fit_section(s4)
    assert c is not None and not any(text_rows(c, DESC_EDGE_DIFF, DESC_ROW_DENSITY))
    assert fit_section(Image.new("RGB", (780, 3000), "white"))[0].height == 975   # 통이미지 -> 4:5 윈도우

    # 메인: 텍스트카드 배제 + 중복 제거
    plain = Image.new("RGB", (492, 492), "white")
    ImageDraw.Draw(plain).rectangle([150, 150, 350, 350], fill=(120, 120, 120))
    other = Image.new("RGB", (492, 492), "white")
    ImageDraw.Draw(other).rectangle([30, 150, 230, 350], fill=(120, 120, 120))
    card = Image.new("RGB", (492, 492), "white")
    _band(card, 100, 400)
    r = classify_main([("plain", plain), ("card", card), ("dup", plain.resize((300, 300))), ("other", other)])
    assert [x[1] for x in r] == [True, False, False, True], r

    # run(): 순번 프리픽스(파일명 정렬 = 풀 순서) + 컨택트시트(candidates/ 밖) + 경고 후보 빨간 테두리
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "m").mkdir()
        (t / "d").mkdir()
        plain.save(t / "m" / "main_01.png")
        other.save(t / "m" / "main_02.png")
        a.save(t / "d" / "desc_01.png")                # A형 3섹션 중 앞 2개가 비율 경고
        with redirect_stdout(io.StringIO()):
            run(str(t / "m"), str(t / "d"), t / "out")
        rep = json.loads((t / "out" / "report.json").read_text(encoding="utf-8"))
        names = sorted(p.name for p in (t / "out" / "candidates").iterdir())
        assert names == rep["pool"] and len(names) == 5, names
        assert [n[:3] for n in names] == ["01_", "02_", "03_", "04_", "05_"], names
        assert (t / "out" / "contact_sheet.png").exists() and "contact_sheet.png" not in names
        assert WARN_RED in {c for _, c in Image.open(t / "out" / "contact_sheet.png").getcolors(1 << 24)}
    print("selftest OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", help="메인 이미지 폴더")
    ap.add_argument("--desc", help="설명 이미지 폴더 (파일명 순으로 세로 이어붙임)")
    ap.add_argument("--out", default="step1_out", help="출력 폴더")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.main or a.desc:
        run(a.main, a.desc, Path(a.out))
    else:
        ap.error("--main/--desc 또는 --selftest 필요")
