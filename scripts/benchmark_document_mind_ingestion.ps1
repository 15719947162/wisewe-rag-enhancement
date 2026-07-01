param(
    [string]$ApiBase = "http://localhost:8000",
    [string]$PdfPath = "data/uploads/080ed361-f4f5-4be6-b134-99d53ecbd19a.pdf",
    [string]$OutputJsonl = "data/results/document_mind_ingestion_benchmark.jsonl",
    [string[]]$CandidateNames = @("p33-c4-no-llm-layout-full"),
    [int]$TimeoutSeconds = 1800,
    [int]$PollSeconds = 10,
    [int]$TaskRequestTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"

$candidates = @(
    @{
        name = "p33-c4-no-llm-layout-full"
        pages = "33"
        concurrency = "4"
        inflight = "1"
        waves = "2"
        probe = "1"
        llmEnhancement = "false"
        outputFormat = "markdown,visualLayoutInfo"
        hedge = "false"
    },
    @{
        name = "p33-c4-managed-layout-full"
        pages = "33"
        concurrency = "4"
        inflight = "1"
        waves = "2"
        probe = "1"
        llmEnhancement = "true"
        outputFormat = "markdown,visualLayoutInfo"
        hedge = "false"
    }
)

if ($CandidateNames.Count -gt 0) {
    $wanted = @{}
    foreach ($candidateName in $CandidateNames) {
        foreach ($normalized in ($candidateName -split '[,;\s]+' | Where-Object { $_ })) {
            $wanted[$normalized] = $true
        }
    }
    $candidates = @($candidates | Where-Object { $wanted.ContainsKey([string]$_.name) })
    if ($candidates.Count -eq 0) {
        throw "No ingestion benchmark candidates matched: $($CandidateNames -join ', ')"
    }
}

$resolvedPdf = (Resolve-Path -LiteralPath $PdfPath).Path
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputJsonl) | Out-Null

function Invoke-SettingsUpdate {
    param([hashtable]$Candidate)

    $payload = @{
        PDF_PARSER_PROVIDER = "ali_document_mind"
        ALIYUN_DOCUMENT_MIND_SHARDING_ENABLED = "true"
        ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD = $Candidate.pages
        ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY = $Candidate.concurrency
        ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY = $Candidate.inflight
        ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES = $Candidate.waves
        ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD = "20"
        ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY = $Candidate.probe
        ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT = $Candidate.outputFormat
        ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT = $Candidate.llmEnhancement
        ALIYUN_DOCUMENT_MIND_ENHANCEMENT_MODE = "VLM"
        ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED = $Candidate.hedge
        ALIYUN_DOCUMENT_MIND_HEDGE_AFTER_SECONDS = "90"
        ALIYUN_DOCUMENT_MIND_HEDGE_MAX_EXTRA_ATTEMPTS = "1"
    }

    Invoke-RestMethod `
        -Uri "$ApiBase/api/console/settings" `
        -Method Put `
        -ContentType "application/json; charset=utf-8" `
        -Body ($payload | ConvertTo-Json -Depth 4) `
        -TimeoutSec 30 | Out-Null
}

function New-BenchmarkKnowledgeBase {
    param([string]$CandidateName)

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $payload = @{
        name = "bench-$CandidateName-$stamp"
        description = "Document Mind full-ingestion benchmark"
        strategy = "hierarchical"
    }
    return Invoke-RestMethod `
        -Uri "$ApiBase/api/knowledge-bases" `
        -Method Post `
        -ContentType "application/json; charset=utf-8" `
        -Body ($payload | ConvertTo-Json -Depth 4) `
        -TimeoutSec 30
}

function Invoke-CurlJson {
    param([string[]]$Arguments)

    $output = & curl.exe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "curl.exe failed with exit code ${LASTEXITCODE}: $($output -join "`n")"
    }
    $text = ($output -join "`n").Trim()
    if (-not $text) {
        throw "curl.exe returned an empty response"
    }
    return $text | ConvertFrom-Json
}

function Start-IngestionTask {
    param([string]$KnowledgeBaseId)

    $uploadUrl = "$ApiBase/api/ingestion/upload?kb_id=$KnowledgeBaseId&strategy=hierarchical&subject_type=general&layout_type=single_column&auto_confirm=true"
    $fileSpec = "file=@$resolvedPdf;filename=benchmark.pdf;type=application/pdf"
    return Invoke-CurlJson @("-sS", "-X", "POST", "-F", $fileSpec, $uploadUrl)
}

function Wait-IngestionTask {
    param([string]$TaskId)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ($true) {
        try {
            $task = Invoke-RestMethod -Uri "$ApiBase/api/ingestion/tasks/$TaskId" -TimeoutSec $TaskRequestTimeoutSeconds
        }
        catch {
            if ((Get-Date) -gt $deadline) {
                throw
            }
            Write-Host "task=$TaskId poll=timeout retrying"
            Start-Sleep -Seconds $PollSeconds
            continue
        }
        $status = [string]$task.status
        $current = ""
        $currentStage = $task.stages | Where-Object { $_.status -eq "running" } | Select-Object -First 1
        if ($currentStage) {
            $current = "$($currentStage.key):$($currentStage.progress)%"
        }
        Write-Host "task=$TaskId status=$status $current"

        if ($status -in @("success", "failed") -or $task.done -eq $true) {
            return $task
        }
        if ((Get-Date) -gt $deadline) {
            throw "Timed out waiting for ingestion task $TaskId after ${TimeoutSeconds}s"
        }
        Start-Sleep -Seconds $PollSeconds
    }
}

function Convert-StageSummary {
    param($Task)

    $summary = [ordered]@{}
    foreach ($stage in $Task.stages) {
        $latencyMs = 0
        if ($null -ne $stage.latencyMs) {
            $latencyMs = [int]$stage.latencyMs
        }
        $inputCount = 0
        if ($null -ne $stage.inputCount) {
            $inputCount = [int]$stage.inputCount
        }
        $outputCount = 0
        if ($null -ne $stage.outputCount) {
            $outputCount = [int]$stage.outputCount
        }
        $summary[[string]$stage.key] = [ordered]@{
            status = $stage.status
            latencyMs = $latencyMs
            inputCount = $inputCount
            outputCount = $outputCount
            metrics = $stage.metrics
        }
    }
    return $summary
}

function Get-StageValue {
    param($Stages, [string]$Stage, [string]$Field)

    if (-not $Stages.Contains($Stage)) {
        return 0
    }
    $value = $Stages[$Stage][$Field]
    if ($null -eq $value) {
        return 0
    }
    return [int]$value
}

function New-BenchmarkRecord {
    param(
        [hashtable]$Candidate,
        $KnowledgeBase,
        $Upload,
        $Task,
        [datetime]$StartedAt,
        [datetime]$FinishedAt
    )

    $stages = Convert-StageSummary -Task $Task
    $totalStageMs = 0
    foreach ($key in $stages.Keys) {
        $latencyMs = $stages[$key].latencyMs
        if ($null -ne $latencyMs) {
            $totalStageMs += [int]$latencyMs
        }
    }

    $chunkCount = 0
    if ($null -ne $Task.chunkCount) {
        $chunkCount = [int]$Task.chunkCount
    }

    return [ordered]@{
        startedAt = $StartedAt.ToString("o")
        finishedAt = $FinishedAt.ToString("o")
        candidate = $Candidate.name
        settings = $Candidate
        ok = ([string]$Task.status -eq "success")
        taskId = $Upload.task_id
        kbId = $KnowledgeBase.id
        taskWallMs = [int](($FinishedAt - $StartedAt).TotalMilliseconds)
        totalStageMs = $totalStageMs
        status = $Task.status
        error = $Task.error
        chunkCount = $chunkCount
        stageLatencies = [ordered]@{
            upload = Get-StageValue $stages "upload" "latencyMs"
            parse = Get-StageValue $stages "parse" "latencyMs"
            clean = Get-StageValue $stages "clean" "latencyMs"
            chunk = Get-StageValue $stages "chunk" "latencyMs"
            quality = Get-StageValue $stages "quality" "latencyMs"
            embedding = Get-StageValue $stages "embedding" "latencyMs"
            export = Get-StageValue $stages "export" "latencyMs"
        }
        stageCounts = [ordered]@{
            parseOutput = Get-StageValue $stages "parse" "outputCount"
            cleanOutput = Get-StageValue $stages "clean" "outputCount"
            chunkOutput = Get-StageValue $stages "chunk" "outputCount"
            qualityOutput = Get-StageValue $stages "quality" "outputCount"
            embeddingOutput = Get-StageValue $stages "embedding" "outputCount"
            exportOutput = Get-StageValue $stages "export" "outputCount"
        }
        stages = $stages
    }
}

Write-Host "Document Mind full-ingestion benchmark started: $resolvedPdf"

foreach ($candidate in $candidates) {
    $startedAt = Get-Date
    $name = [string]$candidate.name
    Write-Host "==> $name llm=$($candidate.llmEnhancement) output=$($candidate.outputFormat)"

    try {
        Invoke-SettingsUpdate -Candidate $candidate
        $kb = New-BenchmarkKnowledgeBase -CandidateName $name
        Write-Host "created kb=$($kb.id)"
        $upload = Start-IngestionTask -KnowledgeBaseId $kb.id
        Write-Host "started task=$($upload.task_id)"
        $task = Wait-IngestionTask -TaskId $upload.task_id
        $record = New-BenchmarkRecord -Candidate $candidate -KnowledgeBase $kb -Upload $upload -Task $task -StartedAt $startedAt -FinishedAt (Get-Date)
    }
    catch {
        $record = [ordered]@{
            startedAt = $startedAt.ToString("o")
            finishedAt = (Get-Date).ToString("o")
            candidate = $name
            settings = $candidate
            ok = $false
            error = ($_ | Out-String).Trim()
        }
    }

    $line = $record | ConvertTo-Json -Depth 50 -Compress
    Add-Content -LiteralPath $OutputJsonl -Value $line -Encoding UTF8
    Write-Host $line
}

Write-Host "Benchmark records written to $OutputJsonl"
