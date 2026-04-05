# Air-Gapped Deployment Guide

Deploy AgenticOrg in environments with no internet access using local LLMs (Ollama + vLLM).

## Prerequisites

- Docker 24+ and Docker Compose v2
- Kubernetes 1.28+ with Helm 3 (for Helm deployment)
- Minimum 32 GB RAM (64 GB recommended for Tier 3 models)
- GPU with 24+ GB VRAM (optional, for vLLM / Tier 3 acceleration)
- All container images pre-pulled and loaded into a local registry

## Model Tiers

| Tier | Model | RAM | Use Case |
|------|-------|-----|----------|
| Tier 1 | gemma3:7b | 8 GB | Data lookups, formatting, simple Q&A |
| Tier 2 | llama3.1:8b | 10 GB | Analysis, summarization, multi-step reasoning |
| Tier 3 | llama3.1:70b | 48 GB | Legal analysis, financial modeling, complex tasks |

## Option A: Docker Compose

### 1. Pull and save models on an internet-connected machine

```bash
# Pull Ollama models
ollama pull gemma3:7b
ollama pull llama3.1:8b
ollama pull llama3.1:70b

# Export the model directory (~/.ollama/models) to a tarball
tar -czf ollama-models.tar.gz -C ~/.ollama models
```

### 2. Transfer to air-gapped host

Copy `ollama-models.tar.gz` and all Docker images to the target machine.

```bash
# Load Docker images
docker load < agenticorg-api.tar
docker load < agenticorg-ui.tar
docker load < ollama.tar
docker load < pgvector.tar
docker load < redis.tar
docker load < minio.tar

# Extract Ollama models
mkdir -p /opt/ollama/models
tar -xzf ollama-models.tar.gz -C /opt/ollama
```

### 3. Configure environment

```bash
export AGENTICORG_LLM_MODE=local
export OLLAMA_BASE_URL=http://ollama:11434
export AGENTICORG_LOCAL_TIER1=gemma3:7b
export AGENTICORG_LOCAL_TIER2=llama3.1:8b
export AGENTICORG_LOCAL_TIER3=llama3.1:70b
```

### 4. Start services

```bash
# Start core + Ollama (CPU only)
docker compose --profile airgap up -d

# Or with GPU support for vLLM
docker compose --profile airgap --profile airgap-gpu up -d
```

### 5. Verify

```bash
# Check Ollama is responding
curl http://localhost:11434/api/tags

# Check AgenticOrg API
curl http://localhost:8000/api/v1/health

# Test LLM routing
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "agent_type": "general"}'
```

## Option B: Helm (Kubernetes)

### 1. Pre-load images into internal registry

```bash
# Tag and push all images to your internal registry
REGISTRY=registry.internal.local

for img in agenticorg/api agenticorg/ui ollama/ollama pgvector/pgvector:pg16 redis:7-alpine minio/minio; do
  docker tag $img $REGISTRY/$img
  docker push $REGISTRY/$img
done
```

### 2. Pre-load Ollama models

Create a PersistentVolume with the Ollama models directory, or use an init container:

```yaml
# Example init container to copy models from a hostPath
initContainers:
  - name: load-models
    image: registry.internal.local/busybox
    command: ["cp", "-r", "/host-models/", "/ollama-models/"]
    volumeMounts:
      - name: ollama-models
        mountPath: /ollama-models
      - name: host-models
        mountPath: /host-models
```

### 3. Deploy

```bash
helm install agenticorg ./helm -f helm/values-airgap.yaml
```

### 4. Verify

```bash
# Check pods
kubectl get pods -l app=agenticorg

# Check Ollama service
kubectl exec -it deploy/ollama -- curl http://localhost:11434/api/tags

# Port-forward and test API
kubectl port-forward svc/agenticorg-api 8000:8000
curl http://localhost:8000/api/v1/health
```

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTICORG_LLM_MODE` | `cloud` | `cloud`, `local`, or `auto` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `VLLM_BASE_URL` | `http://localhost:8000` | vLLM server URL |
| `VLLM_API_KEY` | `vllm` | vLLM API key (if configured) |
| `AGENTICORG_LOCAL_TIER1` | `gemma3:7b` | Model for simple tasks |
| `AGENTICORG_LOCAL_TIER2` | `llama3.1:8b` | Model for moderate tasks |
| `AGENTICORG_LOCAL_TIER3` | `llama3.1:70b` | Model for complex tasks |

## Troubleshooting

**Ollama not responding**: Check the service is running and models are loaded with `ollama list`.

**Out of memory**: Tier 3 models require 48+ GB RAM. Use Tier 1/2 only, or add GPU with vLLM.

**Slow inference**: Enable GPU passthrough in Docker (`--gpus all`) or use vLLM with tensor parallelism.

**Model not found**: Ensure models were pulled on the internet-connected machine and transferred correctly. Run `ollama list` inside the container to verify.
