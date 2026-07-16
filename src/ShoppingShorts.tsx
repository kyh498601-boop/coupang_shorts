import React from "react";
import {
  AbsoluteFill,
  Img,
  Sequence,
  interpolate,
  useCurrentFrame,
  staticFile,
  Easing,
} from "remotion";
import { loadFont } from "@remotion/google-fonts/NotoSansKR";

const { fontFamily } = loadFont();

export const FPS = 30;
export const SLIDE_COUNT = 7; // 2026-07-06: 10장 → 7장 구조로 재작성 (지침 STEP5 참조)
export const SLIDE_DURATION_FRAMES = 90; // 3s × 30fps (7장×3초=21초 목표)
const TRANSITION_FRAMES = 20;
const BRAND_COLOR = "#2E8B57";

// ── Ken Burns 패턴 4가지 (slideIndex % 4 로 반복) ──────────────────────────
// 패턴별 [scaleFrom, scaleTo, panXFrom, panXTo, panYFrom, panYTo]
// scale 최솟값 1.05 이상 유지 — objectFit:"cover"로 이미 프레임을 여백 없이 딱 채운
// 상태에서 translate(%)는 이미지 자기 자신 크기 기준이라, scale=1.0에 pan≠0이 겹치면
// 가장자리에 검은 배경이 비치는 버그가 있었다(2026-07-XX 진단). 안전 조건은
// scale ≥ 50/(50-|pan|).
//
// 2026-07-16: server.py의 _full_bleed_photo_bg가 "블러 배경(cover-crop) + 원본
// 전경(contain, 잘리지 않음)" 2계층 구조로 바뀌면서, 이 PNG 전체에 걸던 기존 줌 폭
// (1.05~1.18, 스윕 0.13)이 여전히 너무 커서 contain된 제품 실루엣 가장자리가 화면
// 밖으로 밀려나는 문제가 남아있었다(필립스 면도기 테스트에서 확인). 줌 스윕을 절반
// 수준(0.06)으로, 패닝 폭도 ±2%→±1%(대각선은 ±1.5%→±0.75%)로 줄여 제품이 재생 내내
// 화면 안에 안정적으로 머물도록 했다. 안전 조건 재계산: 최대 pan(±1%) 기준
// scale_min ≥ 50/(50-1) ≈ 1.0204 → 여유버퍼를 두고 1.03으로 설정.
// 패턴 3(중앙 고정)은 pan이 항상 0이라 원래도 안전 — 줌 폭만 축소해 유지.
const KB_PATTERNS: [number, number, number, number, number, number][] = [
  [1.03, 1.09,  -1,  1,   0,   0],  // 0: 줌인  + 좌→우 패닝
  [1.09, 1.03,   1, -1,   0,   0],  // 1: 줌아웃 + 우→좌 패닝
  [1.03, 1.09,  -1,  1, -0.75, 0.75], // 2: 줌인  + 대각선 (좌상→우하)
  [1.06, 1.0,   0,  0,   0,   0],  // 3: 줌아웃 + 중앙 고정
];

interface SlideProps {
  src: string;
  slideIndex: number;
  slideDuration: number;
}

const KenBurnsSlide: React.FC<SlideProps> = ({ src, slideIndex, slideDuration }) => {
  const frame = useCurrentFrame();
  const [sFrom, sTo, pxFrom, pxTo, pyFrom, pyTo] = KB_PATTERNS[slideIndex % 4];

  const ease = Easing.bezier(0.25, 0.46, 0.45, 0.94);

  const scale = interpolate(frame, [0, slideDuration], [sFrom, sTo], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease,
  });
  const panX = interpolate(frame, [0, slideDuration], [pxFrom, pxTo], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease,
  });
  const panY = interpolate(frame, [0, slideDuration], [pyFrom, pyTo], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease,
  });

  return (
    <AbsoluteFill style={{ overflow: "hidden", backgroundColor: "#000" }}>
      <Img
        src={staticFile(src)}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${scale}) translate(${panX}%, ${panY}%)`,
          transformOrigin: "center center",
        }}
      />
    </AbsoluteFill>
  );
};

// ── 전환 효과 3가지 (slideIndex % 3 로 번갈아) ────────────────────────────
type TransitionType = "fade" | "zoom" | "slide";

interface TransitionOverlayProps {
  progress: number;
  type: TransitionType;
}

const TransitionOverlay: React.FC<TransitionOverlayProps> = ({ progress, type }) => {
  if (type === "fade") {
    // 블랙 페이드
    const opacity = interpolate(progress, [0, 0.5, 1], [0, 1, 0], {
      easing: Easing.ease,
      extrapolateLeft: "clamp", extrapolateRight: "clamp",
    });
    return <AbsoluteFill style={{ backgroundColor: "#000", opacity }} />;
  }

  if (type === "zoom") {
    // 화이트 줌 플래시
    const scale = interpolate(progress, [0, 0.5, 1], [1, 1.08, 1], {
      easing: Easing.bezier(0.25, 0.46, 0.45, 0.94),
      extrapolateLeft: "clamp", extrapolateRight: "clamp",
    });
    const opacity = interpolate(progress, [0, 0.25, 0.75, 1], [0, 0.7, 0.7, 0], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp",
    });
    return (
      <AbsoluteFill
        style={{ backgroundColor: "#fff", opacity, transform: `scale(${scale})` }}
      />
    );
  }

  // slide — 위에서 아래로 검정 커튼이 내려왔다 올라감
  const translateY = interpolate(progress, [0, 0.5, 1], [-100, 0, 100], {
    easing: Easing.bezier(0.4, 0, 0.2, 1),
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#111",
        transform: `translateY(${translateY}%)`,
      }}
    />
  );
};

// ── 브랜드 바 ─────────────────────────────────────────────────────────────
const BrandBar: React.FC = () => {
  const frame = useCurrentFrame();
  const slideInY = interpolate(frame, [0, 20], [120, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  return (
    <AbsoluteFill
      style={{
        top: "auto", bottom: 0, height: 110,
        backgroundColor: BRAND_COLOR,
        display: "flex", alignItems: "center", justifyContent: "center",
        transform: `translateY(${slideInY}px)`,
      }}
    >
      <span
        style={{
          fontFamily, fontSize: 38, fontWeight: 700,
          color: "#fff", letterSpacing: 2,
          textShadow: "0 2px 8px rgba(0,0,0,0.3)",
        }}
      >
        🛒 오늘의 생활꿀템 추천
      </span>
    </AbsoluteFill>
  );
};

// ── 워터마크 ──────────────────────────────────────────────────────────────
const Watermark: React.FC = () => (
  <AbsoluteFill
    style={{
      top: 36, right: 36, left: "auto", bottom: "auto",
      width: "auto", height: "auto",
      display: "flex", alignItems: "center",
    }}
  >
    <div
      style={{
        backgroundColor: "rgba(46,139,87,0.85)",
        borderRadius: 24, padding: "10px 22px",
        display: "flex", alignItems: "center", gap: 8,
      }}
    >
      <span style={{ fontSize: 22 }}>🍀</span>
      <span
        style={{
          fontFamily, fontSize: 22, fontWeight: 700,
          color: "#fff", letterSpacing: 1, whiteSpace: "nowrap",
        }}
      >
        생활꿀템연구소
      </span>
    </div>
  </AbsoluteFill>
);

// ── 자막(나레이션) 캡션 ────────────────────────────────────────────────────
// 배경 박스 없이 흰 글씨 + 검정 외곽선(stroke) 스타일. 가격·할인 등 핵심 키워드는 색상 강조.
const CAPTION_HIGHLIGHT_COLOR = "#FFD24D"; // 노란색 (브랜드 컬러 #2E8B57로 바꾸려면 이 값만 교체)
const CAPTION_HIGHLIGHT_PATTERN =
  /(\d+[%원]|\d+[,.]?\d*\s?(만원|천원)|특가|할인|무료배송|최저가|쿠폰|역대급|1\+1|오늘만|품절임박)/g;

function splitCaptionTokens(text: string): { text: string; highlight: boolean }[] {
  const tokens: { text: string; highlight: boolean }[] = [];
  let lastIndex = 0;
  for (const match of text.matchAll(CAPTION_HIGHLIGHT_PATTERN)) {
    const idx = match.index ?? 0;
    if (idx > lastIndex) tokens.push({ text: text.slice(lastIndex, idx), highlight: false });
    tokens.push({ text: match[0], highlight: true });
    lastIndex = idx + match[0].length;
  }
  if (lastIndex < text.length) tokens.push({ text: text.slice(lastIndex), highlight: false });
  return tokens;
}

// 자막 박스 폭(AbsoluteFill left/right:32 기준) 및 autofit 폰트 범위.
// server.py의 _tw_auto_font와 동일 패턴 — 캔버스로 실측해 최대 2줄 안에 들어올 때까지
// 폰트를 44px→34px로 축소한다. PNG 쪽 제품명(BOTTOM_TEXT_Y=1060)과의 버퍼를 지키기 위함
// (2026-07-14 진단: 나레이션이 배속 보정으로 길어지면 44px 고정 자막이 3줄까지 넘어가
// PNG에 구운 제품명 텍스트와 겹치는 버그가 있었다).
const CAPTION_BOX_WIDTH = 1080 - 32 * 2 - 8; // 좌우 32px 마진 + 외곽선(stroke) 여유버퍼 8px
const CAPTION_FONT_MAX = 44;
const CAPTION_FONT_MIN = 34;
const CAPTION_MAX_LINES = 2;

let _measureCtx: CanvasRenderingContext2D | null = null;
function getMeasureCtx(): CanvasRenderingContext2D {
  if (!_measureCtx) {
    _measureCtx = document.createElement("canvas").getContext("2d")!;
  }
  return _measureCtx;
}

// 단어 단위 줄바꿈 실측 (server.py _tw_wrap과 동일 패턴)
function wrapByWidth(ctx: CanvasRenderingContext2D, text: string, fontPx: number, maxWidth: number): string[] {
  ctx.font = `800 ${fontPx}px ${fontFamily}`;
  const words = text.split(" ");
  const lines: string[] = [];
  let line = "";
  for (const w of words) {
    const test = line ? `${line} ${w}` : w;
    if (ctx.measureText(test).width <= maxWidth) {
      line = test;
    } else {
      if (line) lines.push(line);
      line = w;
    }
  }
  if (line) lines.push(line);
  return lines.length ? lines : [text];
}

// 텍스트 길이에 맞는 자막 폰트 크기 결정. size_max→size_min 순으로 줄여가며
// CAPTION_MAX_LINES(2줄) 이내로 들어오는 크기를 찾고, 최소 크기로도 안 들어오면
// (텍스트를 자르지 않고 — TTS/자막 1:1 원칙 유지) 최소 크기 그대로 3줄 이상 허용한다.
function autofitCaptionFontSize(text: string): number {
  const ctx = getMeasureCtx();
  for (let size = CAPTION_FONT_MAX; size >= CAPTION_FONT_MIN; size -= 2) {
    const lines = wrapByWidth(ctx, text, size, CAPTION_BOX_WIDTH);
    if (lines.length <= CAPTION_MAX_LINES) return size;
  }
  return CAPTION_FONT_MIN;
}

const Caption: React.FC<{ text: string }> = ({ text }) => {
  const fontSize = React.useMemo(() => autofitCaptionFontSize(text || ""), [text]);
  if (!text) return null;
  const tokens = splitCaptionTokens(text);

  return (
    <AbsoluteFill
      style={{
        top: "auto", bottom: 150, left: 32, right: 32, height: "auto",
        display: "flex", justifyContent: "center", alignItems: "flex-end",
      }}
    >
      <p
        style={{
          margin: 0,
          width: "100%",
          maxWidth: "100%",
          minWidth: 0,
          boxSizing: "border-box",
          fontFamily,
          fontWeight: 800,
          fontSize,
          lineHeight: 1.32,
          textAlign: "center",
          color: "#fff",
          WebkitTextStroke: "3.5px #000",
          paintOrder: "stroke fill",
          textShadow: "0 2px 6px rgba(0,0,0,0.45)",
          wordBreak: "keep-all",       // 한글 단어 중간에서 끊기지 않도록
          overflowWrap: "break-word",  // 그래도 넘치면 강제 줄바꿈 (잘림 방지)
          whiteSpace: "normal",
        }}
      >
        {tokens.map((tok, i) => (
          <span key={i} style={tok.highlight ? { color: CAPTION_HIGHLIGHT_COLOR } : undefined}>
            {tok.text}
          </span>
        ))}
      </p>
    </AbsoluteFill>
  );
};

// ── CTA 링크 안내 오버레이 (9~10번 슬라이드 전용) ──────────────────────────
// 대시보드 STEP1에서 선택한 플랫폼값에 따라 문구를 분기 — 단일 플랫폼일 때만 구체적으로
// 표시하고, 복수 선택/"전체"/미지정이면 범용 문구로 폴백한다 (server.py handle_render_video 참고).
const CTA_LINK_TEXT_MAP: Record<string, string> = {
  "유튜브":   "🔗 설명란 링크 확인",
  "youtube":  "🔗 설명란 링크 확인",
  "페이스북": "🔗 게시물 캡션 링크 확인",
  "facebook": "🔗 게시물 캡션 링크 확인",
  "인스타":   "🔗 프로필 링크 확인",
  "instagram": "🔗 프로필 링크 확인",
  "틱톡":     "🔗 프로필 링크 확인",
  "tiktok":   "🔗 프로필 링크 확인",
};
const CTA_LINK_TEXT_DEFAULT = "🔗 프로필/설명란 링크 확인";

const CtaLinkOverlay: React.FC<{ platform?: string }> = ({ platform }) => {
  const frame = useCurrentFrame();
  const normalizedPlatform = platform?.toLowerCase();
  const text =
    (platform && CTA_LINK_TEXT_MAP[platform]) ||
    (normalizedPlatform && CTA_LINK_TEXT_MAP[normalizedPlatform]) ||
    CTA_LINK_TEXT_DEFAULT;

  // 0~15프레임: fade-in / 이후: 깜빡임(sine)으로 시선 유도 — 기존 카피(Caption, bottom:150)와
  // BrandBar(하단 110px)를 침범하지 않도록 하단 텍스트 카드 영역(513px) 상단부에 배치
  const fadeIn = interpolate(frame, [0, 15], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.ease,
  });
  const blink = 0.55 + 0.45 * Math.abs(Math.sin((Math.max(frame - 15, 0) / 15) * Math.PI));
  const opacity = frame < 15 ? fadeIn : blink;

  return (
    <AbsoluteFill
      style={{
        top: "auto", bottom: 490, left: 32, right: 32, height: "auto",
        display: "flex", justifyContent: "center", alignItems: "center",
      }}
    >
      <div
        style={{
          opacity,
          backgroundColor: "rgba(46,139,87,0.92)",
          borderRadius: 999,
          padding: "12px 28px",
          boxShadow: "0 4px 14px rgba(0,0,0,0.35)",
        }}
      >
        <span
          style={{
            fontFamily, fontSize: 32, fontWeight: 700,
            color: "#fff", whiteSpace: "nowrap",
          }}
        >
          {text}
        </span>
      </div>
    </AbsoluteFill>
  );
};

// ── 메인 컴포지션 ─────────────────────────────────────────────────────────
export interface ShoppingProps {
  images?: string[];
  durationPerSlideFrames?: number;
  captions?: string[];  // 슬라이드별 나레이션 자막 (없으면 자막 미표시)
  durationPerSlideFramesArr?: number[];  // 슬라이드별 실제 음성 길이 비례 분배 (있으면 균등분배 대신 사용)
  platform?: string;  // CTA 슬라이드(9~10번) 링크 안내 문구 분기용 (예: "유튜브", "페이스북")
}

const TRANSITION_TYPES: TransitionType[] = ["fade", "zoom", "slide"];

export const ShoppingShorts: React.FC<ShoppingProps> = ({
  images = [],
  durationPerSlideFrames = SLIDE_DURATION_FRAMES,
  captions = [],
  durationPerSlideFramesArr,
  platform,
}) => {
  const frame = useCurrentFrame();

  // durationPerSlideFramesArr(실제 음성 길이 비례)가 있고 슬라이드 수와 맞으면 그걸 쓰고,
  // 없으면 기존처럼 균등분배(durationPerSlideFrames)로 폴백 — 기존 동작 100% 유지.
  const useVariableTiming =
    !!durationPerSlideFramesArr && durationPerSlideFramesArr.length === images.length;
  const slideDurations = useVariableTiming
    ? durationPerSlideFramesArr!
    : images.map(() => durationPerSlideFrames);
  const slideStarts = slideDurations.reduce<number[]>((acc, d, i) => {
    acc.push(i === 0 ? 0 : acc[i - 1] + slideDurations[i - 1]);
    return acc;
  }, []);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {images.map((src, i) => {
        const slideDur = slideDurations[i];
        const slideStart = slideStarts[i];
        const slideEnd = slideStart + slideDur;
        // 슬라이드가 짧아도 전환 효과가 슬라이드 시작 이전으로 넘어가지 않도록 클램프
        const transitionStart = Math.max(slideStart, slideEnd - TRANSITION_FRAMES);
        const transitionType = TRANSITION_TYPES[i % 3];

        return (
          <React.Fragment key={src + i}>
            {/* Ken Burns 슬라이드 */}
            <Sequence from={slideStart} durationInFrames={slideDur}>
              <KenBurnsSlide src={src} slideIndex={i} slideDuration={slideDur} />
            </Sequence>

            {/* 슬라이드 진행 배지("1/7" 등)는 2026-07-06 완전 삭제됨 (PNG 생성 로직과 동일하게 제거) */}

            {/* 자막 (나레이션 텍스트) — 화면 중하단, BrandBar 위 빈 공간 */}
            {captions[i] && (
              <Sequence from={slideStart} durationInFrames={slideDur}>
                <Caption text={captions[i]} />
              </Sequence>
            )}

            {/* CTA 링크 안내 오버레이 — 9~10번 슬라이드(마지막 2장)에만 표시 */}
            {i >= images.length - 2 && (
              <Sequence from={slideStart} durationInFrames={slideDur}>
                <CtaLinkOverlay platform={platform} />
              </Sequence>
            )}

            {/* 전환 효과 */}
            {i < images.length - 1 && (
              <Sequence from={transitionStart} durationInFrames={TRANSITION_FRAMES}>
                <TransitionOverlay
                  progress={interpolate(
                    frame - transitionStart,
                    [0, TRANSITION_FRAMES],
                    [0, 1],
                    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
                  )}
                  type={transitionType}
                />
              </Sequence>
            )}
          </React.Fragment>
        );
      })}

      <Watermark />
      <BrandBar />
    </AbsoluteFill>
  );
};
