#!/usr/bin/env bash
# Deploy the AI shadow worker to the VM. Touches neither the bot, its state nor its unit.
# Installs code + units and a venv; it does NOT enable the timer (see README).
set -euo pipefail

SERVER="${BREAKOUTBOT_SERVER:-breakoutbot}"
cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain -- ai_shadow)" ] && [ "${1:-}" != "--force" ]; then
  echo "ai_shadow/ has uncommitted changes — commit first (or pass --force)" >&2
  exit 1
fi
SHA="$(git rev-parse --short HEAD)"

# COPYFILE_DISABLE: macOS tar would otherwise ship ._ AppleDouble files.
COPYFILE_DISABLE=1 tar --exclude '__pycache__' --exclude '*.pyc' -czf - ai_shadow |
  ssh "$SERVER" "set -euo pipefail
    install -d -m 700 /opt/breakoutbot-ai /var/lib/breakoutbot-ai-shadow
    rm -rf /opt/breakoutbot-ai/ai_shadow.new
    mkdir /opt/breakoutbot-ai/ai_shadow.new
    tar -xzf - -C /opt/breakoutbot-ai/ai_shadow.new --no-same-owner --strip-components=1
    if [ -d /opt/breakoutbot-ai/ai_shadow ]; then
      mv /opt/breakoutbot-ai/ai_shadow /opt/breakoutbot-ai/ai_shadow.prev-\$(date -u +%Y%m%dT%H%M%SZ)
    fi
    mv /opt/breakoutbot-ai/ai_shadow.new /opt/breakoutbot-ai/ai_shadow
    echo '$SHA' > /opt/breakoutbot-ai/DEPLOYED_SHA
    [ -x /opt/breakoutbot-ai/.venv/bin/python ] || python3 -m venv /opt/breakoutbot-ai/.venv
    /opt/breakoutbot-ai/.venv/bin/pip install -q --disable-pip-version-check 'anthropic==1.8.0'
    install -m 644 /opt/breakoutbot-ai/ai_shadow/breakoutbot-ai-shadow.service \
                   /opt/breakoutbot-ai/ai_shadow/breakoutbot-ai-shadow.timer /etc/systemd/system/
    systemctl daemon-reload
    echo \"deployed $SHA\""
