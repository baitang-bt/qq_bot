#!/usr/bin/env bash
# Pick the Python command for this machine (arm64 native on Apple Silicon).
pick_python_cmd() {
  local py="$1"
  if [[ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]]; then
    printf 'arch -arm64 %q' "$py"
  else
    printf '%q' "$py"
  fi
}
