#!/usr/bin/env bash
# =================================================================
# Build Offline Deployment Bundle
# =================================================================
# 在有外网的环境运行此脚本,打包所有依赖到 offline-bundle.tar.gz
# 然后把整个 tarball 拷到内网环境,解压后即可离线部署
# =================================================================
set -euo pipefail

OUT_DIR="offline-bundle"
mkdir -p "$OUT_DIR"

echo "========================================="
echo "Capacity Agent - Offline Bundle Builder"
echo "========================================="

# 1. Pull docker images
echo "[1/5] Pulling docker images..."
IMAGES=(
    "postgres:16"
    "clickhouse/clickhouse-server:24.3"
    "vllm/vllm-openai:v0.6.3"
    "prom/prometheus:v2.55.0"
    "grafana/grafana:11.3.0"
    "python:3.11-slim"
)
for img in "${IMAGES[@]}"; do
    echo "  pulling $img..."
    docker pull "$img"
done

# 2. Save images to tar
echo "[2/5] Saving images to tar..."
docker save "${IMAGES[@]}" -o "$OUT_DIR/docker-images.tar"

# 3. Build local images
echo "[3/5] Building local app/engine images..."
docker compose -f docker/docker-compose.yml build engine agent
docker save \
    capacity_agent-engine:latest \
    capacity_agent-agent:latest \
    -o "$OUT_DIR/local-images.tar"

# 4. Download HuggingFace model (Qwen2.5-32B-Instruct)
echo "[4/5] Downloading LLM model weights..."
mkdir -p "$OUT_DIR/models"
pip install -U "huggingface_hub[cli]"
huggingface-cli download Qwen/Qwen2.5-32B-Instruct \
    --local-dir "$OUT_DIR/models/Qwen2.5-32B-Instruct" \
    --local-dir-use-symlinks False

# 5. Copy code
echo "[5/5] Copying source code..."
cp -r ../app ../engines ../data ../configs ../docker ../scripts ../README.md "$OUT_DIR/"

# 6. Create install instructions
cat > "$OUT_DIR/INSTALL.md" << 'EOF'
# Air-gap Install Instructions

1. Load docker images:
   docker load -i docker-images.tar
   docker load -i local-images.tar

2. Place model into vllm volume:
   mkdir -p /var/lib/docker/volumes/capacity_agent_vllm_models/_data
   cp -r models/Qwen2.5-32B-Instruct \
         /var/lib/docker/volumes/capacity_agent_vllm_models/_data/

3. Start services:
   cd docker && docker compose up -d

4. Verify:
   curl http://localhost:8000/health
   curl http://localhost:8001/health
   curl http://localhost:8002/health

EOF

# 7. Final tarball
echo "Creating final tarball..."
tar czf offline-bundle.tar.gz "$OUT_DIR/"
echo ""
echo "Done! Bundle at: $(pwd)/offline-bundle.tar.gz"
echo "Size: $(du -h offline-bundle.tar.gz | cut -f1)"
echo ""
echo "Transfer to air-gap server and follow $OUT_DIR/INSTALL.md"
