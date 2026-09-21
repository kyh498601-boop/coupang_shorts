"""split_detail_page.py가 뽑은 section_NN.png들을 텍스트 밀도로 재선별하고,
통과한 섹션만 1080x1350(4:5)로 크롭한다. PIL 사용.

사용법: python select_and_crop_sections.py <sections_폴더> [출력폴더]

판정 기준 (2026-09-18 실측 보정치):
- 행(row) 단위로 좌우 인접 픽셀 밝기차가 EDGE_DIFF_THRESH를 넘는 비율(엣지 밀도)을 구해
  ROW_TEXT_DENSITY_THRESH 이상이면 "텍스트 행" 후보로 본다.
- 짧은 끊김(FILL_GAP 이하)은 한 문단으로 메우고, 너무 짧은 덩어리(MIN_RUN 미만)는 노이즈로 버린다.
- 텍스트 행이 전체 세로의 TEXT_EXCLUDE_RATIO(30%) 이상이면 "배제".
- 통과한 섹션은 1080x1350 비율로 크롭하되, 최종 이미지의 상단 40%와 GRID_SAFE_BOTTOM(990px)
  아래 구간(제품명/자막이 나중에 얹히는 자리)에 원본 텍스트가 최대한 안 걸리는 위치를 찾는다.
"""
import sys
from pathlib import Path

from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

EDGE_DIFF_THRESH = 25
ROW_TEXT_DENSITY_THRESH = 0.015
FILL_GAP = 15
MIN_RUN = 5
TEXT_EXCLUDE_RATIO = 0.30

CROP_W, CROP_H = 1080, 1350
TOP_UNSAFE_FRAC = 0.40          # 상단 40% — 헤드라인이 얹히는 구간
GRID_SAFE_BOTTOM = 990          # 이 y좌표(1350 기준) 아래는 제품명/자막이 얹히는 구간
CANDIDATE_STEP = 10


def row_text_flags(img: Image.Image) -> list[bool]:
    """행별로 텍스트 행 여부(gap 메우기 + 짧은 런 제거까지 적용) 반환."""
    gray = img.convert("L")
    w, h = gray.size
    px = gray.load()

    density = []
    for y in range(h):
        prev = px[0, y]
        edges = 0
        for x in range(1, w):
            cur = px[x, y]
            if abs(cur - prev) > EDGE_DIFF_THRESH:
                edges += 1
            prev = cur
        density.append(edges / w)

    flags = [d >= ROW_TEXT_DENSITY_THRESH for d in density]

    i = 0
    while i < len(flags):
        if not flags[i]:
            j = i
            while j < len(flags) and not flags[j]:
                j += 1
            if 0 < i and j < len(flags) and j - i <= FILL_GAP:
                for k in range(i, j):
                    flags[k] = True
            i = j
        else:
            i += 1

    i = 0
    while i < len(flags):
        if flags[i]:
            j = i
            while j < len(flags) and flags[j]:
                j += 1
            if j - i < MIN_RUN:
                for k in range(i, j):
                    flags[k] = False
            i = j
        else:
            i += 1

    return flags


def best_crop_top(flags: list[bool], h: int, crop_h: int) -> tuple[int, int]:
    """danger_score(상단40%+하단GRID_SAFE_BOTTOM 밑 구간의 텍스트 행 개수)가
    가장 작은 top 오프셋을 찾는다. 반환: (top, danger_score)."""
    top_zone_h = int(crop_h * TOP_UNSAFE_FRAC)
    bottom_zone_start = int(crop_h * (GRID_SAFE_BOTTOM / CROP_H))

    max_top = h - crop_h
    best_top, best_score = 0, None
    center_default = max_top // 2

    for top in range(0, max_top + 1, CANDIDATE_STEP):
        score = sum(flags[top:top + top_zone_h]) + sum(flags[top + bottom_zone_start:top + crop_h])
        if best_score is None or score < best_score or (
            score == best_score and abs(top - center_default) < abs(best_top - center_default)
        ):
            best_top, best_score = top, score

    return best_top, best_score


def process(sections_dir: Path, out_dir: Path) -> None:
    files = sorted(sections_dir.glob("section_*.png"))
    if not files:
        print(f"'{sections_dir}'에서 section_*.png를 찾지 못했습니다.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    passed, excluded = [], []

    for f in files:
        img = Image.open(f).convert("RGB")
        w, h = img.size
        flags = row_text_flags(img)
        ratio = sum(flags) / h

        if ratio >= TEXT_EXCLUDE_RATIO:
            excluded.append((f.name, ratio))
            continue

        crop_h = round(w / (CROP_W / CROP_H))
        if crop_h >= h:
            top, note = 0, "섹션 높이 부족 — 전체 사용"
            crop_h = h
        else:
            top, danger_score = best_crop_top(flags, h, crop_h)
            note = f"위험구간 텍스트행={danger_score}px"

        cropped = img.crop((0, top, w, top + crop_h)).resize((CROP_W, CROP_H), Image.LANCZOS)
        out_path = out_dir / f"{f.stem}_crop.png"
        cropped.save(out_path)
        passed.append((f.name, out_path.name, ratio, note))

    print(f"=== 통과 ({len(passed)}개) ===")
    for src, out, ratio, note in passed:
        print(f"  {src} -> {out}  (텍스트비율={ratio:.1%}, {note})")

    print(f"\n=== 배제 ({len(excluded)}개) ===")
    for name, ratio in excluded:
        print(f"  {name}  사유: 문장형 텍스트가 세로 {ratio:.1%} 차지 (기준 {TEXT_EXCLUDE_RATIO:.0%} 이상)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python select_and_crop_sections.py <sections_폴더> [출력폴더]")
        sys.exit(1)

    src_dir = Path(sys.argv[1])
    dest_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else src_dir / "selected"
    process(src_dir, dest_dir)
