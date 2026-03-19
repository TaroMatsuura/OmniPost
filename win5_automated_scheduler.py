#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WIN5 Automated Scheduler (V1.0)
---------------------------------
1. 起動時にIPATへ接続し、WIN5の正確な締切時刻を取得
2. 締切10分前まで待機
3. WIN5予想スクリプトを実行
4. WIN5自動投票スクリプトを実行
"""
import time
import datetime
import subprocess
import os
import sys
import logging
from dotenv import load_dotenv
from ipat_win5_vote_driver import IPATWin5VoteDriver
from ipat_vote_driver import IPATVoteDriver

load_dotenv()

# ── T58_Box ディレクトリ設定（WIN5予想スクリプト・CSV生成先） ──────────────
_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
T58_BOX_DIR   = os.path.abspath(os.path.join(_SCRIPT_DIR, os.getenv('T58_BOX_DIR', '../T58_Box')))
T58_VENV_PY   = os.path.join(T58_BOX_DIR, '.venv', 'bin', 'python3')
WIN5_PRED_PY  = os.path.join(T58_BOX_DIR, 'equine_edge_win5_predictor_v13_1.py')
WIN5_AUTO_PY  = os.path.join(_SCRIPT_DIR, 'equine_edge_win5_auto_vote.py')
PREDICTIONS_DIR = T58_BOX_DIR  # CSVはT58_Boxに出力される

# LOG Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def run_win5_flow(analysis_date):
    logger.info(f"📅 WIN5自動運用を開始します (対象日: {analysis_date})")
    
    driver = IPATWin5VoteDriver()
    deadline_str = "14:45" # Default
    
    try:
        driver.start()
        if driver.login():
            if driver.navigate_to_win5():
                deadline_str = driver.get_win5_deadline()
                logger.info(f"✅ IPATから取得した締切時刻: {deadline_str}")
            else:
                logger.warning("⚠️ WIN5画面への遷移に失敗。デフォルト締切(14:45)を使用します。")
        else:
            logger.error("❌ IPATログインに失敗。デフォルト締切(14:45)を使用します。")
    except Exception as e:
        logger.error(f"❌ 締切時刻取得中にエラー: {e}")
    finally:
        driver.close()

    # 待機目標時刻を計算 (締切10分前)
    target_dt = datetime.datetime.strptime(f"{analysis_date} {deadline_str.replace(':', '')}", "%Y%m%d %H%M")
    trigger_dt = target_dt - datetime.timedelta(minutes=10)
    
    logger.info(f"🎯 WIN5実行予定時刻: {trigger_dt.strftime('%H:%M')} (締切: {deadline_str})")

    while True:
        now = datetime.datetime.now()
        if now >= trigger_dt:
            logger.info("🚀 実行時刻に到達しました。WIN5シーケンスを開始します。")
            break
        
        # 1時間おきにログ
        if now.minute == 0 and now.second < 10:
             logger.info(f"⏳ WIN5待機中... (現在: {now.strftime('%H:%M:%S')}, 予定: {trigger_dt.strftime('%H:%M')})")
        
        time.sleep(10)

    # 1. データ同期 (0B15 & 0B31)
    # 最新データを取得してから予想を行う必要がある
    downloader_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'JVlinkdownloader'))
    script_path = os.path.join(downloader_path, 'run_downloader.sh')
    
    if os.path.exists(script_path):
        logger.info("📡 最新データ(0B15/0B31)を同期中...")
        try:
            # 当日全情報
            subprocess.run([script_path, "--dataspec", "0B15", "--from-time", analysis_date, "--option", "1"], 
                           cwd=downloader_path, capture_output=True, timeout=120)
            # 直前オッズ (WIN5対象レース付近)
            # ※WIN5予想スクリプト内部でも最新を追うが、DBに存在することが前提
            subprocess.run([script_path, "--dataspec", "0B31", "--from-time", analysis_date, "--option", "1"], 
                           cwd=downloader_path, capture_output=True, timeout=120)
            logger.info("✅ データ同期完了")
        except Exception as e:
            logger.warning(f"⚠️ データ同期中にタイムアウトまたはエラー発生: {e}")
    else:
        logger.warning(f"⚠️ ダウンローダーが見つかりません: {script_path}")

    # 2. 予想生成（既存CSVがある場合はスキップ）
    csv_filename = os.path.join(PREDICTIONS_DIR, f"predictions_win5_{analysis_date}.csv")
    if os.path.exists(csv_filename):
        logger.info(f"✅ 既存の予想CSV ({csv_filename}) を使用します。生成スキップ。")
    else:
        logger.info("🧠 WIN5予想スクリプトを実行中...")
        subprocess.run([T58_VENV_PY, WIN5_PRED_PY, analysis_date], cwd=T58_BOX_DIR)
    
    # 3. 予想結果の妥当性チェック
    csv_filename = os.path.join(PREDICTIONS_DIR, f"predictions_win5_{analysis_date}.csv")
    if os.path.exists(csv_filename):
        with open(csv_filename, 'r', encoding='utf-8-sig') as f:
            content = f.read()
            if '馬番' not in content or len(content.split('\n')) < 2 or (',,' in content and 'WIN5-' in content):
                 logger.warning("⚠️ 予想買い目が空（0頭選択）です。本日のWIN5投票は見送ります。")
                 return
    else:
        logger.error(f"❌ 予想ファイル ({csv_filename}) が生成されませんでした。")
        return

    # 4. 投票前残高チェック（残高不足なら見送り・追加資金なし）
    win5_unit   = int(os.getenv('WIN5_UNIT_AMOUNT', 100))
    win5_needed = win5_unit * 243  # TOP3×5Leg=243点
    logger.info(f"💰 WIN5投票前残高チェック中... (必要額: {win5_needed:,}円)")
    balance_driver = IPATVoteDriver()
    try:
        balance_driver.start()
        if balance_driver.login():
            limit = balance_driver.get_purchase_limit()
            if limit is not None:
                logger.info(f"💳 IPAT購入限度額: {limit:,}円 / 必要額: {win5_needed:,}円")
                if limit < win5_needed:
                    logger.warning(
                        f"⏭️  残高不足のためWIN5投票を見送ります。"
                        f" ({limit:,}円 < {win5_needed:,}円 / 不足: {win5_needed - limit:,}円)"
                    )
                    return
            else:
                logger.warning("⚠️ 残高取得失敗。残高確認をスキップして投票を続行します。")
    except Exception as e:
        logger.warning(f"⚠️ 残高チェック中にエラー ({e})。投票を続行します。")
    finally:
        try:
            balance_driver.close()
        except Exception:
            pass

    # 5. 投票実行
    logger.info("🗳️  WIN5自動投票スクリプトを実行中...")
    env = os.environ.copy()
    subprocess.run([sys.executable, WIN5_AUTO_PY, analysis_date], cwd=_SCRIPT_DIR, env=env)

    logger.info("✅ WIN5自動運用シーケンスが完了しました。")

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime('%Y%m%d')
    run_win5_flow(target)
