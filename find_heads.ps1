$files = Get-ChildItem -Path 'api/alembic/versions/*.py'
$revs = @{}
$downs = @{}

foreach ($f in $files) {
    $c = Get-Content $f.FullName -Raw
    if ($c -match 'revision\s*(?::\s*[^=]+)?\s*=\s*["'']([^"'']+)["'']') {
        $rev = $matches[1]
        $revs[$rev] = $f.Name
    }
    if ($c -match 'down_revision\s*(?::\s*[^=]+)?\s*=\s*["'']([^"'']+)["'']') {
        $downs[$matches[1]] = $true
    } elseif ($c -match 'down_revision\s*(?::\s*[^=]+)?\s*=\s*\(([^)]+)\)') {
        $raw = $matches[1]
        $ms = [regex]::Matches($raw, '["'']([^"'']+)["'']')
        foreach ($m in $ms) {
            $downs[$m.Groups[1].Value] = $true
        }
    }
}

Write-Output "--- HEADS FOUND ---"
foreach ($r in $revs.Keys) {
    if (-not $downs.ContainsKey($r)) {
        Write-Output "$r -> $($revs[$r])"
    }
}
