# Enter Spot Testnet credentials locally. This script does not save them to disk.
$testnetKeySecure = Read-Host 'Spot Testnet API key' -AsSecureString
$testnetSecretSecure = Read-Host 'Spot Testnet API secret' -AsSecureString

function Convert-SecureText([Security.SecureString]$value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

try {
    $env:BINANCE_TESTNET_API_KEY = Convert-SecureText $testnetKeySecure
    $env:BINANCE_TESTNET_API_SECRET = Convert-SecureText $testnetSecretSecure
    $env:AUTO_MODE = 'testnet'
    if (-not $env:BINANCE_TESTNET_API_KEY -or -not $env:BINANCE_TESTNET_API_SECRET) {
        throw 'Both Spot Testnet credentials are required.'
    }
    node (Join-Path $PSScriptRoot 'server.mjs')
}
finally {
    Remove-Item Env:BINANCE_TESTNET_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_TESTNET_API_SECRET -ErrorAction SilentlyContinue
    Remove-Item Env:AUTO_MODE -ErrorAction SilentlyContinue
}
