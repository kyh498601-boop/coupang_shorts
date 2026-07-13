# CHANGELOG — 생활꿀템연구소 콘텐츠 자동화

## [1.6.0] — 2026-06-30

### TTS 엔진 교체 및 나레이션·자막 동기화 개선
- TTS 엔진을 Gemini → **Typecast API**로 전면 교체, `audio_tempo=1.0` 고정 (속도 보정은 자체 atempo 로직에서만 처리)
- 감정 톤을 본문 텍스트에서 분리해 `prompt.emotion_preset`(`toneup`) 파라미터로 전달 — 과거 `(힘있고 강조하며…)` 같은 지시문을 TTS가 그대로 읽어버리던 버그 수정
- 카테고리별(화장품/주방용품/생활가전/자동차용품/기타) 보이스 자동 매칭 추가, 드롭다운 수동 변경 시 자동 매칭 비활성화
- Typecast 한국어 보이스 24종으로 드롭다운 교체 (기존 Gemini 30종 제거)

### 나레이션 텍스트 — 트림 로직 완전 제거
- `build_narration()`의 문장 중간 절단(말줄임표 트림) 로직 삭제 → 항상 완성 문장만 생성
- 타이밍 보정은 텍스트 길이가 아닌 atempo 배속 보정으로만 전담
- "음/아/어" 필러 방어적 제거 정규식 추가 (실제 단어는 보존)

### 자막(captions.json / SRT) 클린업
- `_clean_caption_text()` 추가: 괄호 문자·말줄임표 제거, 음성용 원본 텍스트는 그대로 유지 (자막용/음성용 텍스트 분리)
- TTS가 실제로 사용한 최종 텍스트로 `captions.json`을 저장해 음성-자막 텍스트 불일치 해결

### 슬라이드별 가변 타이밍 (균등분배 폐지)
- `slide_durations.json` 추가: 실측 TTS 길이를 슬라이드 글자수 비례로 분배
- `render.ps1` / `Root.tsx` / `ShoppingShorts.tsx`가 가변 프레임 배열을 지원 (없으면 기존 균등분배로 폴백)
- `/generate-srt`도 `slide_durations.json` 재사용해 실제 음성 길이 기준 타임스탬프 생성

### PNG 슬라이드 디자인 개선
- `render_slide()`에서 본문(Body) 카피 텍스트 제거 — Remotion 자막 컴포넌트와 중복 표시되던 문제 수정
- 1번 슬라이드 전용 **히어로(썸네일 스타일) 레이아웃** 추가 — YouTube Shorts가 커스텀 썸네일 API를 지원하지 않는 정책 제약의 대안으로 채택. 브랜드 그린 패널 + 골드 뱃지 + 대형 외곽선 Hook 텍스트 (썸네일 A안 스타일 재사용)

### BGM 믹싱 — 더킹/볼륨 밸런스 재조정
- 나레이션 loudnorm(-16 LUFS) 정규화 추가
- ffmpeg `amix`의 숨은 `normalize=true`, `alimiter`의 숨은 `level=true` 기본값으로 인한 볼륨 손실 발견 및 비활성화
- `sidechaincompress` threshold 0.05→0.15, ratio 4→3으로 완화 (과도한 더킹으로 BGM이 거의 안 들리던 핵심 원인)
- BGM 베이스 볼륨 -22dB → -14dB로 단계적 상향

### 영상 품질
- Remotion 렌더에 `--video-bitrate=10M` 추가 (기존 3.75Mbps → 9.83Mbps, 유튜브 숏폼 권장 8~12Mbps 충족)

### YouTube 업로드 안정성
- OAuth `run_local_server`에 `timeout_seconds=180` 추가 — 인증 미완료 시 무한 대기 대신 명확한 타임아웃 에러
- 대시보드에 썸네일 미선택 시 업로드(STEP 7) 진입을 차단하는 가드(`enterUploadStep`) 추가

---

## [1.5.0] — 2026-06-29

### 보완작업 1~5 완료

#### 보완 1 · SRT 자막 자동 생성
- `POST /generate-srt` 엔드포인트 추가
- 슬라이드 10장 × 2.5초 기준 타임코드 자동 계산
- 구어체 나레이션 템플릿 10종 (후킹→CTA) 적용
- 이모지·URL·특수기호 자동 제거 (`_clean()`)
- 대시보드 "💬 자막 다운로드" 버튼 연결

#### 보완 2 · TTS 나레이션 자동 생성
- `POST /generate-tts` 엔드포인트 추가
- Gemini `gemini-2.5-flash-preview-tts` 모델 사용
- PCM → WAV 변환 (24000Hz · 16-bit · mono)
- 슬라이드별 감정 태그 자동 삽입 (강조·밝게·따뜻하게·긴박·강조)
- `atempo` ffmpeg 필터로 25~30초 목표 속도 자동 조정
- 30개 목소리 선택 UI · 미리듣기 · 재생성 · 다운로드 지원
- `output_narration.wav` 서버 자동 저장

#### 보완 3 · 유튜브 썸네일 자동 생성
- `POST /generate-thumbnail` 엔드포인트 추가
- PIL 기반 1280×720 PNG 렌더링 (A안·B안 동시 생성)
- A안: 좌우 스플릿 레이아웃 (제품이미지 + 브랜드 그린 텍스트)
- B안: 다크 그라디언트 배경 + 골드 후킹 텍스트
- `rembg` 배경 제거 자동 적용 (미설치 시 원본 폴백)
- Hook 문구 최대 5단어 자동 단축, 폰트 크기 자동 조절
- 썸네일 A/B 클릭 선택 + 개별 다운로드

#### 보완 4 · BGM 자동 추가
- `POST /generate-bgm` · `/change-bgm` · `GET /list-bgm` · `POST /upload-bgm` 추가
- `bgm/` 폴더 하위 전체 탐색, 랜덤 BGM 자동 선택
- ffmpeg `amix` 필터로 영상 + BGM + 나레이션 3트랙 믹싱
- 나레이션 있을 때: 나레이션 길이 기준 + 끝 1초 페이드아웃
- BGM 볼륨 슬라이더 (5~50%), 다른 BGM 재선택, 미리보기, 다운로드
- 드래그앤드롭 BGM 업로드 지원 (mp3/wav/m4a/ogg/flac)
- `output_with_bgm.mp4` 서버 자동 저장

#### 보완 5 · Google Drive 자동 업로드
- `POST /upload-drive` 엔드포인트 추가
- OAuth2 인증 (credentials.json → token.json 자동 저장, 만료 시 자동 갱신)
- 업로드 대상: `output_with_bgm.mp4` · `thumbnail_test_a.png` · `thumbnail_test_b.png`
- 실패 시 지수 백오프 3회 자동 재시도 (2s → 4s)
- `GOOGLE_DRIVE_FOLDER_ID=19LfVDYcUtR-NUD4oYmdtC0byZNqu463r` 설정 완료
- 대시보드 "☁️ Drive 저장" 버튼 실제 API 연결
- `.gitignore`에 `credentials.json` · `token.json` 추가

### 의존성 추가
- `google-auth` 2.55.1
- `google-auth-oauthlib` 1.4.0
- `google-api-python-client` 2.198.0

---

## [1.0.0] — 초기 릴리즈

- 대시보드 UI (5단계 파이프라인)
- 슬라이드 카피 자동 생성 (카테고리별 10종 템플릿)
- 캐러셀 PNG 10장 자동 생성 (1080×1350, PIL)
- Remotion MP4 렌더링 파이프라인
- 플랫폼별 캡션 · 해시태그 자동 생성
