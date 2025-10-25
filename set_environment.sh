#!/usr/bin/env bash
set -euo pipefail

# set_environment.sh
# Creates/activates a conda env (installs Miniconda if conda is missing), installs Python deps, builds the MCP server, updates config,
# and validates the environment.
#
# Usage:
#   ./set_environment.sh <env_name> [--python-exec <path>] [--python-path <paths>] [--kicad-cli <path>]
#
# Notes:
# - If conda is not found, this script installs Miniconda silently to $HOME/miniconda3 and initializes it for the current shell.
# - --python-exec should point to the KiCad Python interpreter if pcbnew isn't importable from your default Python.
# - --python-path can include the KiCad site-packages directory if needed (use ':' on Linux). --kicad-cli sets KICAD_CLI for exports.

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
CONFIG_FILE="$ROOT_DIR/config/default-config.json"
REQ_FILE="$ROOT_DIR/python/requirements.txt"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <env_name> [--python-exec <path>] [--python-path <paths>] [--kicad-cli <path>]" >&2
  exit 1
fi

ENV_NAME="$1"; shift || true
PY_EXEC_OVERRIDE=""; PY_PATH_OVERRIDE=""; KICAD_CLI_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python-exec)
      PY_EXEC_OVERRIDE="${2:-}"; shift 2;;
    --python-path)
      PY_PATH_OVERRIDE="${2:-}"; shift 2;;
    --kicad-cli)
      KICAD_CLI_OVERRIDE="${2:-}"; shift 2;;
    *) echo "Unknown argument: $1" >&2; exit 1;;
  esac
done

# Ensure conda is available (find or install Miniconda if missing)
# Robust detection across common install locations
CONDA_BIN="$(command -v conda 2>/dev/null || true)"
for p in "$HOME/anaconda3/bin/conda" "$HOME/miniconda3/bin/conda" "$HOME/mambaforge/bin/conda" "$HOME/miniforge3/bin/conda" "/opt/conda/bin/conda"; do
  [ -n "$CONDA_BIN" ] || { [ -x "$p" ] && CONDA_BIN="$p"; }
done

if [[ -z "$CONDA_BIN" ]]; then
  echo "Conda not found. Installing Miniconda to $HOME/miniconda3 ..."
  OS="$(uname -s)"; ARCH="$(uname -m)"
  case "$OS" in
    Linux) PLATFORM="Linux" ;;
    Darwin) PLATFORM="MacOSX" ;;
    *) echo "Unsupported OS: $OS" >&2; exit 1 ;;
  esac
  case "$ARCH" in
    x86_64|amd64) ARCH_NAME="x86_64" ;;
    aarch64|arm64) ARCH_NAME="$( [[ "$PLATFORM" == "MacOSX" ]] && echo "arm64" || echo "aarch64" )" ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
  esac
  INSTALLER_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-${PLATFORM}-${ARCH_NAME}.sh"
  TMP_SH="/tmp/miniconda_installer.sh"
  echo "Downloading Miniconda from $INSTALLER_URL ..."
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$INSTALLER_URL" -o "$TMP_SH"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$INSTALLER_URL" -O "$TMP_SH"
  else
    echo "Neither curl nor wget is available to download Miniconda." >&2
    exit 1
  fi
  bash "$TMP_SH" -b -p "$HOME/miniconda3"
  rm -f "$TMP_SH"
  CONDA_BIN="$HOME/miniconda3/bin/conda"
fi

# Put discovered conda on PATH
export PATH="$(dirname "$CONDA_BIN"):$PATH"

# Initialize conda for current shell
# shellcheck disable=SC1091
BASE_DIR="$(dirname "$(dirname "$CONDA_BIN")")"
# If conda is callable, prefer its reported base
if command -v conda >/dev/null 2>&1; then
  BASE_DIR="$(conda info --base 2>/dev/null || echo "$BASE_DIR")"
fi
source "$BASE_DIR/etc/profile.d/conda.sh"

# Create env if missing and activate
if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "Conda env '$ENV_NAME' already exists. Activating..."
else
  echo "Creating conda env '$ENV_NAME' (python=3.10)..."
  conda create -y -n "$ENV_NAME" python=3.10
fi
conda activate "$ENV_NAME"
ACTIVE_ENV_DESC="conda:$ENV_NAME"
# Align Conda runtime with system KiCad binaries to avoid GLIBCXX/cffi mismatches
# - Newer libstdc++ provides GLIBCXX_3.4.30 required by pcbnew
# - cffi pinned to 1.15.x to match system _cffi_backend on many distros
conda install -y -c conda-forge "libstdcxx-ng>=12" "libgcc-ng>=12" "cffi==1.15.0" || true



# Ensure KiCad 9+ (and kicad-cli) is available; attempt install if missing or version < 9
ensure_kicad9() {
  local have_cli have_gui ver major
  have_cli=0; have_gui=0; ver=""; major=0
  if command -v kicad-cli >/dev/null 2>&1; then have_cli=1; fi
  if command -v kicad >/dev/null 2>&1; then have_gui=1; fi
  if [[ $have_cli -eq 1 ]]; then
    ver="$(kicad-cli --version 2>/dev/null | head -n1 | grep -oE '[0-9]+(\.[0-9]+)*' | head -n1 || true)"
  elif [[ $have_gui -eq 1 ]]; then
    ver="$(kicad --version 2>/dev/null | head -n1 | grep -oE '[0-9]+(\.[0-9]+)*' | head -n1 || true)"
  fi
  if [[ -n "$ver" ]]; then major="${ver%%.*}"; fi
  if [[ $have_cli -eq 1 && $major -ge 9 ]]; then
    return 0
  fi

  echo "Installing/upgrading KiCad to 9.x (requires sudo on Linux)..."
  if command -v apt-get >/dev/null 2>&1; then
    # Ubuntu/Debian: use official KiCad 9 releases PPA
    if [[ $EUID -ne 0 ]]; then SUDO="sudo"; else SUDO=""; fi
    $SUDO apt-get update -y || true
    $SUDO apt-get install -y software-properties-common gnupg || true
    $SUDO add-apt-repository --yes ppa:kicad/kicad-9.0-releases || true
    $SUDO apt-get update -y || true
    $SUDO apt-get install -y --install-recommends kicad || true
  elif command -v dnf >/dev/null 2>&1; then
    if [[ $EUID -ne 0 ]]; then SUDO="sudo"; else SUDO=""; fi
    $SUDO dnf install -y kicad || true
  elif command -v yum >/dev/null 2>&1; then
    if [[ $EUID -ne 0 ]]; then SUDO="sudo"; else SUDO=""; fi
    $SUDO yum install -y kicad || true
  elif command -v pacman >/dev/null 2>&1; then
    if [[ $EUID -ne 0 ]]; then SUDO="sudo"; else SUDO=""; fi
    $SUDO pacman -Sy --noconfirm kicad || true
  elif command -v brew >/dev/null 2>&1; then
    brew install --cask kicad || brew install kicad || true
  else
    echo "Unsupported or unknown package manager. Please install KiCad 9 (including kicad-cli) manually." >&2
  fi

  # Re-check after install
  if command -v kicad-cli >/dev/null 2>&1; then
    ver="$(kicad-cli --version 2>/dev/null | head -n1 | grep -oE '[0-9]+(\.[0-9]+)*' | head -n1 || true)"
    if [[ -n "$ver" ]]; then major="${ver%%.*}"; else major=0; fi
  else
    major=0
  fi
  if [[ $major -lt 9 ]]; then
    echo "KiCad version is < 9 or kicad-cli missing after install. Please ensure KiCad 9 repository is available on your distro." >&2
  fi
}

# Ensure npm (Node.js) is available; attempt user-level install via nvm if missing
ensure_npm() {
  if command -v npm >/dev/null 2>&1; then
    return 0
  fi
  echo "npm not found. Attempting to install Node.js (LTS) via nvm for current user..."

  export NVM_DIR="$HOME/.nvm"
  if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
    # install nvm
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    elif command -v wget >/dev/null 2>&1; then
      wget -qO- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    else
      echo "Neither curl nor wget available to install nvm; skipping Node install." >&2
      return 0
    fi
  fi
  # shellcheck disable=SC1090
  [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
  nvm install --lts || true
  nvm use --lts || true
}

# Attempt to ensure system-level dependencies
ensure_kicad9 || true
ensure_npm || true

# Capture kicad-cli if available
KICAD_CLI_BIN="$(command -v kicad-cli || true)"
if [[ -n "$KICAD_CLI_BIN" ]]; then
  export KICAD_CLI="$KICAD_CLI_BIN"
fi

# Setup KiCad symbol library configuration
setup_kicad_symbol_libs() {
  echo "Setting up KiCad symbol library configuration..."

  # Detect KiCad version
  local kicad_ver major_ver
  kicad_ver="$(kicad-cli --version 2>/dev/null | head -n1 | grep -oE '[0-9]+(\.[0-9]+)*' | head -n1 || echo "9.0")"
  major_ver="${kicad_ver%%.*}"

  # Auto-detect KiCad symbol directory
  local symbol_dir
  symbol_dir="$(dirname "$(find /usr/share -path '*/kicad/symbols/Device.kicad_sym' -print -quit 2>/dev/null)" 2>/dev/null || true)"

  # Fallback to common paths if auto-detection fails
  if [[ -z "$symbol_dir" ]]; then
    for candidate in "/usr/share/kicad/symbols" "/usr/local/share/kicad/symbols" "/opt/kicad/share/kicad/symbols"; do
      if [[ -d "$candidate" && -f "$candidate/Device.kicad_sym" ]]; then
        symbol_dir="$candidate"
        break
      fi
    done
  fi

  if [[ -z "$symbol_dir" ]]; then
    echo "WARNING: Could not locate KiCad symbol libraries. Skipping sym-lib-table setup." >&2
    return 0
  fi

  echo "Found KiCad symbols at: $symbol_dir"
  export KICAD${major_ver}_SYMBOL_DIR="$symbol_dir"

  # Create KiCad config directory
  local config_dir="$HOME/.config/kicad/${major_ver}.0"
  mkdir -p "$config_dir"

  # Generate sym-lib-table with all available libraries
  local sym_lib_table="$config_dir/sym-lib-table"
  echo "Generating sym-lib-table at: $sym_lib_table"

  {
    echo "(sym_lib_table"
    echo "  (version 7)"

    # Add all .kicad_sym files found in the symbol directory
    local lib_count=0
    while IFS= read -r lib_file; do
      local lib_name
      lib_name="$(basename "$lib_file" .kicad_sym)"
      echo "  (lib (name \"$lib_name\")(type \"KiCad\")(uri \"$lib_file\")(options \"\")(descr \"\"))"
      ((lib_count++))
    done < <(find "$symbol_dir" -maxdepth 1 -name "*.kicad_sym" -type f | sort)

    echo ")"
  } > "$sym_lib_table"

  echo "Successfully configured $lib_count symbol libraries in sym-lib-table"
}

# Run symbol library setup
setup_kicad_symbol_libs || true

# Detect KiCad pcbnew site-packages directory to add to pythonPath
DETECTED_KICAD_PY_PATH="$(/usr/bin/python3 - <<'PY'
try:
    import os, pcbnew
    print(os.path.dirname(pcbnew.__file__))
except Exception:
    print("")
PY
)"

# Detect KiCad shared data path (optional)
if [[ -z "${KICAD_PATH:-}" ]]; then
  if [[ -d "/usr/share/kicad" ]]; then
    export KICAD_PATH="/usr/share/kicad"
  fi
fi
# Choose default Python exec and pythonPath before installing deps
DEFAULT_PY_EXEC="$(command -v python || echo python3)"
PY_EXEC_TO_SET="${PY_EXEC_OVERRIDE:-$DEFAULT_PY_EXEC}"
PY_PATH_TO_SET="${PY_PATH_OVERRIDE:-${DETECTED_KICAD_PY_PATH:-}}"
KICAD_PATH_TO_SET="${KICAD_PATH:-}"
export PY_EXEC_TO_SET
export PY_PATH_TO_SET
export KICAD_PATH_TO_SET
if [[ -n "$KICAD_CLI_OVERRIDE" ]]; then
  export KICAD_CLI="$KICAD_CLI_OVERRIDE"
fi

# If we are in a conda env and we detected a system KiCad Python path,
# add it permanently to the environment's site-packages via a .pth file.
# This makes pcbnew importable in tests and runtime without per-run hacks.
if [[ -n "${CONDA_PREFIX:-}" ]]; then
  # Prefer explicit override for python-path; fall back to detected
  KI_PY_PATHS="$PY_PATH_TO_SET"
  if [[ -z "$KI_PY_PATHS" && -n "$DETECTED_KICAD_PY_PATH" ]]; then
    KI_PY_PATHS="$DETECTED_KICAD_PY_PATH"
  fi
  if [[ -n "$KI_PY_PATHS" ]]; then
    # Resolve site-packages path of the active conda Python
    CONDA_SITE_PKGS="$(python - <<'PY'
import site
cands=[p for p in site.getsitepackages() if p.endswith('site-packages')]
print(cands[0] if cands else site.getusersitepackages())
PY
)"
    if [[ -d "$CONDA_SITE_PKGS" ]]; then
      PTH_FILE="$CONDA_SITE_PKGS/kicad_system.pth"
      # Support multiple paths separated by ':'
      IFS=':' read -r -a _KI_PATH_ARR <<< "$KI_PY_PATHS"
      {
        for p in "${_KI_PATH_ARR[@]}"; do
          [[ -n "$p" ]] && echo "$p"
        done
      } > "$PTH_FILE"
      echo "Wrote KiCad path(s) to $PTH_FILE"
      # Quick verification (non-fatal)
      python - <<'PY' || true
try:
    import pcbnew
    import sys
    print(f"pcbnew resolved: {pcbnew.__file__}")
except Exception as e:
    print(f"WARNING: pcbnew import still failing: {e}")
PY
    else
      echo "WARNING: Could not locate conda site-packages; skipping .pth install" >&2
    fi
  fi
fi



# Install Python dependencies into the same interpreter we will use (avoids ABI mismatches)
if [[ -f "$REQ_FILE" ]]; then
  echo "Installing Python requirements with $PY_EXEC_TO_SET from $REQ_FILE ..."
  if [[ "$PY_EXEC_TO_SET" == "/usr/bin/python3" ]] && command -v apt-get >/dev/null 2>&1; then
    # Prefer system packages to match system ABI when using system Python
    if [[ $EUID -ne 0 ]]; then SUDO="sudo"; else SUDO=""; fi
    $SUDO apt-get update -y || true
    $SUDO apt-get install -y python3-pip python3-pil python3-cairosvg python3-cffi python3-sexpdata || true
    # Install missing packages via pip if not available as distro packages
    "$PY_EXEC_TO_SET" -m pip install --user --upgrade pip || true
    "$PY_EXEC_TO_SET" -m pip install --user -r "$REQ_FILE" || true
  else
    "$PY_EXEC_TO_SET" -m pip install --upgrade pip
    "$PY_EXEC_TO_SET" -m pip install -r "$REQ_FILE"
  fi
else
  echo "Requirements file not found at $REQ_FILE" >&2
  exit 1
fi

# Install Node dependencies and build TS server
if [[ -f "$ROOT_DIR/package.json" ]]; then
  echo "Installing Node dependencies..."
  if command -v npm >/dev/null 2>&1; then
    npm install
    echo "Building TypeScript project..."
    npm run build
  else
    echo "npm not found. Skipping Node install/build."
  fi
fi


python - "$CONFIG_FILE" <<'PYCONF'
import json, os, sys
cfg_path = sys.argv[1]
try:
    with open(cfg_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
except FileNotFoundError:
    cfg = {}

py_exec = os.environ.get('PY_EXEC_TO_SET')
py_path = os.environ.get('PY_PATH_TO_SET', '')
kicad_path = os.environ.get('KICAD_PATH_TO_SET', '')

cfg['pythonExecutable'] = py_exec or cfg.get('pythonExecutable') or 'python3'
# Only set pythonPath if provided; otherwise leave existing as-is or empty
if py_path is not None and py_path != '':
    cfg['pythonPath'] = py_path
# Optionally set kicadPath to propagate KICAD_PATH
if kicad_path:
    cfg['kicadPath'] = kicad_path

# Keep other defaults; ensure logDir is set
cfg.setdefault('logDir', '~/.kicad-mcp/logs')
cfg.setdefault('logLevel', 'warn')
cfg.setdefault('responseTimeoutMs', 60000)

with open(cfg_path, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2)
print(f"Updated config at {cfg_path}: pythonExecutable={cfg['pythonExecutable']}, pythonPath={cfg.get('pythonPath','')}")
PYCONF

# Validate the environment
echo "Running environment validator..."
python "$ROOT_DIR/python/validate_env.py" \
  --config "$CONFIG_FILE" \
  ${PY_EXEC_OVERRIDE:+--python-exec "$PY_EXEC_OVERRIDE"} \
  ${PY_PATH_OVERRIDE:+--python-path "$PY_PATH_OVERRIDE"}

printf "\nEnvironment setup completed for %s.\n" "$ACTIVE_ENV_DESC"
