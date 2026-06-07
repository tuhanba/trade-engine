# run_aurvex.ps1 -- Aurvex AI Trade Engine Auto-Runner & Restarter
# ================================================================
# Bu betik, trade motorunun kesintisiz çalışmasını sağlar.
# Eğer motor çökerse otomatik olarak yeniden başlatır.
# Konsol çıktılarını 'logs/bot_console.log' dosyasına yazar.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Logs klasörünü oluştur
if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
}

$LogFile = "logs/bot_console.log"
$MaxCrashCount = 5
$CrashTimeWindowSeconds = 300 # 5 dakika
$CrashTimestamps = @()

Write-Host "======================================================" -ForegroundColor Green
Write-Host "  AURVEX AI TRADE ENGINE OTONOM BAŞLATICI VE KORUYUCU" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host "Log Dosyası: $LogFile" -ForegroundColor Cyan
Write-Host "Sistem kontrol ediliyor..." -ForegroundColor Yellow

# 1. Health Check çalıştır
try {
    $HealthCheck = python health_check.py
    Write-Host "Sistem Sağlık Kontrolü: BAŞARILI" -ForegroundColor Green
} catch {
    Write-Host "UYARI: Sistem Sağlık Kontrolü bazı hatalar bildirdi veya çalıştırılamadı!" -ForegroundColor Red
}

Write-Host "Motor başlatılıyor. Çökmeler otomatik olarak izlenecek ve kurtarılacaktır." -ForegroundColor Green

while ($true) {
    Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | [Runner] Trade motoru başlatılıyor..." -ForegroundColor Yellow
    
    $StartTime = Get-Date
    
    # Python motorunu başlat ve çıktıları log dosyasına yönlendir, işlemin tamamlanmasını bekle
    $Process = Start-Process python -ArgumentList "async_scalp_engine.py" -NoNewWindow -PassThru -RedirectStandardOutput $LogFile -RedirectStandardError $LogFile -Wait
    
    $EndTime = Get-Date
    $Duration = ($EndTime - $StartTime).TotalSeconds
    
    Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | [Runner] Motor kapandı. Çıkış Kodu: $($Process.ExitCode) | Çalışma Süresi: $($Duration) sn" -ForegroundColor Red
    
    # Eğer çalışma süresi 15 saniyeden kısaysa bunu hızlı çökme olarak kaydet
    if ($Duration -lt 15) {
        $Now = [DateTimeOffset]::Now.ToUnixTimeSeconds()
        $CrashTimestamps += $Now
        
        # 5 dakikadan eski çökme zaman damgalarını temizle
        $Limit = $Now - $CrashTimeWindowSeconds
        $CrashTimestamps = $CrashTimestamps | Where-Object { $_ -gt $Limit }
        
        Write-Host "[Runner] Sık çökme tespiti. Son 5 dakikadaki çökme sayısı: $($CrashTimestamps.Count)/$MaxCrashCount" -ForegroundColor Magenta
        
        if ($CrashTimestamps.Count -ge $MaxCrashCount) {
            Write-Host "🚨 KRİTİK: Motor 5 dakika içinde 5 kez çöktü! Sürekli döngüyü engellemek için başlatma duraklatıldı." -ForegroundColor Red
            Write-Host "Lütfen '$LogFile' dosyasını inceleyin ve ağ/bağlantı sorunlarını çözün." -ForegroundColor Red
            Write-Host "Yeniden başlatmak için 60 saniye bekleniyor..." -ForegroundColor Yellow
            Start-Sleep -Seconds 60
            $CrashTimestamps = @()
        } else {
            Write-Host "[Runner] Yeniden denemek için 5 saniye bekleniyor..." -ForegroundColor Yellow
            Start-Sleep -Seconds 5
        }
    } else {
        # Sağlıklı çalışıp kapandıysa çökme sayacını temizle ve yeniden başlat
        Write-Host "[Runner] Motor sağlıklı şekilde çalıştıktan sonra kapandı. Yeniden başlatılıyor..." -ForegroundColor Cyan
        Start-Sleep -Seconds 2
    }
}
