#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EquineEdge WIN5 Auto-Vote (V1.0)
-----------------------------------------
1. WIN5の予想CSVを読み込む
2. IPATWin5VoteDriverを起動してログイン
3. 買い目を選択し、合計金額（点数×100円）を投票
4. エビデンス（スクリーンショット）を保存
"""
import csv
import sys
import datetime
import os
import logging
from dotenv import load_dotenv
from ipat_win5_vote_driver import IPATWin5VoteDriver

load_dotenv('.env')

# 予想CSVの読み込み元（T58_Boxの出力ディレクトリ）
_SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
PREDICTIONS_DIR = os.path.abspath(os.path.join(_SCRIPT_DIR, os.getenv('T58_BOX_DIR', '../T58_Box')))

# ロギング設定：標準出力に出力する
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

def run_win5_auto_vote(target_date, dry_run=True):
    csv_filename = os.path.join(PREDICTIONS_DIR, f"predictions_win5_{target_date}.csv")
    
    if not os.path.exists(csv_filename):
        print(f"❌ 予想ファイルが見つかりません: {csv_filename}")
        return

    selections = []
    overall_status = '見送り'
    with open(csv_filename, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i == 0:
                overall_status = row.get('ステータス', '投票')
            
            # 馬番のパース（念のためfloat/int変換を考慮）
            raw_horses = row['馬番'].split(',')
            clean_horses = []
            for h in raw_horses:
                val = h.strip()
                if not val: continue
                try:
                    # 5.0 -> 5 のような変換を考慮
                    clean_horses.append(str(int(float(val))).zfill(2))
                except:
                    clean_horses.append(val)
            selections.append(clean_horses)

    if overall_status == '見送り':
        print(f"⏭️  WIN5全体が見送り判定のため終了します")
        return

    if len(selections) != 5:
        print(f"❌ WIN5のデータが5レース分ありません (現在: {len(selections)}レース)")
        return

    # 総点数計算
    total_points = 1
    for s in selections:
        total_points *= len(s)
    win5_unit = int(os.getenv('WIN5_UNIT_AMOUNT', 100))
    total_amount = total_points * win5_unit

    print(f"🎯 WIN5投票を開始します: {target_date}")
    print(f"💰 合計点数: {total_points}点 ({total_amount:,}円)")

    driver = IPATWin5VoteDriver()
    try:
        # IPATサイトに接続
        driver.start()
        
        # ログイン
        if driver.login():
            logger.info("✅ IPATにログインしました")
            if driver.navigate_to_win5():
                success = driver.vote_win5(selections, total_amount)
                if success:
                    actual_dry_run = os.getenv('CONFIRM_VOTE', 'True') == 'True'
                    logger.info(f"✅ WIN5投票の{'シミュレーション' if actual_dry_run else '本番送信'}が完了しました")
                else:
                    logger.error("❌ 投票プロセス中にエラーが発生しました")
            else:
                logger.error("❌ WIN5メニューが見つかりませんでした")
        else:
            logger.error("❌ ログインに失敗しました")
    except Exception as e:
        logger.error(f"❌ 予期せぬエラーが発生しました: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        driver.close()

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime('%Y%m%d')
    # 安全のためデフォルトでドライラン的な動作（driver側の実装による）
    run_win5_auto_vote(target)
