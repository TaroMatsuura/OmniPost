#!/bin/bash
# -----------------------------------------------------------------------------
# OmniPost - JRA IPAT 自動投票システム 一括起動スクリプト
# -----------------------------------------------------------------------------

# 0. 環境設定
DATE_STR=$(date +%Y%m%d)
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"   # OmniPost の絶対パス
JV_DIR="${BASE_DIR}/../JVlinkdownloader"
VENV_PY="${BASE_DIR}/.venv/bin/python3"
LOG_DIR="${BASE_DIR}/logs"
mkdir -p "${LOG_DIR}"

echo "=========================================================="
echo "🏇 OmniPost 自動投票システム 起動シーケンス開始 ($DATE_STR)"
echo "=========================================================="

# 1. JV-Linkリアルタイム監視 (jv_watch.py) の起動
echo "📡 [1/3] JV-Link リアルタイム監視を起動中..."
if [ -f "${JV_DIR}/run_watch.sh" ]; then
    cd "${JV_DIR}"
    ./run_watch.sh &
    cd "${BASE_DIR}"
else
    echo "⚠️  JV-Linkダウンローダーが見つかりません: ${JV_DIR}"
fi
sleep 5

# 2. オート投票スクリプト (equine_edge_auto_vote_v12_7.py) の起動
echo "🎯 [2/3] オート投票スクリプトを起動中..."
cd "${BASE_DIR}"
nohup "${VENV_PY}" equine_edge_auto_vote_v12_7.py "${DATE_STR}" >> "${LOG_DIR}/auto_vote_v12_7_${DATE_STR}.log" 2>&1 &
sleep 2

# 3. WIN5自動スケジューラ (win5_automated_scheduler.py) の起動
echo "🏆 [3/3] WIN5自動スケジューラを起動中..."
nohup "${VENV_PY}" win5_automated_scheduler.py "${DATE_STR}" >> "${LOG_DIR}/win5_scheduler_${DATE_STR}.log" 2>&1 &
sleep 1

echo "=========================================================="
echo "✅ すべてのプロセスがバックグラウンドで起動しました。"
echo "📊 投票ログ:  tail -f ${LOG_DIR}/auto_vote_v12_7_${DATE_STR}.log"
echo "📊 WIN5ログ:  tail -f ${LOG_DIR}/win5_scheduler_${DATE_STR}.log"
echo "=========================================================="
