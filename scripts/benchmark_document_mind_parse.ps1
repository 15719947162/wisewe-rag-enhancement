param(
    [string]$ApiBase = "http://localhost:8000",
    [string]$Container = "wisewe-rag-backend",
    [string]$PdfPath = "/app/data/uploads/080ed361-f4f5-4be6-b134-99d53ecbd19a.pdf",
    [string]$OutputJsonl = "data/results/document_mind_parse_benchmark.jsonl",
    [string[]]$CandidateNames = @(),
    [string[]]$PageRanges = @(),
    [int]$TimeoutSeconds = 1800
)

$ErrorActionPreference = "Stop"

$candidates = @(
    @{ name = "p33-c4-probe1"; pages = "33"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "1" },
    @{ name = "p33-c4-no-llm-layout-fixed"; pages = "33"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "1"; llmEnhancement = "false"; outputFormat = "markdown,visualLayoutInfo"; weighted = "false"; heavyFirst = "false" },
    @{ name = "p33-c4-no-llm-layout-weighted"; pages = "33"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "1"; llmEnhancement = "false"; outputFormat = "markdown,visualLayoutInfo"; weighted = "true"; heavyFirst = "true" },
    @{ name = "p33-c4-no-llm-layout"; pages = "33"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "1"; llmEnhancement = "false"; outputFormat = "markdown,visualLayoutInfo" },
    @{ name = "p33-c4-no-llm-markdown"; pages = "33"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "1"; llmEnhancement = "false"; outputFormat = "markdown" },
    @{ name = "p33-c4-llm-markdown"; pages = "33"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "1"; llmEnhancement = "true"; outputFormat = "markdown" },
    @{ name = "p33-c4-hedge90"; pages = "33"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "1"; hedge = "true"; hedgeAfter = "90"; hedgeExtra = "1" },
    @{ name = "p33-c4-hedge75"; pages = "33"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "1"; hedge = "true"; hedgeAfter = "75"; hedgeExtra = "1" },
    @{ name = "p33-c4-probe2"; pages = "33"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "2" },
    @{ name = "p33-c4-probe3"; pages = "33"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "3" },
    @{ name = "p33-c4"; pages = "33"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "1" },
    @{ name = "p30-c4"; pages = "30"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "1" },
    @{ name = "p36-c4"; pages = "36"; concurrency = "4"; inflight = "1"; waves = "2"; probe = "1" },
    @{ name = "p33-c5"; pages = "33"; concurrency = "5"; inflight = "1"; waves = "2"; probe = "1" },
    @{ name = "p33-c6"; pages = "33"; concurrency = "6"; inflight = "1"; waves = "2"; probe = "1" }
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
        throw "No benchmark candidates matched: $($CandidateNames -join ', ')"
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputJsonl) | Out-Null
$pageRangesText = (($PageRanges | ForEach-Object { $_ -split '[,;\s]+' }) | Where-Object { $_ }) -join ","

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
        ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT = if ($Candidate.ContainsKey("outputFormat")) { $Candidate.outputFormat } else { "markdown,visualLayoutInfo" }
        ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT = if ($Candidate.ContainsKey("llmEnhancement")) { $Candidate.llmEnhancement } else { "true" }
        ALIYUN_DOCUMENT_MIND_ENHANCEMENT_MODE = if ($Candidate.ContainsKey("enhancementMode")) { $Candidate.enhancementMode } else { "VLM" }
        ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED = if ($Candidate.ContainsKey("hedge")) { $Candidate.hedge } else { "false" }
        ALIYUN_DOCUMENT_MIND_HEDGE_AFTER_SECONDS = if ($Candidate.ContainsKey("hedgeAfter")) { $Candidate.hedgeAfter } else { "90" }
        ALIYUN_DOCUMENT_MIND_HEDGE_MAX_EXTRA_ATTEMPTS = if ($Candidate.ContainsKey("hedgeExtra")) { $Candidate.hedgeExtra } else { "1" }
        ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED = if ($Candidate.ContainsKey("weighted")) { $Candidate.weighted } else { "false" }
        ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST = if ($Candidate.ContainsKey("heavyFirst")) { $Candidate.heavyFirst } else { "false" }
    }

    Invoke-RestMethod `
        -Uri "$ApiBase/api/console/settings" `
        -Method Put `
        -ContentType "application/json; charset=utf-8" `
        -Body ($payload | ConvertTo-Json -Depth 4) `
        -TimeoutSec 30 | Out-Null
}

function Invoke-ParseInContainer {
    param([string]$CandidateName)

    $python = @"
import json
import os
import time
from core.config import load_project_env

load_project_env()

from core.parser.provider import get_pdf_parser_provider, parse_pdf
from core.parser.document_mind_parser import get_last_document_mind_key_pool_metrics
from collections import Counter

pdf_path = os.environ.get("BENCH_PDF_PATH", "$PdfPath")
candidate = os.environ.get("BENCH_CANDIDATE", "$CandidateName")
page_ranges_text = os.environ.get("BENCH_PAGE_RANGES", "").strip()
events = []

def log(message):
    if len(events) < 2000:
        events.append(str(message))

def parse_page_ranges(text):
    import re

    ranges = []
    for part in re.split(r"[,;\s]+", text or ""):
        part = part.strip()
        if not part:
            continue
        match = re.match(r"^(\d+)(?:-(\d+))?$", part)
        if not match:
            raise ValueError(f"Invalid page range: {part}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise ValueError(f"Invalid page range: {part}")
        ranges.append((start, end))
    return ranges

def build_canary_pdf(source_pdf_path, page_ranges, candidate_name):
    if not page_ranges:
        return source_pdf_path, []

    import re
    import time
    from pathlib import Path
    from core.parser.pdf_sharding import import_fitz

    fitz = import_fitz()
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate_name)[:80] or "candidate"
    out_dir = Path("/app/data/output/document_mind_canary")
    out_dir.mkdir(parents=True, exist_ok=True)
    canary_path = out_dir / f"{safe_name}_{int(time.time() * 1000)}.pdf"
    selected = []

    source = fitz.open(str(source_pdf_path))
    target = fitz.open()
    try:
        page_count = int(source.page_count)
        inserted_pages = set()
        for start_page, end_page in sorted(page_ranges):
            start_idx = max(0, start_page - 1)
            end_idx = min(page_count, end_page) - 1
            if start_idx >= page_count or end_idx < start_idx:
                continue
            pending_start = None
            previous_idx = None
            for page_idx in range(start_idx, end_idx + 1):
                if page_idx in inserted_pages:
                    if pending_start is not None and previous_idx is not None:
                        target.insert_pdf(source, from_page=pending_start, to_page=previous_idx)
                        selected.append(f"P{pending_start + 1}-{previous_idx + 1}")
                        pending_start = None
                    previous_idx = None
                    continue
                inserted_pages.add(page_idx)
                if pending_start is None:
                    pending_start = page_idx
                previous_idx = page_idx
            if pending_start is not None and previous_idx is not None:
                target.insert_pdf(source, from_page=pending_start, to_page=previous_idx)
                selected.append(f"P{pending_start + 1}-{previous_idx + 1}")
        if int(target.page_count) <= 0:
            raise ValueError(f"No valid canary pages selected from {page_ranges}")
        target.save(str(canary_path), garbage=4, deflate=True)
    finally:
        target.close()
        source.close()

    return str(canary_path), selected

source_pdf_path = pdf_path
selected_page_ranges = parse_page_ranges(page_ranges_text)
pdf_path, selected_page_ranges_display = build_canary_pdf(
    source_pdf_path,
    selected_page_ranges,
    candidate,
)

start = time.monotonic()
provider = get_pdf_parser_provider()
blocks = parse_pdf(pdf_path, output_dir="/app/data/output", log_fn=log, original_name="benchmark.pdf")
elapsed_ms = int((time.monotonic() - start) * 1000)
metrics = dict(get_last_document_mind_key_pool_metrics())
type_counts = Counter(str(getattr(block, "type", "")).split(".")[-1].lower() for block in blocks)
text_chars = sum(len(getattr(block, "text", "") or "") for block in blocks)
image_blocks = sum(1 for block in blocks if str(getattr(block, "type", "")).lower().endswith("image") or getattr(block, "image_path", None))
table_blocks = sum(1 for block in blocks if getattr(block, "is_table", False) or str(getattr(block, "type", "")).lower().endswith("table"))
pages = [int(getattr(block, "page_idx", 0) or 0) for block in blocks]
metrics["provider"] = provider
metrics["parseWallMs"] = elapsed_ms
metrics["outputBlocks"] = len(blocks)
metrics["outputTextChars"] = text_chars
metrics["outputImageBlocks"] = image_blocks
metrics["outputTableBlocks"] = table_blocks
metrics["outputTitleBlocks"] = int(type_counts.get("title", 0))
metrics["outputTextBlocks"] = int(type_counts.get("text", 0))
metrics["outputPageMin"] = min(pages) + 1 if pages else 0
metrics["outputPageMax"] = max(pages) + 1 if pages else 0
metrics["candidate"] = candidate
metrics["pdfPath"] = pdf_path
metrics["sourcePdfPath"] = source_pdf_path
metrics["canaryPageRanges"] = selected_page_ranges_display

print(json.dumps({
    "candidate": candidate,
    "ok": True,
    "metrics": metrics,
    "eventTail": events[-40:],
}, ensure_ascii=False))
"@

    $envArgs = @(
        "-e", "BENCH_PDF_PATH=$PdfPath",
        "-e", "BENCH_CANDIDATE=$CandidateName"
    )
    if ($pageRangesText) {
        $envArgs += @("-e", "BENCH_PAGE_RANGES=$pageRangesText")
    }
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($python))
    $runner = "import base64; exec(base64.b64decode('$encoded').decode('utf-8'))"
    $output = & docker exec @envArgs $Container python -c $runner 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "docker exec failed with exit code ${LASTEXITCODE}: $($output -join "`n")"
    }
    $jsonLine = ($output | Select-Object -Last 1)
    if (-not $jsonLine) {
        throw "docker exec did not return a JSON result"
    }
    return $jsonLine | ConvertFrom-Json
}

Write-Host "Document Mind parse benchmark started: $PdfPath"
if ($pageRangesText) {
    Write-Host "Canary page ranges: $pageRangesText"
}

foreach ($candidate in $candidates) {
    $name = [string]$candidate.name
    $llm = if ($candidate.ContainsKey("llmEnhancement")) { $candidate.llmEnhancement } else { "true" }
    $format = if ($candidate.ContainsKey("outputFormat")) { $candidate.outputFormat } else { "markdown,visualLayoutInfo" }
    $weighted = if ($candidate.ContainsKey("weighted")) { $candidate.weighted } else { "false" }
    $heavyFirst = if ($candidate.ContainsKey("heavyFirst")) { $candidate.heavyFirst } else { "false" }
    Write-Host "==> $name pages=$($candidate.pages) concurrency=$($candidate.concurrency) inflight=$($candidate.inflight) probe=$($candidate.probe) llm=$llm output=$format weighted=$weighted heavyFirst=$heavyFirst"

    $startedAt = (Get-Date).ToString("o")
    try {
        Invoke-SettingsUpdate -Candidate $candidate
        $result = Invoke-ParseInContainer -CandidateName $name
        $record = [ordered]@{
            startedAt = $startedAt
            finishedAt = (Get-Date).ToString("o")
            candidate = $name
            settings = $candidate
            ok = $true
            metrics = $result.metrics
            eventTail = $result.eventTail
        }
    }
    catch {
        $errorText = ($_ | Out-String).Trim()
        if (-not $errorText) {
            $errorText = $_.Exception.Message
        }
        $record = [ordered]@{
            startedAt = $startedAt
            finishedAt = (Get-Date).ToString("o")
            candidate = $name
            settings = $candidate
            ok = $false
            error = $errorText
        }
    }

    $line = $record | ConvertTo-Json -Depth 20 -Compress
    Add-Content -LiteralPath $OutputJsonl -Value $line -Encoding UTF8
    Write-Host $line
}

Write-Host "Benchmark records written to $OutputJsonl"
