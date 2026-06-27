# render.ps1 — input/ 폴더 PNG 10장 → output/shopping-shorts.mp4 자동 렌더
param(
  [string]$InputDir = ".\public\input",
  [string]$OutputFile = ".\output\shopping-shorts.mp4"
)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$env:PATH = "C:\Users\JOEUN\tools\node;$env:PATH"

# 1. input 폴더 PNG 확인
$pngs = Get-ChildItem $InputDir -Filter "*.png" | Sort-Object Name
if ($pngs.Count -eq 0) {
  Write-Host "❌ $InputDir 에 PNG 파일이 없습니다. PNG 10장을 넣어주세요." -ForegroundColor Red
  exit 1
}
Write-Host "✅ PNG $($pngs.Count)장 발견: $($pngs.Name -join ', ')" -ForegroundColor Green

# 2. PNG 파일명을 slide01.png ~ slide10.png 로 복사/정렬
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

# 4. 나레이션 길이 감지 → 슬라이드당 프레임 자동 계산
$propsArg = @()
$narratorWav = ".\output_narration.wav"
if (Test-Path $narratorWav) {
  Write-Host "🎙️ 나레이션 감지 → 슬라이드 길이 자동 계산..." -ForegroundColor Cyan
  $durRaw = & ffprobe -v error -show_entries format=duration -of csv=p=0 $narratorWav 2>$null
  if ($durRaw -and $durRaw -match '[\d.]+') {
    $dur = [double]$durRaw
    # 나레이션 총 길이 ÷ 슬라이드 수(10) × FPS(30) = 슬라이드당 프레임
    $dpsFrames = [int][Math]::Ceiling($dur / 10.0 * 30)
    $dps = [Math]::Round($dpsFrames / 30.0, 2)
    Write-Host "⏱️  나레이션 $($dur.ToString('F2'))초  →  슬라이드당 $dps 초 ($dpsFrames 프레임)  →  총 $([Math]::Round($dur,1))초" -ForegroundColor Cyan
    $propsJson = "{`"durationPerSlideFrames`":$dpsFrames}"
    $propsFile = [System.IO.Path]::Combine($env:TEMP, "remotion_props_$([System.Guid]::NewGuid().ToString('N').Substring(0,8)).json")
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($propsFile, $propsJson, $utf8NoBom)
    $propsArg = @("--props=$propsFile")
    Write-Host "📄 props → $propsFile  ($propsJson)" -ForegroundColor DarkCyan
  }
} else {
  Write-Host "ℹ️  나레이션 없음 → 기본 슬라이드 길이 사용 (2.5초)" -ForegroundColor Yellow
}

# 5. Remotion 렌더
Write-Host "🎬 렌더링 시작..." -ForegroundColor Cyan
npx remotion render ShoppingShorts $OutputFile --codec=h264 --log=verbose @propsArg 2>&1
if ($LASTEXITCODE -eq 0) {
  Write-Host "✅ 완료! → $OutputFile" -ForegroundColor Green
} else {
  Write-Host "❌ 렌더 실패 (exit $LASTEXITCODE)" -ForegroundColor Red
}
