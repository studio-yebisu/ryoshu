$ErrorActionPreference = 'Stop'

if (-not (Get-Command Get-RyoshuFileSha256 -CommandType Function -ErrorAction SilentlyContinue)) {
    . (Join-Path $PSScriptRoot 'python-runtime.ps1')
}

function Get-RyoshuUv([string]$Directory) {
    $target = [System.IO.Path]::GetFullPath($Directory)
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    if ((Get-Item -LiteralPath $target).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw '実行環境の保存先にリンクは使えません。' }
    $executable = Join-Path $target 'uv.exe'
    $executableHash = 'f45d1cb9d9140ca6d3f4005fdfc0ebeccf849fd04d1dc85804155cb923f1597b'
    if ((Test-Path -LiteralPath $executable) -and (Get-RyoshuFileSha256 -LiteralPath $executable) -eq $executableHash) { return $executable }
    $archivePath = Join-Path $target ('download-' + [guid]::NewGuid().ToString('N') + '.zip')
    $candidate = Join-Path $target ('uv-' + [guid]::NewGuid().ToString('N') + '.exe')
    try {
        Write-Host '実行環境の準備ツール（uv 0.13.5）を公式配布元から取得しています。'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/astral-sh/uv/releases/download/0.13.5/uv-x86_64-pc-windows-msvc.zip' -OutFile $archivePath
        if ((Get-RyoshuFileSha256 -LiteralPath $archivePath) -ne 'a38b8d1902b7cfbc76b423ba1bb456f623f42064cdc72628b6075b89afd1019a') { throw '取得ファイルの検証値が違います。実行せずに停止しました。' }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
        try {
            $entries = @($archive.Entries | Where-Object { $_.FullName -ceq 'uv.exe' })
            if ($entries.Count -ne 1 -or $entries[0].Length -ne 31899952) { throw '準備ツールの内容が一致しません。' }
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0],$candidate,$false)
        } finally { $archive.Dispose() }
        if ((Get-RyoshuFileSha256 -LiteralPath $candidate) -ne $executableHash) { throw '準備ツールの検証値が違います。実行せずに停止しました。' }
        # A corrupt previous tool is replaced only after the candidate was verified.
        [System.IO.File]::Copy($candidate,$executable,$true)
        return $executable
    } finally {
        if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
        if (Test-Path -LiteralPath $candidate) { Remove-Item -LiteralPath $candidate -Force }
    }
}
