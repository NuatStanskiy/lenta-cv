docker network create price-net 2>$null

if (-not $env:HF_TOKEN) {
  Write-Host "HF_TOKEN is not set. Public models may still download, but HF may rate-limit you."
}

New-Item -ItemType Directory -Force -Path "$HOME\.cache\huggingface" | Out-Null

docker rm -f qwen-sglang 2>$null | Out-Null

docker run -d `
  --name qwen-sglang `
  --network price-net `
  --gpus all `
  --shm-size 32g `
  --ipc=host `
  -p 30000:30000 `
  -v ${HOME}\.cache\huggingface:/root/.cache/huggingface `
  -e HF_TOKEN=$env:HF_TOKEN `
  lmsysorg/sglang:latest `
  python3 -m sglang.launch_server `
    --model-path Vishva007/Qwen3-VL-8B-Instruct-W4A16-AutoRound-AWQ `
    --host 0.0.0.0 `
    --port 30000 `
    --trust-remote-code `
    --grammar-backend xgrammar `
    --context-length 4096 `
    --mem-fraction-static 0.80

Write-Host ""
Write-Host "Container started. Stream logs with:"
Write-Host "    docker logs -f qwen-sglang"
Write-Host "Health check (after model loads, ~5-15 min on first run):"
Write-Host "    curl http://localhost:30000/health"
