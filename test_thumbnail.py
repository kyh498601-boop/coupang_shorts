"""썸네일 테스트 — 업로드된 원본 제품 이미지로 A안/B안 생성."""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from PIL import Image
from server import _render_thumb_a, _render_thumb_b

BASE_DIR  = os.path.dirname(__file__)
OUT_A     = os.path.join(BASE_DIR, "thumbnail_test_a.png")
OUT_B     = os.path.join(BASE_DIR, "thumbnail_test_b.png")

# 이미지 소스 우선순위:
#   1. product_original.png — 대시보드에서 PNG 생성 시 자동 저장된 원본 제품 이미지
#   2. 명령줄 인수로 지정한 이미지 경로
#   3. 없으면 오류

def find_product_image() -> str:
    # 1) 명령줄 인수
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        return sys.argv[1]
    # 2) 대시보드가 저장한 원본 이미지
    orig = os.path.join(BASE_DIR, "product_original.png")
    if os.path.isfile(orig):
        return orig
    return ""

img_path = find_product_image()
if not img_path:
    print("[오류] 제품 원본 이미지를 찾을 수 없습니다.")
    print("  방법 1: 대시보드에서 PNG 생성을 먼저 실행 (product_original.png 자동 저장)")
    print("  방법 2: python test_thumbnail.py <이미지경로>")
    sys.exit(1)

print(f"사용 이미지: {img_path}")
product_img = Image.open(img_path)
print(f"이미지 크기: {product_img.size}  모드: {product_img.mode}")

hook_text = "오늘만 이 가격 놓치지 마세요"
sub_text  = "쿠팡 로켓배송 특가 상품"

print("\n── A안 생성 ──")
a_bytes = _render_thumb_a(product_img, hook_text, sub_text)
with open(OUT_A, "wb") as f:
    f.write(a_bytes)
print(f"저장: {OUT_A}  ({len(a_bytes)//1024}KB)")

print("\n── B안 생성 ──")
b_bytes = _render_thumb_b(product_img, hook_text, sub_text)
with open(OUT_B, "wb") as f:
    f.write(b_bytes)
print(f"저장: {OUT_B}  ({len(b_bytes)//1024}KB)")

print("\n완료.")
