#!/bin/bash
# Deploy the regime-aware bot to the TEST service — ayrı klasör, eski bota DOKUNMAZ.
# Mac'ten çalıştır: bash deploy_test.sh [--force]
#
# Bu betik üç şeyi garanti eder:
#   1. Gönderilen dosya listesi paper_bb.py'nin GERÇEK import ağacından türetilir.
#      Elle tutulan liste `metrics.py`'yi atlıyordu; paper_bb onu import ediyor,
#      yani metrics.py'ye yapılan her düzeltme (T-M1 dahil) sunucuya HİÇ gitmedi
#      ve oradaki kopya eski bir elle-kopyalamadan kalmaydı.
#   2. Kirli ağaçtan deploy edilmez. Aksi halde çalışan sisteme karşılık gelen
#      bir commit olmaz ve VM ölürse dağıtılan sistem repodan kurulamaz.
#   3. Dağıtılan SHA sunucuya yazılır (DEPLOYED_SHA), böylece "sunucuda ne koşuyor"
#      sorusunun cevabı tahmin değil, dosya olur.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

: "${BREAKOUTBOT_SERVER:?BREAKOUTBOT_SERVER tanımlı değil — örn: export BREAKOUTBOT_SERVER=root@<vm-ip>}"
SERVER="$BREAKOUTBOT_SERVER"
DIR='~/BreakoutBot-test'
FORCE="${1:-}"

# ── 1. Dosya listesini import ağacından türet ────────────────────────────────
# Kök paper_bb.py; yerel modüller özyinelemeli izlenir. Liste elle tutulmaz.
# while-read rather than `mapfile`: macOS ships bash 3.2, which has no mapfile.
FILES=()
while IFS= read -r _f; do
  [ -n "$_f" ] && FILES+=("$_f")
done < <(python3.12 - <<'PY'
import ast, os

# secrets_local.py, paper_bb'nin SADECE --testnet dalında lazy import ettiği
# API anahtarı dosyasıdır ve .gitignore'dadır. Çalışan bot saf simülasyon —
# anahtar yüklü değil ve olmamalı. İzleyici onu bulur; buradan çıkarılır.
EXCLUDE = {"secrets_local.py"}

seen, queue = set(), ["paper_bb.py"]
while queue:
    f = queue.pop()
    if f in seen or f in EXCLUDE or not os.path.exists(f):
        continue
    seen.add(f)
    tree = ast.parse(open(f, encoding="utf-8").read())
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for n in names:
            cand = n.split(".")[0] + ".py"
            if os.path.exists(cand) and cand not in seen and cand not in EXCLUDE:
                queue.append(cand)
print("\n".join(sorted(seen)))
PY
)

echo "📦 Import ağacından türetilen ${#FILES[@]} dosya:"
printf '   %s\n' "${FILES[@]}"

# ── 2. Kirli ağaç kontrolü ───────────────────────────────────────────────────
DIRTY="$(git status --porcelain -- "${FILES[@]}" 2>/dev/null || true)"
if [ -n "$DIRTY" ]; then
  echo ""
  echo "⛔ Çalışma ağacı kirli — bu dosyalar commit edilmemiş:"
  echo "$DIRTY" | sed 's/^/     /'
  if [ "$FORCE" != "--force" ]; then
    echo ""
    echo "   Deploy edilirse sunucuda koşan koda karşılık gelen bir commit OLMAZ."
    echo "   Önce commit'le, ya da bilerek geçmek için:  bash deploy_test.sh --force"
    exit 1
  fi
  echo "   ⚠️  --force verildi, kirli ağaçtan devam ediliyor."
fi

SHA="$(git rev-parse --short HEAD 2>/dev/null || echo 'no-git')"
[ -n "$DIRTY" ] && SHA="${SHA}-dirty"

# ── 3. Gönder ────────────────────────────────────────────────────────────────
echo ""
echo "📁 Sunucuda klasör: $DIR"
ssh "$SERVER" "mkdir -p $DIR"
echo "📤 Kopyalanıyor…"
scp "${FILES[@]}" "$SERVER:$DIR/"

# Ne koştuğunu tahmin etmek zorunda kalmamak için sürümü diske yaz.
ssh "$SERVER" "printf '%s\n' '$SHA  $(date -u +%Y-%m-%dT%H:%M:%SZ)' > $DIR/DEPLOYED_SHA"

echo ""
echo "✅ Kopyalandı → $DIR   (sürüm: $SHA)"
echo ""
echo "⚠️  ENV SÖZLEŞMESİ — parametreler artık config.py VARSAYILANI (2026-08-27)."
echo "    Unit'te bir X_* exit override'ı KALMAMALI: doğrulanan seti ezer ve hiç"
echo "    test edilmemiş bir karışım çalıştırır. Denetle:"
echo ""
echo "      ssh $SERVER 'systemctl show breakoutbot-test -p Environment'"
echo ""
echo "    Boş dönmeli. Bir X_* bayrağı ancak experiments/DEFTER.md'de onu hak"
echo "    eden bir kol varsa eklenir — elle ayar yok."
echo ""
echo "    Yeniden başlat:  ssh $SERVER 'systemctl restart breakoutbot-test'"
