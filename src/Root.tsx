import "./index.css";
import { Composition } from "remotion";
import {
  ShoppingShorts,
  ShoppingProps,
  SLIDE_COUNT,
  SLIDE_DURATION_FRAMES,
  FPS,
} from "./ShoppingShorts";

export const RemotionRoot: React.FC = () => {
  const defaultImages = Array.from(
    { length: SLIDE_COUNT },
    (_, i) => `input/slide${String(i + 1).padStart(2, "0")}.png`
  );

  return (
    <>
      <Composition
        id="ShoppingShorts"
        component={ShoppingShorts}
        // calculateMetadata: --props 로 durationPerSlideFrames 전달 시 총 길이 자동 계산
        calculateMetadata={async ({ props }: { props: ShoppingProps }) => ({
          durationInFrames:
            (props.images?.length ?? SLIDE_COUNT) *
            (props.durationPerSlideFrames ?? SLIDE_DURATION_FRAMES),
        })}
        durationInFrames={SLIDE_COUNT * SLIDE_DURATION_FRAMES}
        fps={FPS}
        width={1080}
        height={1920}
        defaultProps={{
          images: defaultImages,
          durationPerSlideFrames: SLIDE_DURATION_FRAMES,
        }}
      />
    </>
  );
};
