"""상세페이지 원본(780xN)을 흰 여백 기준으로 섹션별 자동 분할.

사용법: python split_detail_page.py <원본.png> [출력폴더]
"""
import sys
from pathlib import Path

from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BRIGHTNESS_THRESHOLD = 245   # 이 값 이상 평균 밝기면 "밝은 행"(여백 후보)으로 판정
MIN_GAP_HEIGHT = 80          # 이만큼 연속으로 밝은 행이 이어져야 여백 구간으로 인정
                              # (2026-09-18: 실측 결과 장식용 여백은 ~55px 이하, 진짜 섹션 경계는
                              # 90px 이상으로 뚜렷이 갈림 — 15px는 장식 여백까지 경계로 오인해 과분할됨)
MIN_SECTION_HEIGHT = 50      # 이보다 짧은 섹션은 이상 구간
MAX_SECTION_HEIGHT = 3000    # 이보다 긴 섹션은 이상 구간
MIN_GAPS_REQUIRED = 2        # 감지된 여백 구간이 이보다 적으면 분할 실패로 판단


def row_brightness(img: Image.Image) -> list[float]:
    gray = img.convert("L")
    w, h = gray.size
    px = gray.load()
    result = []
    for y in range(h):
        total = 0
        for x in range(w):
            total += px[x, y]
        result.append(total / w)
    return result


def find_gaps(brightness: list[float]) -> list[tuple[int, int]]:
    """연속으로 밝은 행 구간(시작, 끝) 목록. MIN_GAP_HEIGHT 미만인 건 제외."""
    gaps = []
    start = None
    for y, b in enumerate(brightness):
        bright = b >= BRIGHTNESS_THRESHOLD
        if bright and start is None:
            start = y
        elif not bright and start is not None:
            if y - start >= MIN_GAP_HEIGHT:
                gaps.append((start, y))
            start = None
    if start is not None and len(brightness) - start >= MIN_GAP_HEIGHT:
        gaps.append((start, len(brightness)))
    return gaps


def gaps_to_boundaries(gaps: list[tuple[int, int]], total_height: int) -> list[int]:
    """각 여백 구간의 중앙값을 분할 경계선으로 사용."""
    return [(g0 + g1) // 2 for g0, g1 in gaps]


def split_sections(image_path: Path, out_dir: Path) -> None:
    img = Image.open(image_path)
    brightness = row_brightness(img)
    gaps = find_gaps(brightness)

    if len(gaps) < MIN_GAPS_REQUIRED:
        print("자동분할 실패 — 수동 확인 필요")
        return

    boundaries = [0, *gaps_to_boundaries(gaps, img.height), img.height]

    out_dir.mkdir(parents=True, exist_ok=True)
    section_no = 0
    for i in range(len(boundaries) - 1):
        top, bottom = boundaries[i], boundaries[i + 1]
        height = bottom - top
        if height <= 0:
            continue

        section_no += 1
        flag = ""
        if height < MIN_SECTION_HEIGHT:
            flag = " [이상 구간: 너무 짧음]"
        elif height > MAX_SECTION_HEIGHT:
            flag = " [이상 구간: 너무 김]"

        out_path = out_dir / f"section_{section_no:02d}.png"
        img.crop((0, top, img.width, bottom)).save(out_path)

        gap_height = gaps[i][1] - gaps[i][0] if i < len(gaps) else 0
        print(f"{out_path.name}  ({height}px)  신뢰도(여백 높이)={gap_height}px{flag}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python split_detail_page.py <원본.png> [출력폴더]")
        sys.exit(1)

    src = Path(sys.argv[1])
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else src.parent / f"{src.stem}_sections"
    split_sections(src, dest)
