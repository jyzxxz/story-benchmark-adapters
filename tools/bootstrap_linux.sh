#!/usr/bin/env bash
# Install isolated dependencies. No models are called and no source snapshots are edited.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_SYSTEM=all
TASK_SKIP_APT=0
while (($#)); do
  case "$1" in
    --system) TASK_SYSTEM="${2:?missing system}"; shift 2 ;;
    --skip-system-deps) TASK_SKIP_APT=1; shift ;;
    --help|-h) echo 'Usage: bash tools/bootstrap_linux.sh [--system all|if_line|ai4visualnovel|infiplot] [--skip-system-deps]'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$TASK_SYSTEM" in all|if_line|ai4visualnovel|infiplot) ;; *) echo 'Invalid system' >&2; exit 2 ;; esac
if [[ "$(uname -s)" != Linux ]]; then echo 'Run inside Ubuntu 24.04 (Windows: WSL2 Ubuntu).'; exit 2; fi
if [[ "$EUID" == 0 ]]; then echo 'Run as a regular user with sudo access. PostgreSQL cannot initialize as root.'; exit 2; fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 ]]; then
  echo 'This installer targets Ubuntu 24.04; use that release for the documented environment.' >&2; exit 2
fi
if [[ "$TASK_SKIP_APT" == 0 ]]; then
  sudo apt-get update
  sudo apt-get install -y python3.12 python3.12-venv python3-dev build-essential curl ca-certificates xz-utils git \
    fontconfig fonts-noto-cjk postgresql-16 redis-server
fi
mkdir -p "$TASK_ROOT/work/installation" "$TASK_ROOT/work/envs" "$TASK_ROOT/work/models"
cd "$TASK_ROOT"
python3.12 -m venv work/envs/common
work/envs/common/bin/python -m pip install --disable-pip-version-check 'Pillow==12.3.0' 'python-dotenv==1.2.3' 'pytest==8.4.2' 'jsonschema==4.26.0' 'httpx==0.25.2' -c tools/linux/common-py312.constraints.txt
# Official Node binary, verify the exact architecture archive against the release checksums.
TASK_NODE_VERSION=22.12.0
case "$(uname -m)" in x86_64) TASK_ARCH=x64 ;; aarch64|arm64) TASK_ARCH=arm64 ;; *) echo 'Unsupported CPU'; exit 2 ;; esac
TASK_ARCHIVE="node-v${TASK_NODE_VERSION}-linux-${TASK_ARCH}.tar.xz"
if [[ ! -x work/node/bin/node ]] || [[ "$(work/node/bin/node --version)" != "v${TASK_NODE_VERSION}" ]]; then
  curl --fail --location --retry 3 "https://nodejs.org/dist/v${TASK_NODE_VERSION}/${TASK_ARCHIVE}" -o "work/installation/$TASK_ARCHIVE"
  curl --fail --location --retry 3 "https://nodejs.org/dist/v${TASK_NODE_VERSION}/SHASUMS256.txt" -o work/installation/node-SHASUMS256.txt
  (cd work/installation; awk -v file="$TASK_ARCHIVE" '$2 == file {print}' node-SHASUMS256.txt > node-selected.sha256; test -s node-selected.sha256; sha256sum -c node-selected.sha256)
  mkdir -p work/node
  tar -xJf "work/installation/$TASK_ARCHIVE" --strip-components=1 -C work/node
fi
export PATH="$TASK_ROOT/work/node/bin:$PATH"
export PLAYWRIGHT_BROWSERS_PATH="$TASK_ROOT/work/browser-cache"
npm install --global --prefix "$TASK_ROOT/work/node" pnpm@9.12.0
mkdir -p work/media-tools
cp benchmark/native_shims/if_line/renderer-package.json work/media-tools/package.json
cp tools/linux/renderer-package-lock.json work/media-tools/package-lock.json
npm ci --prefix work/media-tools --no-audit --no-fund
if [[ "$TASK_SKIP_APT" == 0 ]]; then
  # Runs apt via sudo; browser binaries themselves are installed below as the regular user.
  work/node/bin/node work/media-tools/node_modules/playwright/cli.js install-deps chromium
fi
work/node/bin/node work/media-tools/node_modules/playwright/cli.js install chromium
if [[ "$TASK_SYSTEM" == all || "$TASK_SYSTEM" == if_line ]]; then
  python3.12 -m venv work/envs/if_line
  work/envs/if_line/bin/python -m pip install --disable-pip-version-check -r benchmark/native_shims/if_line/requirements-media.txt -c tools/linux/if_line-py312.constraints.txt
  mkdir -p work/models/if_line
  U2NET_HOME="$TASK_ROOT/work/models/if_line" work/envs/if_line/bin/python -c "from rembg import new_session; new_session('u2net', providers=['CPUExecutionProvider'])"
  work/envs/if_line/bin/python -m pip freeze > work/installation/if_line-pip-freeze.txt
fi
if [[ "$TASK_SYSTEM" == all || "$TASK_SYSTEM" == ai4visualnovel ]]; then
  python3.12 -m venv work/envs/ai4visualnovel
  work/envs/ai4visualnovel/bin/python -m pip install --disable-pip-version-check -r systems/AI4VisualNovel/requirements.txt 'rembg[cpu]==2.0.69' -c tools/linux/ai4visualnovel-py312.constraints.txt
  mkdir -p work/models/ai4visualnovel
  U2NET_HOME="$TASK_ROOT/work/models/ai4visualnovel" work/envs/ai4visualnovel/bin/python -c "from rembg import new_session; new_session('isnet-anime', providers=['CPUExecutionProvider'])"
  TASK_FONT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/fonts/story-benchmark"
  mkdir -p "$TASK_FONT_DIR"
  cp systems/AI4VisualNovel/SourceHanSansCN-Regular.otf "$TASK_FONT_DIR/"
  fc-cache -f "$TASK_FONT_DIR"
  work/envs/ai4visualnovel/bin/python -m pip freeze > work/installation/ai4visualnovel-pip-freeze.txt
fi
if [[ "$TASK_SYSTEM" == all || "$TASK_SYSTEM" == infiplot ]]; then
  mkdir -p work/infiplot-deps
  cp systems/infiplot/package.json systems/infiplot/pnpm-lock.yaml work/infiplot-deps/
  pnpm --dir work/infiplot-deps install --frozen-lockfile
  # Each run installs into a fresh copy using the cache, so test that same operation.
  TASK_PROBE="$(mktemp -d "$TASK_ROOT/work/infiplot-offline-probe.XXXXXX")"
  cp systems/infiplot/package.json systems/infiplot/pnpm-lock.yaml "$TASK_PROBE/"
  pnpm --dir "$TASK_PROBE" install --offline --frozen-lockfile
  rm -rf -- "$TASK_PROBE"
fi
work/envs/common/bin/python -m pip freeze > work/installation/common-pip-freeze.txt
# Resolve this file's own location when sourced, so a copied checkout has no stale Mac paths.
cat > work/activate.sh <<'ACTIVATE'
TASK_BENCH_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$TASK_BENCH_ROOT/work/envs/common/bin:$TASK_BENCH_ROOT/work/node/bin:$PATH"
export PLAYWRIGHT_BROWSERS_PATH="$TASK_BENCH_ROOT/work/browser-cache"
unset TASK_BENCH_ROOT
ACTIVATE
work/envs/common/bin/python tools/verify_sources.py > work/installation/source-verification.json
printf 'Installation complete. Next: source work/activate.sh; python3 tools/configure_batch.py; python3 tools/doctor.py --system %s\n' "$TASK_SYSTEM"
