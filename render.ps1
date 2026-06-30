# render.ps1 — input/ 폴더 PNG 10장 → output/shopping-shorts.mp4 자동 렌더
param(
  [string]$InputDir = ".\public\input",
  [string]$OutputFile = ".\output\shopping-shorts.mp4"
)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$env:PATH = "C:\Users\JOEUN\tools\node;$env:PATH"

# 1. input 폴더 PNG 확인 (이전 실행이 만든 slide_NN.png 결과물은 "신규 원본"에서 제외 — 자기복제 방지)
$pngs = Get-ChildItem $InputDir -Filter "*.png" | Where-Object { $_.Name -notmatch '^slide_\d{2}\.png$' } | Sort-Object Name
if ($pngs.Count -eq 0) {
  Write-Host "❌ $InputDir 에 PNG 파일이 없습니다. PNG 10장을 넣어주세요." -ForegroundColor Red
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
    $dpsFrames = 75
    Write-Host "⚠️  나레이션 길이 측정 실패 → 기본 슬라이드 길이 사용 (2.5초)" -ForegroundColor Yellow
  }
} else {
  $dpsFrames = 75
  Write-Host "ℹ️  나레이션 없음 → 기본 슬라이드 길이 사용 (2.5초)" -ForegroundColor Yellow
}

# 6. props 파일 작성 — images/durationPerSlideFrames/slideCount를 같은 소스(slideCount)로 통일
$propsJson = "{`"durationPerSlideFrames`":$dpsFrames,`"slideCount`":$slideCount,`"images`":[$imagesJsonArr]}"
$propsFile = [System.IO.Path]::Combine($env:TEMP, "remotion_props_$([System.Guid]::NewGuid().ToString('N').Substring(0,8)).json")
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($propsFile, $propsJson, $utf8NoBom)
$propsArg = @("--props=$propsFile")
Write-Host "📄 props → $propsFile  ($propsJson)" -ForegroundColor DarkCyan

# 7. Remotion 렌더
Write-Host "🎬 렌더링 시작..." -ForegroundColor Cyan
npx remotion render ShoppingShorts $OutputFile --codec=h264 --log=verbose @propsArg 2>&1
if ($LASTEXITCODE -eq 0) {
  Write-Host "✅ 완료! → $OutputFile" -ForegroundColor Green
} else {
  Write-Host "❌ 렌더 실패 (exit $LASTEXITCODE)" -ForegroundColor Red
}
