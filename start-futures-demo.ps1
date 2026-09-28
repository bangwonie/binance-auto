# Enter Futures Demo credentials locally. This script never writes them to disk.
param(
    [switch]$WithRealAccount,
    [switch]$EnableRealTrading,
    [decimal]$RealMaxUsdt = 60,
    [decimal]$RealDailyLossUsdt = 20,
    [decimal]$RealStopLossPct = 2
)
$WithRealAccount = $WithRealAccount -or $EnableRealTrading
$demoKeySecure = Read-Host 'Futures Demo API key' -AsSecureString
$demoSecretSecure = Read-Host 'Futures Demo API secret' -AsSecureString
if ($WithRealAccount) {
    $realKeySecure = Read-Host 'Futures Real API key (read-only)' -AsSecureString
    $realSecretSecure = Read-Host 'Futures Real API secret' -AsSecureString
}

function Convert-SecureText([Security.SecureString]$value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

try {
    $env:BINANCE_FUTURES_DEMO_API_KEY = Convert-SecureText $demoKeySecure
    $env:BINANCE_FUTURES_DEMO_API_SECRET = Convert-SecureText $demoSecretSecure
    if ($WithRealAccount) {
        $env:BINANCE_REAL_API_KEY = Convert-SecureText $realKeySecure
        $env:BINANCE_REAL_API_SECRET = Convert-SecureText $realSecretSecure
    }
    if ($EnableRealTrading) {
        if ($RealMaxUsdt -lt 50 -or $RealMaxUsdt -gt 1000) { throw 'RealMaxUsdt must be between 50 and 1000.' }
        if ($RealDailyLossUsdt -lt 1 -or $RealDailyLossUsdt -gt 1000) { throw 'RealDailyLossUsdt must be between 1 and 1000.' }
        if ($RealStopLossPct -lt 0.1 -or $RealStopLossPct -gt 10) { throw 'RealStopLossPct must be between 0.1 and 10.' }
        $env:REAL_TRADING_ENABLED = '1'
        $env:REAL_MAX_USDT = $RealMaxUsdt.ToString([Globalization.CultureInfo]::InvariantCulture)
        $env:REAL_DAILY_LOSS_USDT = $RealDailyLossUsdt.ToString([Globalization.CultureInfo]::InvariantCulture)
        $env:REAL_STOP_LOSS_PCT = $RealStopLossPct.ToString([Globalization.CultureInfo]::InvariantCulture)
    }
    $env:AUTO_MODE = 'futures_demo'
    if (-not $env:BINANCE_FUTURES_DEMO_API_KEY -or -not $env:BINANCE_FUTURES_DEMO_API_SECRET) {
        throw 'Both Futures Demo credentials are required.'
    }
    node (Join-Path $PSScriptRoot 'server.mjs')
}
finally {
    Remove-Item Env:BINANCE_FUTURES_DEMO_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_FUTURES_DEMO_API_SECRET -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_REAL_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_REAL_API_SECRET -ErrorAction SilentlyContinue
    Remove-Item Env:REAL_TRADING_ENABLED -ErrorAction SilentlyContinue
    Remove-Item Env:REAL_MAX_USDT -ErrorAction SilentlyContinue
    Remove-Item Env:REAL_DAILY_LOSS_USDT -ErrorAction SilentlyContinue
    Remove-Item Env:REAL_STOP_LOSS_PCT -ErrorAction SilentlyContinue
    Remove-Item Env:AUTO_MODE -ErrorAction SilentlyContinue
}
