$files = Get-ChildItem -Path 'api/alembic/versions/*.py'
$revs = @{}
$downMap = @{}
$downs = @{}

foreach ($f in $files) {
    $c = Get-Content $f.FullName -Raw
    $rev = $null
    if ($c -match 'revision\s*(?::\s*[^=]+)?\s*=\s*["'']([^"'']+)["'']') {
        $rev = $matches[1]
        $revs[$rev] = $f.Name
    }
    if ($c -match 'down_revision\s*(?::\s*[^=]+)?\s*=\s*["'']([^"'']+)["'']') {
        $downs[$matches[1]] = $true
        $downMap[$rev] = @($matches[1])
    } elseif ($c -match 'down_revision\s*(?::\s*[^=]+)?\s*=\s*\(([^)]+)\)') {
        $raw = $matches[1]
        $ms = [regex]::Matches($raw, '["'']([^"'']+)["'']')
        $list = @()
        foreach ($m in $ms) {
            $downs[$m.Groups[1].Value] = $true
            $list += $m.Groups[1].Value
        }
        $downMap[$rev] = $list
    } else {
        $downMap[$rev] = @()
    }
}

$heads = @()
foreach ($r in $revs.Keys) {
    if (-not $downs.ContainsKey($r)) {
        $heads += $r
    }
}

foreach ($h in $heads) {
    Write-Output "=== HEAD: $h ($($revs[$h])) ==="
    $curr = $h
    $depth = 0
    while ($curr -and $depth -lt 30) {
        $parents = $downMap[$curr]
        if (-not $parents -or $parents.Count -eq 0) {
            Write-Output "   -> ROOT: $curr"
            break
        }
        $p = $parents[0]
        Write-Output "   <- $p ($($revs[$p]))"
        $curr = $p
        $depth++
    }
}
