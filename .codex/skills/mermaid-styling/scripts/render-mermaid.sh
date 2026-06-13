#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 path/to/file.md" >&2
  exit 2
fi

input=$1
chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

if [[ ! -f "$input" ]]; then
  echo "Markdown file not found: $input" >&2
  exit 1
fi

if [[ ! -x "$chrome" ]]; then
  echo "Chrome executable not found: $chrome" >&2
  exit 1
fi

slug=$(basename "$input" .md)
output_dir="/tmp/mermaid-styling-${slug}-assets"
output_md="/tmp/mermaid-styling-${slug}.md"

PUPPETEER_EXECUTABLE_PATH="$chrome" \
  npx --yes @mermaid-js/mermaid-cli \
  -i "$input" \
  -o "$output_md" \
  -a "$output_dir" \
  -e png \
  -s 2

echo "Rendered assets: $output_dir"
