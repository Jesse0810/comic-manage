[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ManifestPath = (Join-Path $PSScriptRoot 'komga_notable_entities_manifest.json'),
    [string]$BaseUrl,
    [string]$LibraryId,
    [Security.SecureString]$ApiKey
)

$ErrorActionPreference = 'Stop'
$knownKinds = @('原作', '发行性质', '出版形态', '修正状态', '年龄分级', '色彩', '内容类型')

function ConvertTo-RelativePath([string]$Value) {
    $normalized = $Value.Replace('\', '/').Trim('/')
    if (!$normalized -or $normalized -match '(^|/)\.\.?(/|$)' -or $normalized -match '^[a-zA-Z]:') {
        throw "Invalid relative path in manifest: $Value"
    }
    return $normalized
}

function ConvertTo-ServerPath([string]$Value) {
    if (!$Value) { return '' }
    # Komga returns decoded file paths. Decode only explicit file URLs, so a
    # real filename containing a literal sequence such as %20 remains intact.
    if ($Value -match '^file:') {
        $fileUri = [Uri]$Value
        if (!$fileUri.IsFile) { throw 'Expected a file URL.' }
        $Value = $fileUri.LocalPath
    }
    return $Value.Replace('\', '/').TrimEnd('/')
}

function Test-PathSuffix([string]$ServerPath, [string]$RelativePath) {
    $comparison = [StringComparison]::Ordinal
    if ($ServerPath -match '^[a-zA-Z]:/' -or $ServerPath.StartsWith('//')) {
        $comparison = [StringComparison]::OrdinalIgnoreCase
    }
    return $ServerPath.EndsWith('/' + $RelativePath, $comparison)
}

function Get-SeriesTitleKey([string]$Value) {
    # The exporter groups width and case variants, preserving inner whitespace.
    return $Value.Normalize([Text.NormalizationForm]::FormKC).Trim().ToLowerInvariant()
}

function Get-PairKey([string]$Kind, [string]$Name) {
    $kindKey = $Kind.Trim().ToLowerInvariant()
    return ('{0}:{1}{2}' -f $kindKey.Length, $kindKey, $Name.Trim())
}

function Get-PairSignature($Values, [string]$First, [string]$Second) {
    return (@($Values | ForEach-Object { Get-PairKey $_.$First $_.$Second } | Sort-Object -Unique -CaseSensitive) -join "`n")
}

function Merge-Entities($Existing, $Desired, $ManagedKinds) {
    $merged = [Collections.Generic.List[object]]::new()
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $unmanaged = @($Existing | Where-Object { $null -ne $_ -and $_.kind -notin $ManagedKinds })
    foreach ($entity in $unmanaged + @($Desired)) {
        if ($null -eq $entity) { continue }
        $key = Get-PairKey $entity.kind $entity.name
        if ($seen.Add($key)) {
            $merged.Add(@{ kind = [string]$entity.kind; name = [string]$entity.name })
        }
    }
    return [pscustomobject]@{ values = @($merged.ToArray()) }
}

function Merge-AlternateTitles($Existing, $Desired) {
    $merged = [Collections.Generic.List[object]]::new()
    foreach ($title in @($Existing) + @($Desired)) {
        if ($null -eq $title) { continue }
        $sameLabel = @($merged | Where-Object { $_.label -ieq $title.label })
        if ($sameLabel.Count -and @($sameLabel | Where-Object { $_.title -cne $title.title }).Count) {
            throw "Alternate title label '$($title.label)' already has a different title."
        }
        if (!$sameLabel.Count) {
            $merged.Add(@{ label = [string]$title.label; title = [string]$title.title })
        }
    }
    return [pscustomobject]@{ values = @($merged.ToArray()) }
}

function Invoke-Komga([string]$Method, [string]$ApiPath, $Body) {
    $arguments = @{
        Method = $Method
        Uri = $BaseUrl + $ApiPath
        Headers = $requestHeaders
        MaximumRedirection = 0
        ErrorAction = 'Stop'
        Verbose = $false
        Debug = $false
    }
    if ($null -ne $Body) {
        $arguments.ContentType = 'application/json; charset=utf-8'
        $arguments.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 30 -Compress))
    }
    try { return Invoke-RestMethod @arguments }
    catch {
        $message = $_.Exception.Message
        $secret = $requestHeaders['X-API-Key']
        if ($secret) { $message = $message.Replace($secret, '[redacted]') }
        throw "Komga request failed: $Method $ApiPath. $message"
    }
}

function Get-BookMatches($Entry, $Books, [string]$SelectedLibrary) {
    return @($Books | Where-Object {
        if ($_.deleted -or $_.libraryId -cne $SelectedLibrary -or $_.metadata.title -cne $Entry.title) { return $false }
        $serverPath = ConvertTo-ServerPath $_.url
        $fileName = ($serverPath -split '/')[-1]
        (Test-PathSuffix $serverPath $Entry.relativePath) -and $fileName -ceq $Entry.fileName
    })
}

if (!(Test-Path -LiteralPath $ManifestPath -PathType Leaf) -and !$PSBoundParameters.ContainsKey('ManifestPath')) {
    $ManifestPath = (Read-Host 'Full path to the exported Komga manifest JSON').Trim().Trim('"')
}
if (!(Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw 'Manifest file was not found.' }
$manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$managedKinds = $knownKinds
if ($manifest.PSObject.Properties.Name -contains 'managedEntityKinds') {
    if ($null -eq $manifest.managedEntityKinds) { throw 'managedEntityKinds must be an array.' }
    $managedKinds = @($manifest.managedEntityKinds)
    if (@($managedKinds | Where-Object { $_ -notin $knownKinds }).Count) { throw 'Manifest has unsupported managed entity kinds.' }
}
foreach ($entry in @($manifest.books)) {
    $entry.relativePath = ConvertTo-RelativePath $entry.relativePath
    if (!$entry.title -or !$entry.fileName -or ($entry.relativePath -split '/')[-1] -cne $entry.fileName) {
        throw 'Each manifest book needs a title and a fileName matching relativePath.'
    }
    foreach ($entity in @($entry.notableEntities)) {
        if (!$entity -or $entity.kind -notin $managedKinds -or [string]::IsNullOrWhiteSpace($entity.name)) {
            throw "Invalid notable entity in manifest book '$($entry.title)'."
        }
    }
}
foreach ($entry in @($manifest.series)) {
    $entry.relativePath = ConvertTo-RelativePath $entry.relativePath
    if ([string]::IsNullOrWhiteSpace($entry.seriesTitle)) { throw 'Each manifest series needs seriesTitle.' }
    foreach ($title in @($entry.alternateTitles)) {
        if (!$title -or [string]::IsNullOrWhiteSpace($title.label) -or [string]::IsNullOrWhiteSpace($title.title)) {
            throw 'Alternate titles need nonempty labels and titles.'
        }
    }
}

if (!$BaseUrl) { $BaseUrl = Read-Host 'Komga URL (for example http://192.168.1.10:25600)' }
$BaseUrl = $BaseUrl.TrimEnd('/')
$serverUri = $null
if (![Uri]::TryCreate($BaseUrl, [UriKind]::Absolute, [ref]$serverUri) -or $serverUri.Scheme -notin @('http', 'https') -or $serverUri.UserInfo -or $serverUri.Query -or $serverUri.Fragment) {
    throw 'Komga URL must be an absolute HTTP(S) URL without embedded credentials, query or fragment.'
}
if (!$ApiKey) { $ApiKey = Read-Host 'Komga administrator API key (hidden)' -AsSecureString }
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ApiKey)
try {
    $requestHeaders = @{ 'X-API-Key' = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer); Accept = 'application/json' }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
}

try {
    $libraries = @(Invoke-Komga 'Get' '/api/v1/libraries' $null)
    if (!$libraries.Count) { throw 'No accessible libraries were returned.' }
    if (!$LibraryId) {
        for ($index = 0; $index -lt $libraries.Count; $index++) { Write-Host "[$index] $($libraries[$index].name)" }
        $selection = -1
        if (![int]::TryParse((Read-Host 'Library number'), [ref]$selection) -or $selection -lt 0 -or $selection -ge $libraries.Count) { throw 'Invalid library number.' }
        $LibraryId = $libraries[$selection].id
    }
    $library = @($libraries | Where-Object { $_.id -ceq $LibraryId })
    if ($library.Count -ne 1) { throw 'LibraryId must identify exactly one accessible library.' }

    $books = [Collections.Generic.List[object]]::new()
    $seenBookIds = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $pageNumber = 0
    do {
        $query = @{ condition = @{ libraryId = @{ operator = 'is'; value = $LibraryId } } }
        $page = Invoke-Komga 'Post' "/api/v1/books/list?page=$pageNumber&size=200&sort=url,asc" $query
        if ($null -eq $page.content) { throw 'Book search did not return a paginated content array.' }
        foreach ($book in @($page.content)) {
            if ($seenBookIds.Add($book.id)) { $books.Add($book) }
        }
        $pageNumber++
        if ($pageNumber -gt 100000) { throw 'Too many book search pages.' }
    } while (!$page.last -and @($page.content).Count -gt 0 -and $pageNumber -lt $page.totalPages)

    $plan = [Collections.Generic.List[object]]::new()
    $plannedIds = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $skipped = 0
    foreach ($entry in @($manifest.books)) {
        $matches = @(Get-BookMatches $entry $books $LibraryId)
        if ($matches.Count -ne 1) {
            Write-Warning "Skipped book '$($entry.relativePath)': expected one path, filename and title match, got $($matches.Count)."
            $skipped++
            continue
        }
        $book = $matches[0]
        if (!$plannedIds.Add('book:' + $book.id)) { throw 'Manifest entries resolve to the same book more than once.' }
        $merged = (Merge-Entities $book.metadata.notableEntities $entry.notableEntities $managedKinds).values
        if ((Get-PairSignature $merged 'kind' 'name') -ceq (Get-PairSignature $book.metadata.notableEntities 'kind' 'name') -and $book.metadata.notableEntitiesLock) { continue }
        $plan.Add([pscustomobject]@{ type = 'book'; id = $book.id; entry = $entry; target = $entry.relativePath })
    }
    foreach ($entry in @($manifest.series)) {
        $seriesIds = @($books | Where-Object {
            $serverPath = ConvertTo-ServerPath $_.url
            $parentPath = $serverPath.Substring(0, [Math]::Max(0, $serverPath.LastIndexOf('/')))
            !$_.deleted -and $_.libraryId -ceq $LibraryId -and (Get-SeriesTitleKey $_.seriesTitle) -ceq (Get-SeriesTitleKey $entry.seriesTitle) -and (Test-PathSuffix $parentPath $entry.relativePath)
        } | Select-Object -ExpandProperty seriesId -Unique)
        if ($seriesIds.Count -ne 1) {
            Write-Warning "Skipped series '$($entry.relativePath)': expected one matching series, got $($seriesIds.Count)."
            $skipped++
            continue
        }
        $seriesId = $seriesIds[0]
        if (!$plannedIds.Add('series:' + $seriesId)) { throw 'Manifest entries resolve to the same series more than once.' }
        $series = Invoke-Komga 'Get' "/api/v1/series/$([Uri]::EscapeDataString($seriesId))" $null
        try { $merged = (Merge-AlternateTitles $series.metadata.alternateTitles $entry.alternateTitles).values }
        catch { Write-Warning "Skipped series '$($entry.seriesTitle)': $($_.Exception.Message)"; $skipped++; continue }
        if ((Get-PairSignature $merged 'label' 'title') -ceq (Get-PairSignature $series.metadata.alternateTitles 'label' 'title') -and $series.metadata.alternateTitlesLock) { continue }
        $plan.Add([pscustomobject]@{ type = 'series'; id = $seriesId; entry = $entry; target = $entry.relativePath })
    }

    Write-Host "Library '$($library[0].name)': $($plan.Count) updates planned, $skipped unmatched or conflicting entries skipped."
    foreach ($operation in $plan) { Write-Host "  $($operation.type): $($operation.target)" }
    if (!$plan.Count) { return }
    if (!$WhatIfPreference -and (Read-Host 'Enter YES to apply this plan') -cne 'YES') { Write-Host 'Cancelled.'; return }

    $updated = 0
    $failed = 0
    foreach ($operation in $plan) {
        if (!$PSCmdlet.ShouldProcess($operation.target, 'Update Komga metadata')) { continue }
        try {
            $escapedId = [Uri]::EscapeDataString($operation.id)
            if ($operation.type -eq 'book') {
                # Re-read immediately before PATCH to preserve newly added unmanaged entities.
                $current = Invoke-Komga 'Get' "/api/v1/books/$escapedId" $null
                if (@(Get-BookMatches $operation.entry @($current) $LibraryId).Count -ne 1) { throw 'Book identity changed after preview.' }
                $entities = (Merge-Entities $current.metadata.notableEntities $operation.entry.notableEntities $managedKinds).values
                $patch = @{ notableEntities = @($entities); notableEntitiesLock = $true }
                Invoke-Komga 'Patch' "/api/v1/books/$escapedId/metadata" $patch | Out-Null
            } else {
                $current = Invoke-Komga 'Get' "/api/v1/series/$escapedId" $null
                if ($current.libraryId -cne $LibraryId -or (Get-SeriesTitleKey $current.metadata.title) -cne (Get-SeriesTitleKey $operation.entry.seriesTitle) -or !(Test-PathSuffix (ConvertTo-ServerPath $current.url) $operation.entry.relativePath)) { throw 'Series identity changed after preview.' }
                $titles = (Merge-AlternateTitles $current.metadata.alternateTitles $operation.entry.alternateTitles).values
                $patch = @{ alternateTitles = @($titles); alternateTitlesLock = $true }
                Invoke-Komga 'Patch' "/api/v1/series/$escapedId/metadata" $patch | Out-Null
            }
            $updated++
        } catch {
            Write-Warning "Update failed for '$($operation.target)': $($_.Exception.Message)"
            $failed++
        }
    }
    Write-Host "Finished: $updated updated, $skipped skipped, $failed failed."
    if ($failed) { throw 'Some Komga updates failed. Review the messages above before retrying.' }
} finally {
    if ($requestHeaders) { $requestHeaders.Clear() }
    $requestHeaders = $null
}
