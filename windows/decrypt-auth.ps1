param(
  [string]$ProfileDir = "$env:APPDATA\com.qoder.app.stable"
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security

$statePath = Join-Path $ProfileDir "Local State"
$authPath  = Join-Path $ProfileDir "auth.v1.dat"
if (-not (Test-Path $statePath)) { throw "missing Local State" }
if (-not (Test-Path $authPath))  { throw "missing auth.v1.dat" }

$localState = Get-Content $statePath -Raw | ConvertFrom-Json
$encKeyB64 = $localState.os_crypt.encrypted_key
$encKey = [Convert]::FromBase64String($encKeyB64)
if ([Text.Encoding]::ASCII.GetString($encKey[0..4]) -ne "DPAPI") { throw "unexpected key prefix" }
$key = [System.Security.Cryptography.ProtectedData]::Unprotect(
  $encKey[5..($encKey.Length-1)], $null,
  [System.Security.Cryptography.DataProtectionScope]::CurrentUser)

$raw = [IO.File]::ReadAllBytes($authPath)
if ([Text.Encoding]::ASCII.GetString($raw[0..2]) -ne "v10") { throw "unexpected blob prefix" }
$nonce = $raw[3..14]
$tag = $raw[($raw.Length-16)..($raw.Length-1)]
$ct  = $raw[15..($raw.Length-17)]

$aes = [System.Security.Cryptography.AesGcm]::new($key, 16)
$plain = [byte[]]::new($ct.Length)
$aes.Decrypt($nonce, $ct, $tag, $plain, $null)
$aes.Dispose()

$json = [Text.Encoding]::UTF8.GetString($plain)
Write-Output $json
