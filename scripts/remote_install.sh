#!/usr/bin/env bash
set -euo pipefail

export PATH=/root/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
source /root/autodl-tmp/ai_interview/env.sh
readonly ROOT="$AI_STUDIO_ROOT"
readonly APPS="$ROOT/apps"
readonly ENVS="$ROOT/envs"
export PIP_CACHE_DIR="$ROOT/cache/pip"
# AutoDL's direct PyPI route was repeatedly timing out during IndexTTS's large
# CUDA dependency set.  Keep the lockfile authoritative but fetch its exact
# artifacts from the domestic mirror.  The cache survives retries and resumes.
export UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX:-https://mirrors.aliyun.com/pypi/simple}"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple}"
export UV_HTTP_TIMEOUT=300
export UV_HTTP_RETRIES=10
install -d "$ENVS" "$PIP_CACHE_DIR"

enable_model_network() {
  if [[ -r /etc/network_turbo ]]; then source /etc/network_turbo >/dev/null; fi
}

disable_model_network() {
  unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY REQUESTS_CA_BUNDLE SSL_CERT_FILE
}

free_gib() {
  df -Pk "$ROOT" | awk 'NR==2 {printf "%d", $4/1024/1024}'
}

require_free_gib() {
  local required=$1 available
  available=$(free_gib)
  if (( available < required )); then
    echo "disk guard: ${available}GiB free; ${required}GiB required before this stage" >&2
    exit 28
  fi
}

# The AutoDL image's CUDA 13/PyTorch base is the isolated comfy-h3 runtime.
disable_model_network
/root/miniconda3/bin/python -m pip install -U pip uv 'huggingface_hub[cli]'
if compgen -G "$ROOT/cache/wheelhouse/comfyui_workflow_templates-0.11.68-*.whl" >/dev/null; then
  /root/miniconda3/bin/python -m pip install --no-index \
    --find-links "$ROOT/cache/wheelhouse" comfyui-workflow-templates==0.11.68
else
  enable_model_network
  /root/miniconda3/bin/python -m pip install --index-url https://pypi.org/simple \
    comfyui-workflow-templates==0.11.68
  disable_model_network
fi
/root/miniconda3/bin/python -m pip install -r "$APPS/ComfyUI/requirements.txt"
/root/miniconda3/bin/python -m pip install -r "$APPS/ComfyUI/custom_nodes/comfyui_seedvr2/requirements.txt"

# IndexTTS has an official Python <3.12 and PyTorch 2.8/cu128 lock. The high
# first-create headroom is unnecessary when its existing virtualenv is resumed.
if [[ ! -x "$APPS/index-tts/.venv/bin/python" ]]; then
  require_free_gib 42
fi
cd "$APPS/index-tts"
/root/miniconda3/bin/uv sync --extra webui --no-dev --default-index "$UV_DEFAULT_INDEX"

# Qwen3-TTS stays isolated while reusing the CUDA-ready image PyTorch.
if [[ ! -x "$ENVS/qwen3-tts/bin/python" ]]; then
  /root/miniconda3/bin/python -m venv --system-site-packages "$ENVS/qwen3-tts"
fi
"$ENVS/qwen3-tts/bin/python" -m pip install -U pip
"$ENVS/qwen3-tts/bin/python" -m pip install -e "$APPS/Qwen3-TTS"

# LatentSync is deliberately isolated because it pins an older CUDA/PyTorch stack.
if [[ ! -x "$ENVS/latentsync/bin/python" ]]; then
  require_free_gib 32
  /root/miniconda3/bin/conda create -y -p "$ENVS/latentsync" python=3.10.13
fi
"$ENVS/latentsync/bin/python" -m pip install -U pip
# insightface 0.7.3's PEP 517 isolated build can deadlock on the 2GB no-card
# instance.  Its build requirements are installed in this dedicated venv first,
# then the package is built without a nested isolated resolver.
"$ENVS/latentsync/bin/python" -m pip install --prefer-binary setuptools cython "numpy==1.26.4"
"$ENVS/latentsync/bin/python" -m pip install --no-build-isolation "insightface==0.7.3"
for wheel in \
  "$ROOT/cache/wheelhouse"/torch-*.whl \
  "$ROOT/cache/wheelhouse"/torchvision-*.whl \
  "$ROOT/cache/wheelhouse"/nvidia_*.whl \
  "$ROOT/cache/wheelhouse"/onnxruntime_gpu-*.whl \
  "$ROOT/cache/wheelhouse"/triton-*.whl; do
  [[ -e "$wheel" ]] || continue
  "$ENVS/latentsync/bin/python" -m pip install --no-index --no-deps "$wheel"
done
if ! "$ENVS/latentsync/bin/python" -c 'import torch' >/dev/null 2>&1; then
  "$ENVS/latentsync/bin/python" -m pip install --no-deps \
    --index-url https://download.pytorch.org/whl/cu121 \
    torch==2.5.1 torchvision==0.20.1
fi
if ! "$ENVS/latentsync/bin/python" -c 'import importlib.metadata as m; [m.version(p) for p in ("nvidia-cuda-cupti-cu12", "nvidia-cuda-nvrtc-cu12", "nvidia-cuda-runtime-cu12")]' >/dev/null 2>&1; then
  "$ENVS/latentsync/bin/python" -m pip install --no-deps \
    --index-url https://download.pytorch.org/whl/cu121 \
    nvidia-cuda-cupti-cu12==12.1.105 \
    nvidia-cuda-nvrtc-cu12==12.1.105 \
    nvidia-cuda-runtime-cu12==12.1.105
fi
for package in \
  'accelerate==0.26.1' \
  'lpips==0.1.4' \
  'face-alignment==1.4.1' \
  'kornia==0.8.0' \
  'DeepCache==0.1.1' \
  'mediapipe==0.10.11' \
  'psutil==7.2.2' \
  'kornia-rs==0.1.14' \
  'absl-py==2.5.0' \
  'attrs==26.1.0' \
  'jax==0.6.2' \
  'jaxlib==0.6.2' \
  'opencv-contrib-python==4.9.0.80' \
  'sounddevice==0.5.6' \
  'protobuf==3.20.3' \
  'sympy==1.13.1' \
  'opt_einsum==3.4.0' \
  'ml-dtypes==0.5.4'; do
  "$ENVS/latentsync/bin/python" -m pip install --find-links "$ROOT/cache/wheelhouse" --no-deps "$package"
done
LATENTSYNC_CONSTRAINTS="$ROOT/cache/latentsync-constraints.txt"
printf '%s\n' 'opencv-python-headless==4.9.0.80' > "$LATENTSYNC_CONSTRAINTS"
printf '%s\n' 'opencv-contrib-python==4.9.0.80' >> "$LATENTSYNC_CONSTRAINTS"
printf '%s\n' 'onnx==1.16.1' >> "$LATENTSYNC_CONSTRAINTS"
printf '%s\n' 'scikit-image==0.22.0' >> "$LATENTSYNC_CONSTRAINTS"
LATENTSYNC_REQUIREMENTS="$ROOT/cache/latentsync-requirements.txt"
sed -e '/^torch==/d' \
  -e '/^torchvision==/d' \
  -e '/^accelerate==/d' \
  -e '/^lpips==/d' \
  -e '/^face-alignment==/d' \
  -e '/^kornia==/d' \
  -e '/^DeepCache==/d' \
  -e '/^mediapipe==/d' \
  -e '/^--extra-index-url/d' \
  "$APPS/LatentSync/requirements.txt" > "$LATENTSYNC_REQUIREMENTS"
"$ENVS/latentsync/bin/python" -m pip install --find-links "$ROOT/cache/wheelhouse" \
  --constraint "$LATENTSYNC_CONSTRAINTS" \
  -r "$LATENTSYNC_REQUIREMENTS"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y libgl1
rm -rf /var/lib/apt/lists/*

"$ROOT/scripts/remote_inventory.py" >/dev/null
echo "Remote environments installed; run remote_deploy_models.sh for direct runtime-model deployment"
