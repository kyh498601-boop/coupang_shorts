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
export const SLIDE_COUNT = 10;
export const SLIDE_DURATION_FRAMES = 75; // 2.5s × 30fps
const TRANSITION_FRAMES = 20;
const BRAND_COLOR = "#2E8B57";

// ── Ken Burns 패턴 4가지 (slideIndex % 4 로 반복) ──────────────────────────
// 패턴별 [scaleFrom, scaleTo, panXFrom, panXTo, panYFrom, panYTo]
const KB_PATTERNS: [number, number, number, number, number, number][] = [
  [1.0, 1.13,  -2,  2,   0,   0],  // 0: 줌인  + 좌→우 패닝
  [1.13, 1.0,   2, -2,   0,   0],  // 1: 줌아웃 + 우→좌 패닝
  [1.0, 1.13,  -2,  2,  -1.5, 1.5], // 2: 줌인  + 대각선 (좌상→우하)
  [1.13, 1.0,   0,  0,   0,   0],  // 3: 줌아웃 + 중앙 고정
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

// ── 메인 컴포지션 ─────────────────────────────────────────────────────────
export interface ShoppingProps {
  images?: string[];
  durationPerSlideFrames?: number;
}

const TRANSITION_TYPES: TransitionType[] = ["fade", "zoom", "slide"];

export const ShoppingShorts: React.FC<ShoppingProps> = ({
  images = [],
  durationPerSlideFrames = SLIDE_DURATION_FRAMES,
}) => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {images.map((src, i) => {
        const slideStart = i * durationPerSlideFrames;
        const slideEnd = slideStart + durationPerSlideFrames;
        const transitionStart = slideEnd - TRANSITION_FRAMES;
        const transitionType = TRANSITION_TYPES[i % 3];

        return (
          <React.Fragment key={src + i}>
            {/* Ken Burns 슬라이드 */}
            <Sequence from={slideStart} durationInFrames={durationPerSlideFrames}>
              <KenBurnsSlide src={src} slideIndex={i} slideDuration={durationPerSlideFrames} />
            </Sequence>

            {/* 슬라이드 번호 배지 */}
            <Sequence from={slideStart} durationInFrames={durationPerSlideFrames}>
              <AbsoluteFill
                style={{ top: 48, left: 40, right: "auto", bottom: "auto", width: "auto", height: "auto" }}
              >
                <div
                  style={{
                    backgroundColor: "rgba(0,0,0,0.5)",
                    borderRadius: 20, padding: "8px 18px",
                    display: "flex", alignItems: "center",
                  }}
                >
                  <span style={{ fontFamily, fontSize: 24, fontWeight: 600, color: "#fff" }}>
                    {i + 1} / {images.length}
                  </span>
                </div>
              </AbsoluteFill>
            </Sequence>

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
