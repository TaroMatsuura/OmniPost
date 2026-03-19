#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IPAT 購入限度額（残高）確認スクリプト
使い方: .venv/bin/python3 check_ipat_balance.py

※ auto_vote が稼働していない時間帯に実行してください（セッション競合防止）
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ipat_vote_driver import IPATVoteDriver

def check_balance():
    # .env から投票設定を読み込む
    tansho_unit   = int(os.getenv('INITIAL_WALLET_AMOUNT', 300))  # 単勝1頭あたり
    tansho_needed = tansho_unit * 3                                # TOP3合計
    win5_unit     = int(os.getenv('WIN5_UNIT_AMOUNT', 100))        # WIN5 1点あたり
    # WIN5点数は243点（TOP3×5Leg）固定
    win5_needed   = win5_unit * 243

    driver = IPATVoteDriver()
    try:
        driver.start()
        if not driver.login():
            print("❌ ログイン失敗")
            return None

        # 「更新」ボタンクリック相当: get_purchase_limit() は clear_popups 後に残高を取得
        limit = driver.get_purchase_limit()

        if limit is not None:
            print(f"""\n{'='*45}
✅ IPAT 購入限度額: {limit:,}円
{'='*45}
【単勝TOP3】1レース必要額: {tansho_needed:,}円  (1頭{tansho_unit}円 × 3頭)
            → {'✅ 残高OK' if limit >= tansho_needed else f'❌ 残高不足 ({tansho_needed - limit:,}円 不足)'}

【WIN5 243点】必要額: {win5_needed:,}円  (1点{win5_unit}円 × 243点)
            → {'✅ 残高OK' if limit >= win5_needed else f'❌ 残高不足 ({win5_needed - limit:,}円 不足 → チャージが必要)'}
{'='*45}""")
        else:
            print("⚠️ 購入限度額を取得できませんでした")

        return limit

    except Exception as e:
        print(f"❌ エラー: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        driver.close()

if __name__ == "__main__":
    check_balance()
