param(
    [string]$InputApk = "$PSScriptRoot/../android/app/build/outputs/apk/release/app-release.apk",
    [string]$OutputApk = "$PSScriptRoot/../../artifacts/apk/1Cpace-Android.apk"
)
$ErrorActionPreference = 'Stop'
$env:DEBUG = ''
$apkJavaHome = 'C:/Program Files/Eclipse Adoptium/jdk-25.0.2.10-hotspot'
$apkTools = Join-Path $env:LOCALAPPDATA 'Android/Sdk/build-tools/36.0.0'
$apkKeyDirectory = Join-Path $env:USERPROFILE '.android/onecpase-mobile'
$apkKey = Join-Path $apkKeyDirectory 'onecpase-mobile.p12'
$apkPasswordFile = Join-Path $apkKeyDirectory 'signing-password.dpapi'
$apkAlias = 'onecpase-mobile'

function Invoke-ApkTool {
    param([string]$Executable, [string[]]$ToolArguments)
    # Windows PowerShell treats redirected native stderr as ErrorRecords, even
    # when keytool is only reporting successful key generation.
    $apkPreviousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @ToolArguments 2>&1 | ForEach-Object { $_.ToString() }
        $apkToolExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $apkPreviousErrorPreference
    }
    if ($apkToolExit -ne 0) { throw "APK tool failed with exit code ${apkToolExit}: $Executable" }
}

if (!(Test-Path -LiteralPath $InputApk)) { throw 'Build the release APK first.' }
New-Item -ItemType Directory -Force -Path $apkKeyDirectory | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputApk) | Out-Null
if (!(Test-Path -LiteralPath $apkPasswordFile)) {
    if (Test-Path -LiteralPath $apkKey) { throw 'The existing signing key needs its protected password file.' }
    $apkRandom = New-Object byte[] 32
    $apkRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $apkRng.GetBytes($apkRandom) } finally { $apkRng.Dispose() }
    $apkSecurePassword = ConvertTo-SecureString ([Convert]::ToBase64String($apkRandom)) -AsPlainText -Force
    ConvertFrom-SecureString $apkSecurePassword | Set-Content -LiteralPath $apkPasswordFile -Encoding ascii
} else {
    $apkSecurePassword = Get-Content -LiteralPath $apkPasswordFile | ConvertTo-SecureString
}
$apkPointer = [IntPtr]::Zero
$apkAligned = $null
try {
    $apkPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($apkSecurePassword)
    $env:ONECPASE_APK_SIGNING_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($apkPointer)
    if (!(Test-Path -LiteralPath $apkKey)) {
        Invoke-ApkTool "$apkJavaHome/bin/keytool.exe" @('-J-Xmx64m', '-genkeypair', '-noprompt', '-storetype', 'PKCS12',
            '-keystore', $apkKey, '-alias', $apkAlias, '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000',
            '-dname', 'CN=1Cpace Mobile,O=1Cpace,C=ZA', '-storepass:env', 'ONECPASE_APK_SIGNING_PASSWORD')
    }
    & "$apkTools/zipalign.exe" -c -P 16 4 $InputApk
    if ($LASTEXITCODE -ne 0) {
        $apkAligned = Join-Path (Split-Path -Parent $OutputApk) 'aligned-intermediate.apk'
        & "$apkTools/zipalign.exe" -P 16 -f 4 $InputApk $apkAligned
        if ($LASTEXITCODE -ne 0) { throw 'APK alignment failed.' }
        $InputApk = $apkAligned
    }
    Invoke-ApkTool "$apkJavaHome/bin/java.exe" @('--enable-native-access=ALL-UNNAMED', '-Xmx128m', '-jar', "$apkTools/lib/apksigner.jar", 'sign',
        '--ks', $apkKey, '--ks-key-alias', $apkAlias, '--ks-pass', 'env:ONECPASE_APK_SIGNING_PASSWORD',
        '--key-pass', 'env:ONECPASE_APK_SIGNING_PASSWORD', '--out', $OutputApk, $InputApk)
    Invoke-ApkTool "$apkJavaHome/bin/java.exe" @('--enable-native-access=ALL-UNNAMED', '-Xmx128m', '-jar', "$apkTools/lib/apksigner.jar",
        'verify', '--verbose', '--print-certs', $OutputApk)
    & "$apkTools/zipalign.exe" -c -P 16 4 $OutputApk
    if ($LASTEXITCODE -ne 0) { throw 'Signed APK alignment verification failed.' }
    Get-FileHash -LiteralPath $OutputApk -Algorithm SHA256
} finally {
    Remove-Item Env:ONECPASE_APK_SIGNING_PASSWORD -ErrorAction SilentlyContinue
    if ($apkPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($apkPointer) }
    if ($apkAligned -and (Test-Path -LiteralPath $apkAligned)) { Remove-Item -LiteralPath $apkAligned }
}
