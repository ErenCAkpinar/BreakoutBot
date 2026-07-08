#!/bin/bash
# Deploy the NEW regime-aware bot as a SECOND test service — ayrı klasör,
# eski bota DOKUNMAZ. Mac'ten çalıştır: bash deploy_test.sh
set -e

: "${BREAKOUTBOT_SERVER:?BREAKOUTBOT_SERVER tanımlı değil — örn: export BREAKOUTBOT_SERVER=root@<vm-ip>}"
SERVER="$BREAKOUTBOT_SERVER"
DIR='~/BreakoutBot-test'
FILES="paper_bb.py config.py indicators.py strategy.py math_engine.py mean_reversion.py regime.py short_sleeve.py"

echo "📁 Sunucuda test klasörü oluşturuluyor: $DIR"
ssh "$SERVER" "mkdir -p $DIR"

echo "📤 Dosyalar kopyalanıyor (8 dosya)…"
scp $FILES "$SERVER:$DIR/"

echo ""
echo "✅ Kopyalandı → $DIR"
echo "Şimdi sunucuya gir ve eski servisin ayarını göster (ben servisi ona göre yazacağım):"
echo "    ssh $SERVER"
echo "    systemctl cat breakoutbot"
