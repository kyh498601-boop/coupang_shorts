# render.ps1 — input/ 폴더 PNG 7장 → output/shopping-shorts.mp4 자동 렌더
param(
  [string]$InputDir = ".\public\input",
  [string]$OutputFile = ".\output\shopping-shorts.mp4",
  [string]$Platform = ""  # CTA 슬라이드(9~10번) 링크 안내 문구 분기용 — 대시보드 STEP1 플랫폼 선택값
)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$env:PATH = "C:\Users\JOEUN\tools\node;$env:PATH"

# server.py의 SLIDE_TOTAL과 동일 (2026-07-06 10장 → 7장 재작성)
$SlideTotal = 7

# 1. input 폴더 PNG 확인
#    - slide_NN.png 이외 이름의 "새 원본" 파일이 섞여 있으면 그것만 사용 (이전 실행이 남긴
#      slide_NN.png 잔여물과의 자기복제 방지)
#    - slide_NN.png 뿐이면(= server.py generate-png 자동저장 결과물이거나, 사용자가 zip을
#      풀어 그대로 복사해 넣은 경우) 그것을 신규 원본으로 그대로 사용
$allPngs   = Get-ChildItem $InputDir -Filter "*.png" | Sort-Object Name
$freshPngs = $allPngs | Where-Object { $_.Name -notmatch '^slide_\d{2}\.png$' }
$pngs      = if ($freshPngs.Count -gt 0) { $freshPngs } else { $allPngs }
if ($pngs.Count -eq 0) {
  Write-Host "❌ $InputDir 에 PNG 파일이 없습니다. PNG ${SlideTotal}장을 넣어주세요." -ForegroundColor Red
  exit 1
}
Write-Host "✅ PNG $($pngs.Count)장 발견: $($pngs.Name -join ', ')" -ForegroundColor Green

# 2. PNG 파일명을 slide_01.png ~ slide_NN.png 로 복사/정렬
$i = 1
foreach ($png in $pngs) {
  $target = "$InputDir\slide_$(([string]$i).PadLeft(2,'0')).png"
  if ($png.FullName -ne (Resolve-Path $target -ErrorAction SilentlyContinue)) {
    Copy-Item $png.FullName $target -Force
  }
  $i++
}

# 3. output 폴더 생성
New-Item -ItemType Directory -Force (Split-Path $OutputFile) | Out-Null

# 4. 실제 복사된 슬라이드 파일 목록 → images 배열 (Root.tsx 기본값과 무관하게 항상 실제 파일과 일치시킴)
$slideCount = [Math]::Max($pngs.Count, 1)
$imageNames = 1..$slideCount | ForEach-Object { "input/slide_$(([string]$_).PadLeft(2,'0')).png" }
$imagesJsonArr = ($imageNames | ForEach-Object { "`"$_`"" }) -join ","

# 5. 나레이션 길이 감지 → 슬라이드당 프레임 자동 계산 (슬라이드당 동일 분배: 나레이션 길이 ÷ 슬라이드 수)
$narratorWav = ".\output_narration.wav"
if (Test-Path $narratorWav) {
  Write-Host "🎙️ 나레이션 감지 → 슬라이드 길이 자동 계산..." -ForegroundColor Cyan
  $durRaw = & ffprobe -v error -show_entries format=duration -of csv=p=0 $narratorWav 2>$null
  if ($durRaw -and $durRaw -match '[\d.]+') {
    $dur = [double]$durRaw
    # 나레이션 총 길이 ÷ 실제 슬라이드 수 × FPS(30) = 슬라이드당 프레임 (모든 슬라이드 동일)
    $dpsFrames = [int][Math]::Ceiling($dur / $slideCount * 30)
    $dps = [Math]::Round($dpsFrames / 30.0, 2)
    Write-Host "⏱️  나레이션 $($dur.ToString('F2'))초 / $slideCount 슬라이드  →  슬라이드당 $dps 초 ($dpsFrames 프레임, 균등 분배)" -ForegroundColor Cyan
  } else {
    $dpsFrames = 90
    Write-Host "⚠️  나레이션 길이 측정 실패 → 기본 슬라이드 길이 사용 (3초)" -ForegroundColor Yellow
  }
} else {
  $dpsFrames = 90
  Write-Host "ℹ️  나레이션 없음 → 기본 슬라이드 길이 사용 (3초)" -ForegroundColor Yellow
}

# 6. 슬라이드별 실제 음성 길이 비례 분배(slide_durations.json) 감지
#    server.py handle_generate_tts가 실제 TTS 음성 길이를 글자수 비례로 나눠 저장한 파일.
#    있으면 균등분배(5번) 대신 이 값을 슬라이드별로 적용해 실제 발화 길이에 더 가깝게 동기화한다.
$durationsArrJson = "null"
$slideDurFile = ".\slide_durations.json"
if (Test-Path $slideDurFile) {
  $durRawJson = Get-Content $slideDurFile -Raw -Encoding UTF8
  try {
    $secArr = $durRawJson | ConvertFrom-Json
    if ($secArr.Count -eq $slideCount) {
      $frameArr = $secArr | ForEach-Object { [int][Math]::Round($_ * 30) }
      $durationsArrJson = "[" + ($frameArr -join ",") + "]"
      Write-Host "🎯 슬라이드별 실제 음성 길이 비례 분배 적용: $($frameArr -join ', ') 프레임" -ForegroundColor Cyan
    } else {
      Write-Host "⚠️  slide_durations.json 슬라이드 수($($secArr.Count))가 현재 PNG 수($slideCount)와 달라 균등 분배로 폴백" -ForegroundColor Yellow
    }
  } catch {
    Write-Host "⚠️  slide_durations.json 파싱 실패 → 균등 분배로 폴백" -ForegroundColor Yellow
  }
} else {
  Write-Host "ℹ️  slide_durations.json 없음 → 균등 분배 사용 (나레이션 생성 시 자동 생성됨)" -ForegroundColor Yellow
}

# 7. 자막(captions.json) 감지 — server.py /generate-srt, /generate-tts 호출 시 슬라이드별 나레이션 텍스트가 저장됨
$captionsJsonArr = "null"
$captionsFile = ".\captions.json"
if (Test-Path $captionsFile) {
  $captionsRaw = Get-Content $captionsFile -Raw -Encoding UTF8
  try {
    $captionsParsed = $captionsRaw | ConvertFrom-Json
    Write-Host "💬 자막 감지 → $($captionsParsed.Count)개 슬라이드 텍스트 적용" -ForegroundColor Cyan
    $captionsJsonArr = $captionsRaw.Trim()
  } catch {
    Write-Host "⚠️  captions.json 파싱 실패 → 자막 미적용" -ForegroundColor Yellow
  }
} else {
  Write-Host "ℹ️  captions.json 없음 → 자막 미적용 (STEP 5.5에서 SRT 자막을 먼저 생성하세요)" -ForegroundColor Yellow
}

# 8. props 파일 작성 — images/durationPerSlideFrames/slideCount/captions/durationPerSlideFramesArr를
#    같은 소스(slideCount)로 통일. captions·durationPerSlideFramesArr가 없으면 키 자체를 생략
#    (JSON null을 보내면 JS 기본값(= [] / undefined)이 적용되지 않아 런타임 에러 위험)
$captionsField  = if ($captionsJsonArr -eq "null")  { "" } else { ",`"captions`":$captionsJsonArr" }
$durationsField = if ($durationsArrJson -eq "null") { "" } else { ",`"durationPerSlideFramesArr`":$durationsArrJson" }
$platformEsc    = $Platform -replace '\\', '\\\\' -replace '"', '\"'
$platformField  = if ($Platform) { ",`"platform`":`"$platformEsc`"" } else { "" }
if ($Platform) {
  Write-Host "🔗 CTA 오버레이 플랫폼: $Platform" -ForegroundColor Cyan
}
$propsJson = "{`"durationPerSlideFrames`":$dpsFrames,`"slideCount`":$slideCount,`"images`":[$imagesJsonArr]$captionsField$durationsField$platformField}"
$propsFile = [System.IO.Path]::Combine($env:TEMP, "remotion_props_$([System.Guid]::NewGuid().ToString('N').Substring(0,8)).json")
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($propsFile, $propsJson, $utf8NoBom)
$propsArg = @("--props=$propsFile")
Write-Host "📄 props → $propsFile  ($propsJson)" -ForegroundColor DarkCyan

# 9. Remotion 렌더
Write-Host "🎬 렌더링 시작..." -ForegroundColor Cyan
npx remotion render ShoppingShorts $OutputFile --codec=h264 --video-bitrate=10M --log=verbose @propsArg 2>&1
if ($LASTEXITCODE -eq 0) {
  Write-Host "✅ 완료! → $OutputFile" -ForegroundColor Green
} else {
  Write-Host "❌ 렌더 실패 (exit $LASTEXITCODE)" -ForegroundColor Red
}
