# CHANGELOG — 생활꿀템연구소 콘텐츠 자동화

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
