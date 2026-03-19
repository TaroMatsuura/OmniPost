#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自動投票スクリプト V12.7 (Harvest & Run / ころがし対応版)
- 資金を次レースへ自動的に「ころがし」ます。
- 指定したしきい値（デフォルト300%）に達した場合、その日の運用を停止（勝ち逃げ）します。
- 払戻データ(0B12)のポーリング機能を搭載。
"""

import os
import sys
import time
import json
import logging
import threading
import pandas as pd
import pymysql
import subprocess
from datetime import datetime, timedelta
from dotenv import load_dotenv

# IPATドライバーをインポート
try:
    from ipat_vote_driver import IPATVoteDriver
    DRIVER_AVAILABLE = True
except ImportError:
    print("⚠️  ipat_vote_driver.py が見つかりません。ダミーモードで実行します。")
    DRIVER_AVAILABLE = False

load_dotenv('.env')

logger = logging.getLogger(__name__)

# ── T58_Box ディレクトリ設定（予想スクリプト・CSV生成先） ──────────────────────
# .env の T58_BOX_DIR で上書き可能（デフォルト: スクリプトの1階層上の T58_Box）
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
T58_BOX_DIR  = os.path.abspath(os.path.join(_SCRIPT_DIR, os.getenv('T58_BOX_DIR', '../T58_Box')))
T58_VENV_PY  = os.path.join(T58_BOX_DIR, '.venv', 'bin', 'python3')
PREDICTOR_PY = os.path.join(T58_BOX_DIR, 'equine_edge_predictor_v20.py')

class AutoVoteSystemV12_7:
    """自動投票システム V12.7"""
    
    def __init__(self, analysis_date, dry_run=False, threshold=1.5):
        self.analysis_date = analysis_date
        self.dry_run = dry_run
        self.threshold = threshold  # 3.0 = 300%
        self.driver = None
        self.predictions = None
        self.use_db_time = os.getenv('SYNC_RACE_TIME_WITH_DB', 'False') == 'True'
        self.last_csv_mtime = 0
        self.use_rollover = os.getenv('V12_7_USE_ROLLOVER', 'False') == 'True'

        # 状態管理
        self.state_file = f'v12_7_state_{analysis_date}.json'
        # 予想CSV読み込み先 (T58_Box の出力ディレクトリ)
        self.predictions_dir = T58_BOX_DIR
        # .env から初期金額を取得
        self.initial_wallet_amount = int(os.getenv('INITIAL_WALLET_AMOUNT', 1000))
        self.initial_bank_amount = int(os.getenv('INITIAL_BANK_AMOUNT', 10000))
        self.target_balance_amount = int(os.getenv('TARGET_BALANCE_AMOUNT', int(self.initial_bank_amount * self.threshold)))
        
        self.wallet_balance = 0
        self.bank_balance = 0
        self.total_invested_cash = 0
        self.current_rollover = 0
        self.stopped = False
        self.bankroll_mode = 'fixed'

        # 複数レース並行時の払戻スレッド排他制御
        self._payout_lock = threading.Lock()
        # バッチ事前準備済みのレース番号セット（同一Rナンバー全場を一括準備後に登録）
        self.batch_prepared = set()

        self.load_state()
        
        # 初回起動時（または状態が空の場合）に初期設定
        if self.wallet_balance == 0 and self.total_invested_cash == 0:
            # 銀行から最初の財布分を出す
            refill = self.initial_wallet_amount
            self.wallet_balance = refill
            self.total_invested_cash = refill
            self.bank_balance = self.initial_bank_amount - refill
            self.save_state()

        self._refresh_bankroll_mode(force_log=True)
            
        self.setup_logging()

        self.jyo_map = {
            '中山': '06', '京都': '08', '小倉': '10', '東京': '05', '阪神': '09',
            '福島': '02', '新潟': '04', '札幌': '01', '函館': '03', '中京': '07'
        }
        
        # パルス・リフレッシュ用
        self.all_race_times = []
        self.processed_pulses = set()
        self.load_all_race_times()
        
        # 起動時点で既に発走済みのレースはパルス処理済みにする
        now = datetime.now()
        for pt in self.all_race_times:
            if now > pt:
                self.processed_pulses.add(pt)

    def load_all_race_times(self):
        """当日開催される全レースの発走時刻をリスト化する"""
        year = self.analysis_date[:4]
        monthday = self.analysis_date[4:]
        conn = self.get_db_connection()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT HassoTime FROM S_RACE WHERE Year=%s AND MonthDay=%s", (year, monthday))
                rows = cursor.fetchall()
                for r in rows:
                    t_str = str(r['HassoTime']).zfill(4)
                    dt = datetime.combine(
                        datetime.strptime(self.analysis_date, '%Y%m%d').date(),
                        datetime.strptime(t_str, '%H%M').time()
                    )
                    self.all_race_times.append(dt)
                self.all_race_times.sort()
                self.logger.info(f"🕒 全{len(self.all_race_times)}レースの発走時刻をロードしました（パルス監視用）")
        except Exception as e:
            self.logger.warning(f"⚠️ 発走時刻のロード失敗: {e}")
        finally:
            conn.close()

    def setup_logging(self):
        log_filename = f'auto_vote_v12_7_{self.analysis_date}.log'
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler = logging.FileHandler(log_filename, encoding='utf-8')
        file_handler.setFormatter(formatter)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.handlers = [file_handler, console_handler]
        self.logger = root_logger

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.wallet_balance = state.get('wallet_balance', 0)
                    self.bank_balance = state.get('bank_balance', 0)
                    self.total_invested_cash = state.get('total_invested_cash', 0)
                    self.current_rollover = state.get('current_rollover', 0)
                    self.stopped = state.get('stopped', False)
            except Exception as e:
                print(f"Error loading state: {e}")

    def save_state(self):
        state = {
            'wallet_balance': int(self.wallet_balance),
            'bank_balance': int(self.bank_balance),
            'total_invested_cash': int(self.total_invested_cash),
            'current_rollover': int(self.current_rollover),
            'target_balance_amount': int(self.target_balance_amount),
            'bankroll_mode': self.bankroll_mode,
            'managed_balance': int(self.get_managed_balance()),
            'stopped': self.stopped,
            'last_update': datetime.now().isoformat()
        }
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=4)

    def get_managed_balance(self):
        """財布と内部銀行を合算した運用残高を返す"""
        return self.wallet_balance + self.bank_balance

    def get_bankroll_mode(self):
        """目標残高に応じて現在の運用モードを返す"""
        if not self.use_rollover:
            return 'fixed'
        if self.target_balance_amount > 0 and self.get_managed_balance() < self.target_balance_amount:
            return 'rollover'
        return 'fixed'

    def _refresh_bankroll_mode(self, force_log=False):
        """現在残高に応じて運用モードを更新する"""
        next_mode = self.get_bankroll_mode()
        active_logger = getattr(self, 'logger', logger)
        if force_log or next_mode != self.bankroll_mode:
            active_logger.info(
                "💼 資金管理モード: %s (運用残高 %s円 / 目標 %s円)",
                'ころがし' if next_mode == 'rollover' else '定額',
                f"{self.get_managed_balance():,}",
                f"{self.target_balance_amount:,}",
            )
        self.bankroll_mode = next_mode
        if self.bankroll_mode == 'fixed':
            self.current_rollover = 0
        return self.bankroll_mode

    def ensure_wallet_balance(self, required_amount, context=''):
        """必要額に達するまで内部銀行から財布へ補充する"""
        if self.wallet_balance >= required_amount:
            return True

        while self.wallet_balance < required_amount:
            refill_amount = self.initial_wallet_amount
            if self.bank_balance >= refill_amount:
                self.logger.info(f"💸 財布残高不足{context}。銀行から {refill_amount}円 を補充します。")
                self.wallet_balance += refill_amount
                self.bank_balance -= refill_amount
                self.total_invested_cash += refill_amount
            else:
                self.wallet_balance += self.bank_balance
                self.total_invested_cash += self.bank_balance
                self.bank_balance = 0
                break

        self.save_state()
        return self.wallet_balance >= required_amount

    def build_tansho_bet_plan(self, horse_numbers, base_stake, rollover_amount):
        """単勝複数買い時に、ころがし額を100円単位で均等配分する"""
        stakes = [base_stake for _ in horse_numbers]
        if rollover_amount <= 0 or not horse_numbers:
            return list(zip(horse_numbers, stakes))

        extra_units = rollover_amount // 100
        for index in range(extra_units):
            stakes[index % len(stakes)] += 100
        return list(zip(horse_numbers, stakes))

    def get_db_connection(self):
        return pymysql.connect(
            host=os.getenv('DB_HOST', '192.168.1.9'),
            user=os.getenv('DB_USER', 'taromat'),
            password=os.getenv('DB_PASSWORD', '61072711taro'),
            database=os.getenv('DB_DATABASE', 'jravan_data'),
            charset='utf8mb4'
        )

    def bootstrap_daily_data(self):
        """起動時に当日データ(0B15)の取得と初期予想の生成を行う"""
        year = self.analysis_date[:4]
        monthday = self.analysis_date[4:]
        date_key = f"{year}{monthday}"
        csv_filename = os.path.join(self.predictions_dir, f'predictions_{self.analysis_date}.csv')
        
        downloader_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'JVlinkdownloader'))
        script_path = os.path.join(downloader_path, 'run_downloader.sh')

        # 1. 0B15 (当日情報) を一括取得
        if os.path.exists(script_path):
            self.logger.info(f"🌞 [Bootstrap] 当日全情報(0B15)を取得しています... ({date_key})")
            try:
                subprocess.run([script_path, "--dataspec", "0B15", "--from-time", date_key, "--option", "1"], 
                               cwd=downloader_path, capture_output=True, timeout=120)
            except Exception as e:
                self.logger.warning(f"⚠️  0B15 Bootstrap取得失敗: {e}")

        # 2. 予想CSVがない場合、全レースの予想を回す
        if not os.path.exists(csv_filename):
            self.logger.info(f"🧠 [Bootstrap] 予想ファイルが未生成のため、全レースの初期予想を開始します...")
            conn = self.get_db_connection()
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("SELECT JyoCD, RaceNum FROM S_RACE WHERE Year=%s AND MonthDay=%s", (year, monthday))
                    races = cursor.fetchall()
                    for r in races:
                        race_num = int(r['RaceNum'])
                        self.logger.info(f"🚀 初期予想実行: {date_key} R{race_num}")
                        subprocess.run([T58_VENV_PY, PREDICTOR_PY, self.analysis_date, "--race", str(race_num)],
                                       cwd=T58_BOX_DIR, capture_output=True, timeout=60)
            except Exception as e:
                self.logger.error(f"⚠️  初期予想生成中にエラー: {e}")
            finally:
                conn.close()

    def refresh_scratches_0B16(self):
        """0B16を使用して取消・変更情報を更新"""
        year = self.analysis_date[:4]
        monthday = self.analysis_date[4:]
        date_key = f"{year}{monthday}"
        
        downloader_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'JVlinkdownloader'))
        script_path = os.path.join(downloader_path, 'run_downloader.sh')

        if os.path.exists(script_path):
            self.logger.info(f"📡 [Periodic] 定期データ更新(0B16)を実行中... ({date_key})")
            try:
                subprocess.run([script_path, "--dataspec", "0B16", "--from-time", date_key, "--option", "1"], 
                               cwd=downloader_path, capture_output=True, timeout=60)
            except Exception as e:
                self.logger.warning(f"⚠️  0B16 更新失敗: {e}")

    def refresh_all_upcoming_info(self):
        """0B15 と 0B16 をセットで取得し、全場の最新情報を反映させる"""
        year = self.analysis_date[:4]
        monthday = self.analysis_date[4:]
        date_key = f"{year}{monthday}"
        
        downloader_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'JVlinkdownloader'))
        script_path = os.path.join(downloader_path, 'run_downloader.sh')

        if os.path.exists(script_path):
            self.logger.info(f"⚡ [Pulse Refresh] 最新情報を一括要求中... (0B15/0B16)")
            try:
                # 0B15 & 0B16
                subprocess.run([script_path, "--dataspec", "0B15", "--from-time", date_key, "--option", "1"], 
                               cwd=downloader_path, capture_output=True, timeout=120)
                subprocess.run([script_path, "--dataspec", "0B16", "--from-time", date_key, "--option", "1"], 
                               cwd=downloader_path, capture_output=True, timeout=120)
                
                # 💡 データ取得後、予想を最新化する（全レース一括）
                self.logger.info(f"🧠 [Pulse] 取得データに基づき、全レースの予想を再構成しています...")
                subprocess.run([T58_VENV_PY, PREDICTOR_PY, self.analysis_date],
                               cwd=T58_BOX_DIR, capture_output=True, timeout=120)

                return True
            except Exception as e:
                self.logger.warning(f"⚠️  Pulse Refresh失敗: {e}")
        return False

    def sync_wallet_with_ipat(self):
        """IPATの実残高(購入限度額)を取得して、内部の財布残高を同期する"""
        if not self.driver or self.dry_run:
            return False
            
        try:
            limit = self.driver.get_purchase_limit()
            if limit is not None:
                old_balance = self.wallet_balance
                self.wallet_balance = limit
                self.logger.info(f"🔄 財布残高をIPAT実残高と同期しました: {old_balance:,}円 -> {limit:,}円")
                self._refresh_bankroll_mode(force_log=True)
                self.save_state()
                return True
        except Exception as e:
            self.logger.warning(f"⚠️ 財布残高の同期に失敗しました: {e}")
        return False

    def poll_payout_with_0B12(self, jyo_cd, race_num):
        """0B12を使用して払戻を強制取得、確定するまでループ"""
        year = self.analysis_date[:4]
        monthday = self.analysis_date[4:]
        race_key = f"{year}{monthday}{jyo_cd}{str(race_num).zfill(2)}"
        
        # downloaderの場所（親ディレクトリを想定）
        downloader_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'JVlinkdownloader'))
        script_path = os.path.join(downloader_path, 'run_downloader.sh')

        self.logger.info(f"🔍 払戻情報のポーリングを開始します: {race_key}")
        
        start_time = time.time()
        timeout = 600 # 10分
        
        while time.time() - start_time < timeout:
            # 開催当日なら S_HARAI、過去検証なら N_HARAI を参照
            today_str = datetime.now().strftime('%Y%m%d')
            table_name = "S_HARAI" if self.analysis_date == today_str else "N_HARAI"
            
            # DB確認
            conn = self.get_db_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            query = f"SELECT * FROM {table_name} WHERE Year=%s AND MonthDay=%s AND JyoCD=%s AND RaceNum=%s"
            cursor.execute(query, (year, monthday, jyo_cd, str(race_num).zfill(2)))
            result = cursor.fetchone()
            conn.close()
            
            if result and result.get('PayTansyoPay1'):
                self.logger.info(f"✅ 払戻確定を検知しました: {race_key} ({table_name}参照)")
                return result

            # 確定していない場合はコマンド実行
            if os.path.exists(script_path):
                self.logger.info(f"📡 0B12 取得コマンドを実行中... ({race_key})")
                try:
                    # --from-time に RaceKey(12keta) を指定してJVOpen/JVRTOpenを誘発
                    subprocess.run([script_path, "--dataspec", "0B12", "--from-time", race_key, "--option", "1"], 
                                   cwd=downloader_path, capture_output=True, timeout=30)
                except Exception as e:
                    self.logger.warning(f"⚠️  0B12 取得コマンド失敗: {e}")
            else:
                self.logger.warning(f"⚠️  {script_path} が見つかりません。DB待機のみ行います。")

            time.sleep(30) # 30秒待機
            
        self.logger.warning(f"⌛ 払戻ポーリングがタイムアウトしました: {race_key}")
        return None

    def refresh_daily_payouts(self):
        """0B12を使用して当日の全会場の払戻を更新"""
        year = self.analysis_date[:4]
        monthday = self.analysis_date[4:]
        date_key = f"{year}{monthday}"
        
        downloader_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'JVlinkdownloader'))
        script_path = os.path.join(downloader_path, 'run_downloader.sh')

        if os.path.exists(script_path):
            self.logger.info(f"📡 0B12 取得コマンド（当日分一括）を実行中... ({date_key})")
            try:
                # 0B12 (払戻) を 8桁キー(YYYYMMDD) で一括取得
                subprocess.run([script_path, "--dataspec", "0B12", "--from-time", date_key, "--option", "1"], 
                               cwd=downloader_path, capture_output=True, timeout=60)
                return True
            except Exception as e:
                self.logger.warning(f"⚠️  0B12 一括取得失敗: {e}")
        return False

    def refresh_tenko_baba(self):
        """0B15を使用して馬場状態・天候を最新化（S_RACE更新 → 馬場CD補正に反映）"""
        year = self.analysis_date[:4]
        monthday = self.analysis_date[4:]
        date_key = f"{year}{monthday}"

        downloader_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'JVlinkdownloader'))
        script_path = os.path.join(downloader_path, 'run_downloader.sh')

        if os.path.exists(script_path):
            self.logger.info(f"🌤️  [Pre-Vote] 馬場状態(0B15)を最新化中... ({date_key})")
            try:
                subprocess.run([script_path, "--dataspec", "0B15", "--from-time", date_key, "--option", "1"],
                               cwd=downloader_path, capture_output=True, timeout=60)
            except Exception as e:
                self.logger.warning(f"⚠️  0B15 馬場状態取得失敗: {e}")

    def refresh_live_odds(self, jyo_cd, race_num):
        """0B31を使用して発走直前オッズを強制取得"""
        year = self.analysis_date[:4]
        monthday = self.analysis_date[4:]
        race_key = f"{year}{monthday}{jyo_cd}{str(race_num).zfill(2)}"
        
        downloader_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'JVlinkdownloader'))
        script_path = os.path.join(downloader_path, 'run_downloader.sh')

        if os.path.exists(script_path):
            self.logger.info(f"📡 0B31 取得コマンド（直前オッズ）を実行中... ({race_key})")
            try:
                # 0B31 (速報オッズ) を 12桁キーで取得
                subprocess.run([script_path, "--dataspec", "0B31", "--from-time", race_key, "--option", "1"], 
                               cwd=downloader_path, capture_output=True, timeout=30)
                return True
            except Exception as e:
                self.logger.warning(f"⚠️  0B31 取得失敗: {e}")
        return False

    def calculate_race_payout(self, harai, umabans, total_stake, ticket_type='単複'):
        """払戻計算 - 全馬券種対応
        Args:
            harai      : N_HARAI / S_HARAI のレコード dict
            umabans    : 購入馬番のリスト [int, ...]  例: [11], [11,15], [3,5,7]
            total_stake: 実際に投じた合計金額(円)
            ticket_type: CSV の 馬券種 列の値
        """
        year = self.analysis_date[:4]
        def parse_p(val):
            if not val: return 0
            s = str(val).strip()
            try:
                return int(s[:5]) if year == '2026' else int(s)
            except:
                return 0

        if not umabans or not harai:
            return 0

        ub1 = umabans[0]
        payout = 0

        def kumi_sorted(nums):
            """昇順ゼロ埋め2桁連結 (馬連・ワイド・枠連・3連複)"""
            return ''.join(f'{x:02d}' for x in sorted(nums))

        def kumi_ordered(nums):
            """入力順ゼロ埋め2桁連結 (馬単・3連単)"""
            return ''.join(f'{x:02d}' for x in nums)

        def norm(val):
            """DBのKumi値から区切り文字を除去"""
            return str(val or '').replace('-', '').replace(' ', '').strip()

        if ticket_type == '単複':
            win_stake   = int(total_stake * 0.7)
            place_stake = total_stake - win_stake
            if str(harai.get('PayTansyoUmaban1', '')).zfill(2) == str(ub1).zfill(2):
                payout += (win_stake / 100.0) * parse_p(harai.get('PayTansyoPay1'))
            for i in range(1, 4):
                if str(harai.get(f'PayFukusyoUmaban{i}', '')).zfill(2) == str(ub1).zfill(2):
                    payout += (place_stake / 100.0) * parse_p(harai.get(f'PayFukusyoPay{i}'))
                    break

        elif ticket_type == '単勝':
            if str(harai.get('PayTansyoUmaban1', '')).zfill(2) == str(ub1).zfill(2):
                payout = (total_stake / 100.0) * parse_p(harai.get('PayTansyoPay1'))

        elif ticket_type == '複勝':
            for i in range(1, 4):
                if str(harai.get(f'PayFukusyoUmaban{i}', '')).zfill(2) == str(ub1).zfill(2):
                    payout = (total_stake / 100.0) * parse_p(harai.get(f'PayFukusyoPay{i}'))
                    break

        elif ticket_type == '枠連':
            k = kumi_sorted(umabans)
            if norm(harai.get('PayWakurenKumi1')) == k:
                payout = (total_stake / 100.0) * parse_p(harai.get('PayWakurenPay1'))

        elif ticket_type == '馬連':
            k = kumi_sorted(umabans)
            if norm(harai.get('PayUmarenKumi1')) == k:
                payout = (total_stake / 100.0) * parse_p(harai.get('PayUmarenPay1'))

        elif ticket_type == 'ワイド':
            k = kumi_sorted(umabans)
            for i in range(1, 4):
                if norm(harai.get(f'PayWideKumi{i}')) == k:
                    payout = (total_stake / 100.0) * parse_p(harai.get(f'PayWidePay{i}'))
                    break

        elif ticket_type == '馬単':
            k = kumi_ordered(umabans)
            if norm(harai.get('PayUmatanKumi1')) == k:
                payout = (total_stake / 100.0) * parse_p(harai.get('PayUmatanPay1'))

        elif ticket_type == '3連複':
            k = kumi_sorted(umabans)
            if norm(harai.get('PaySanrenpukuKumi1')) == k:
                payout = (total_stake / 100.0) * parse_p(harai.get('PaySanrenpukuPay1'))

        elif ticket_type == '3連単':
            k = kumi_ordered(umabans)
            if norm(harai.get('PaySanrentanKumi1')) == k:
                payout = (total_stake / 100.0) * parse_p(harai.get('PaySanrentanPay1'))

        return payout

    def wait_until_race_time(self, hassojikan_str, minutes_before=5, nowait=False):
        if nowait: return True
        if pd.isna(hassojikan_str) or not hassojikan_str: return True
        hassojikan_str = str(hassojikan_str)
        
        if ':' in hassojikan_str:
            race_time = datetime.strptime(hassojikan_str, '%H:%M').time()
        else:
            # Fallback for HHMM
            t = str(hassojikan_str).zfill(4)
            race_time = datetime.strptime(t, '%H%M').time()
        race_date = datetime.strptime(self.analysis_date, '%Y%m%d').date()
        race_datetime = datetime.combine(race_date, race_time)
        vote_datetime = race_datetime - timedelta(minutes=minutes_before)
        
        while datetime.now() < vote_datetime:
            # 待機中に状態ファイルを監視して勝ち逃げフラグが立ったら即終了できるようにする
            self.load_state()
            if self.stopped: return False
            
            # 💡 パルス・リフレッシュ (Pulse Refresh)
            # 全場の「いずれかのレース」が発走した直後に情報を同期する
            now_dt = datetime.now()
            for pulse_time in self.all_race_times:
                # 発走時刻を過ぎており、かつまだそのパルスを処理していない場合
                if now_dt > pulse_time and pulse_time not in self.processed_pulses:
                    self.logger.info(f"🔔 [Pulse] レース発走を検知しました ({pulse_time.strftime('%H:%M')})。次レース情報を同期します。")
                    self.refresh_all_upcoming_info()
                    self.processed_pulses.add(pulse_time)
                    # 💡 一気に複数が過ぎている場合は一回だけリフレッシュすれば十分
                    break

            diff = (vote_datetime - datetime.now()).total_seconds()
            wait_step = min(30, diff) if diff > 0 else 0
            if wait_step > 0:
                time.sleep(wait_step)
            else:
                break
        
        # 発走時刻を過ぎているかチェック
        if datetime.now() > race_datetime - timedelta(minutes=1):
            return False # 直前すぎる
            
        return True

    def run_vote_sequence(self):
        json_state_path = f'v12_7_voted_races_{self.analysis_date}.json'
        # 💡 プロアクティブ・ブートストラップ (0B15取得 & 初期予想生成)
        if not self.dry_run:
            self.bootstrap_daily_data()

        # 予想ファイルを読み込み (起動時に存在しない場合は待機する)
        csv_filename = os.path.join(self.predictions_dir, f'predictions_{self.analysis_date}.csv')
        self.logger.info(f"🚀 システムを起動しました。予想ファイル ({csv_filename}) を待機しています...")
        
        while not os.path.exists(csv_filename):
            if self.stopped: return
            time.sleep(10)
            if int(time.time()) % 60 < 10:
                self.logger.info(f"⏳ 予想ファイルの生成を待機中... ({datetime.now().strftime('%H:%M:%S')})")

        self.logger.info(f"✅ 予想ファイルを確認しました。運用監視ループを開始します。")

        # ドライバー初期化
        if not self.dry_run:
            self.driver = IPATVoteDriver()
            self.driver.start()
            if not self.driver.login():
                self.logger.error("IPAT Login Failed")
                return
            self.driver.select_normal_bet()

        while True:
            self.load_state()
            if self.stopped:
                self.logger.info("🏁 勝ち逃げ条件達成済みのため終了します")
                break

            # 常に最新の予想を読み込む
            try:
                df = pd.read_csv(csv_filename, encoding='utf-8-sig')
                df = df.sort_values(by=['発走時刻', '競馬場', 'レース番号'])
            except Exception as e:
                self.logger.error(f"⚠️ 予想ファイルの読み込みに失敗しました: {e}")
                time.sleep(30)
                continue

            voted_races = []
            if os.path.exists(json_state_path):
                with open(json_state_path, 'r') as f:
                    voted_races = json.load(f)

            # 未処理のレースがあるか確認
            active_races = []
            now_plus_slack = datetime.now() + timedelta(minutes=1)
            
            for _, row in df.iterrows():
                jyo_name = row['競馬場']
                r_num_str = str(row['レース番号']).replace('R', '')
                race_key = f"{jyo_name}{r_num_str}R"
                
                if race_key in voted_races:
                    continue
                
                hasso = row['発走時刻']
                if pd.isna(hasso): continue
                
                # 時刻パース
                t_str = str(hasso).replace(':', '').zfill(4)
                hasso_dt = datetime.combine(
                    datetime.strptime(self.analysis_date, '%Y%m%d').date(),
                    datetime.strptime(t_str, '%H%M').time()
                )
                
                # 締切（5分前）を過ぎていなければ候補
                if datetime.now() < hasso_dt - timedelta(minutes=5):
                    active_races.append(row)

            if not active_races:
                # 本日の全レースが終了したかチェック
                last_race_time = self.all_race_times[-1] if self.all_race_times else datetime.now()
                if datetime.now() > last_race_time + timedelta(minutes=20):
                    self.logger.info("👋 本日の全レースの監視が終了しました。スクリプトを終了します。")
                    break
                
                # パルス監視を兼ねた待機
                self.logger.info("⏸️  現在、直近の投票対象レースがありません。パルス監視を継続します...")
                self.wait_until_pulse_or_timeout(60)
                continue

            # ── バッチ事前準備: 同一Rナンバーの全場を一括で馬場+オッズ取得・予想 ──
            # 発走15分前に1回だけ実行することで、連続発走時の準備不足を防ぐ
            if not self.dry_run:
                min_race_num = min(
                    int(str(r['レース番号']).replace('R', '')) for r in active_races
                )
                if min_race_num not in self.batch_prepared:
                    group = sorted(
                        [r for r in active_races
                         if int(str(r['レース番号']).replace('R', '')) == min_race_num],
                        key=lambda r: str(r['発走時刻'])
                    )
                    first_hasso_str = str(group[0]['発走時刻']).replace(':', '').zfill(4)
                    first_hasso_dt = datetime.combine(
                        datetime.strptime(self.analysis_date, '%Y%m%d').date(),
                        datetime.strptime(first_hasso_str, '%H%M').time()
                    )
                    batch_trigger_dt = first_hasso_dt - timedelta(minutes=15)

                    if datetime.now() < batch_trigger_dt:
                        # まだバッチ準備時刻に達していない → 待機
                        wait_sec = (batch_trigger_dt - datetime.now()).total_seconds()
                        self.logger.info(
                            f"⏳ [{min_race_num}R 全場一括準備] "
                            f"{batch_trigger_dt.strftime('%H:%M')} まで待機 "
                            f"({wait_sec:.0f}秒後)"
                        )
                        self.wait_until_pulse_or_timeout(min(wait_sec, 60))
                        continue
                    else:
                        # バッチ準備を実行
                        venue_names = ', '.join(r['競馬場'] for r in group)
                        self.logger.info(
                            f"🗂️  [{min_race_num}R 全場一括] "
                            f"馬場+オッズ取得・予想開始 ({venue_names})"
                        )
                        # 0B15 は全場共通で1回のみ
                        self.refresh_tenko_baba()
                        # 各場の0B31を取得
                        for gr in group:
                            g_jyo = self.jyo_map.get(gr['競馬場'])
                            if g_jyo:
                                self.refresh_live_odds(g_jyo, min_race_num)
                        # 全場一括予想 (--race で同一Rナンバーの全場を処理)
                        subprocess.run(
                            [T58_VENV_PY, PREDICTOR_PY,
                             self.analysis_date, "--race", str(min_race_num)],
                            cwd=T58_BOX_DIR, capture_output=True, timeout=120
                        )
                        self.batch_prepared.add(min_race_num)
                        self.logger.info(f"✅ [{min_race_num}R 全場一括] 準備完了")
                        continue  # ループ再開 → 各レースの投票フローへ

            # 最も近いレースを処理
            row = active_races[0]
            jyo_name = row['競馬場']
            race_num = int(str(row['レース番号']).replace('R', ''))
            jyo_cd = self.jyo_map.get(jyo_name)
            race_key = f"{jyo_name}{race_num}R"
            hasso = row['発走時刻']

            # 「見送り」の判定
            status = row.get('ステータス', '')
            ticket_type = row.get('馬券種', '')
            investment = row.get('投資額', 0)
            
            # 見送りであっても、その時間が来るまで待機（パルス取得のため）
            if status == '見送り' or ticket_type == '見送り' or pd.isna(investment) or investment == 0:
                self.logger.info(f"⏳ 次のレース ({jyo_name}{race_num}R {hasso}) は現在「見送り」設定です。発走まで監視を継続します...")
                if not self.wait_until_race_time(hasso, nowait=self.dry_run):
                    voted_races.append(race_key)
                    with open(json_state_path, 'w') as f: json.dump(voted_races, f)
                continue

            # 投票対象の場合
            self.logger.info(f"⏳ 投票予定: {hasso} {jyo_name}{race_num}R")
            if not self.wait_until_race_time(hasso, nowait=self.dry_run):
                voted_races.append(race_key)
                with open(json_state_path, 'w') as f: json.dump(voted_races, f)
                continue
            
            # 💡 投票直前の払戻一括更新とオッズ取得
            if not self.dry_run:
                self.refresh_daily_payouts()
                # 💡 実残高(購入限度額)を同期して財布のズレを補正
                self.sync_wallet_with_ipat()
                # 💡 バッチ準備済みの場合は 0B15(60秒)をスキップ、未準備の場合は個別取得
                if race_num not in self.batch_prepared:
                    self.refresh_tenko_baba()
                else:
                    self.logger.info(
                        f"⚡ [{jyo_name}{race_num}R] バッチ準備済み: 0B15スキップ → 最新オッズのみ更新"
                    )
                self.refresh_live_odds(jyo_cd, race_num)
                # 💡 最新オッズで再計算
                self.logger.info(f"🧠 [Proactive] 投票直前：最新オッズで予想を再計算しています... ({jyo_name}{race_num}R)")
                subprocess.run([T58_VENV_PY, PREDICTOR_PY, self.analysis_date, "--race", str(race_num)],
                               cwd=T58_BOX_DIR, capture_output=True, timeout=60)
                # ファイル更新を待って再読み込み
                time.sleep(1)
                df_latest = pd.read_csv(csv_filename, encoding='utf-8-sig')
                updated_row = df_latest[(df_latest['競馬場'] == jyo_name) & (df_latest['レース番号'] == row['レース番号'])]
                if not updated_row.empty:
                    row = updated_row.iloc[0]
                    status = row.get('ステータス', '')
                    if status == '見送り':
                        self.logger.info(f"⏭️ 最新オッズによる再判定で「見送り」に変わりました。スキップします。")
                        voted_races.append(race_key)
                        with open(json_state_path, 'w') as f: json.dump(voted_races, f)
                        continue

            # 実際の投票プロセスへ（既存ロジックを流用)
            self.execute_vote_process(row, jyo_name, race_num, jyo_cd, race_key, hasso, json_state_path, voted_races)

    def wait_until_pulse_or_timeout(self, timeout_sec):
        """タイムアウトまでパルス監視を行う待機"""
        end_time = time.time() + timeout_sec
        while time.time() < end_time:
            now_dt = datetime.now()
            for pulse_time in self.all_race_times:
                if now_dt > pulse_time and pulse_time not in self.processed_pulses:
                    self.logger.info(f"🔔 [Pulse] レース発走検知 ({pulse_time.strftime('%H:%M')})。情報を同期します。")
                    self.refresh_all_upcoming_info()
                    self.processed_pulses.add(pulse_time)
                    return # リフレッシュしたら一度ループへ戻る
            time.sleep(10)

    def execute_vote_process(self, row, jyo_name, race_num, jyo_cd, race_key, hasso, json_state_path, voted_races):
        """投票・払戻確認・状態更新の一連の流れ"""
        base_stake = row['投資額']
        bankroll_mode = self._refresh_bankroll_mode()
        rollover_amount = self.current_rollover if bankroll_mode == 'rollover' else 0
        total_stake = base_stake + rollover_amount
        
        # 資金補充
        if not self.ensure_wallet_balance(total_stake):
            self.logger.error(f"❌ 最終的な資金不足: {jyo_name}{race_num}R スキップ")
            return

        self.logger.info("="*80)
        # ── 馬券種・購入馬番のパース ──────────────────────────────────────────
        ticket_type = str(row.get('馬券種', '単複')).strip()
        umaban_raw  = str(row.get('購入馬番', '')).strip()
        umabans     = [int(float(x)) for x in umaban_raw.split(',') if x.strip()]
        if not umabans:
            self.logger.error(f"❌ 購入馬番が空です: {jyo_name}{race_num}R")
            return
        umaban = umabans[0]  # 単勝・複勝・単複で使う代表馬番

        # 馬券種 → ドライバー用キー変換表
        TICKET_KEY = {
            '単勝': 'tansho', '複勝': 'fukusho',
            '馬連': 'umaren', '馬単': 'umatan',
            '3連複': 'sanrenpuku', '3連単': 'sanrentan',
        }
        # 複数馬番が必要な馬券種（BOX: 順不同 / NAGASHI: 順序あり）
        MULTI_BOX     = {'馬連', '3連複'}
        MULTI_NAGASHI = {'馬単', '3連単'}

        self.logger.info(
            f"🎯 投票実行: {jyo_name}{race_num}R (モード:{'ころがし' if bankroll_mode == 'rollover' else '定額'} 馬券種:{ticket_type} 馬番:{umabans} Total:{total_stake})"
        )

        success = True
        if not self.dry_run:
            s1 = self.driver.select_course_and_race(jyo_name, race_num, expected_time=hasso)
            if s1:
                if ticket_type == '単複':
                    win_stake   = int(total_stake * 0.7)
                    place_stake = total_stake - win_stake
                    s2 = self.driver.vote_horses([(umaban, win_stake)],   bet_type='tansho',  finalize=False, clear_cart=True)
                    s3 = self.driver.vote_horses([(umaban, place_stake)], bet_type='fukusho', finalize=True,  clear_cart=False, calculated_total=total_stake)
                    success = s2 and s3

                elif ticket_type in ('単勝', '複勝'):
                    bk = TICKET_KEY[ticket_type]
                    if ticket_type == '単勝':
                        # TOP1 / TOP2 / TOP3 に各 base_stake 円ずつ単勝投票
                        bet_umabans = [umaban]
                        for col in ['TOP2', 'TOP3']:
                            raw = str(row.get(col, '')).strip()
                            if raw and raw.lower() not in ('nan', ''):
                                try:
                                    bet_umabans.append(int(float(raw)))
                                except Exception:
                                    pass
                        bet_plan = self.build_tansho_bet_plan(bet_umabans, base_stake, rollover_amount)
                        total_stake = sum(stake for _, stake in bet_plan)
                        self.logger.info(f"  (TOP3単勝プラン: {bet_plan} 計{total_stake}円)")
                        if not self.ensure_wallet_balance(total_stake, '(TOP3)'):
                            self.logger.error(f"❌ 残高不足(TOP3単勝): {self.wallet_balance}円 < {total_stake}円")
                            success = False
                        else:
                            success = True
                            for i, (ub, stake_amount) in enumerate(bet_plan):
                                is_last = (i == len(bet_plan) - 1)
                                ok = self.driver.vote_horses(
                                    [(ub, stake_amount)], bet_type='tansho',
                                    finalize=is_last, clear_cart=(i == 0))
                                if not ok:
                                    success = False
                                    break
                    else:
                        s2 = self.driver.vote_horses([(umaban, total_stake)], bet_type=bk, finalize=True, clear_cart=True, calculated_total=total_stake)
                        success = s2

                elif ticket_type in MULTI_BOX:
                    bk         = TICKET_KEY[ticket_type]
                    horse_list = [(ub, total_stake) for ub in umabans]
                    s2 = self.driver.vote_horses(horse_list, bet_type=bk, formation='BOX', finalize=True, clear_cart=True, calculated_total=total_stake)
                    success = s2

                elif ticket_type in MULTI_NAGASHI:
                    bk         = TICKET_KEY[ticket_type]
                    horse_list = [(ub, total_stake) for ub in umabans]
                    s2 = self.driver.vote_horses(horse_list, bet_type=bk, formation='NAGASHI', finalize=True, clear_cart=True, calculated_total=total_stake)
                    success = s2

                else:
                    self.logger.warning(f"⚠️ 未対応馬券種 '{ticket_type}' → 単複にフォールバック")
                    win_stake   = int(total_stake * 0.7)
                    place_stake = total_stake - win_stake
                    s2 = self.driver.vote_horses([(umaban, win_stake)],   bet_type='tansho',  finalize=False, clear_cart=True)
                    s3 = self.driver.vote_horses([(umaban, place_stake)], bet_type='fukusho', finalize=True,  clear_cart=False, calculated_total=total_stake)
                    success = s2 and s3
                    ticket_type = '単複'  # 払戻計算をフォールバック先に合わせる

                if success: self.driver.handle_continue_voting()
            else:
                success = False

        if success:
            self.logger.info(f"✅ 投票成功: {total_stake}円")
            self.wallet_balance -= total_stake
            voted_races.append(race_key)
            with open(json_state_path, 'w') as f: json.dump(voted_races, f)

            # 💡 払戻ポーリングをバックグラウンドスレッドで実行
            # → メインスレッドはすぐ次のレース処理へ進める
            def _poll_in_background(jcd, rnum, ubs, stake, ttype, rkey):
                harai = self.poll_payout_with_0B12(jcd, rnum)
                with self._payout_lock:
                    if harai:
                        payout = self.calculate_race_payout(harai, ubs, stake, ttype)
                        self.logger.info(f"💰 払戻確定[BG]: {rkey} {payout:.0f}円")
                        self.wallet_balance += payout
                        next_mode = self._refresh_bankroll_mode(force_log=True)
                        if payout > 0:
                            if self.use_rollover and next_mode == 'rollover':
                                self.current_rollover = payout
                                self.logger.info(f"🔁 ころがし継続: 次走へ {payout:.0f}円 を再投資します")
                            else:
                                self.current_rollover = 0
                                if self.use_rollover and self.target_balance_amount > 0:
                                    self.logger.info("🛡️ 目標残高に到達したため、以降は定額運用へ切り替えます")
                                elif self.get_managed_balance() >= (self.total_invested_cash * self.threshold):
                                    self.stopped = True
                                    self.logger.info(f"🎊 勝ち逃げ成功！")
                        else:
                            self.current_rollover = 0
                        self.save_state()
                    else:
                        self.current_rollover = 0

            t = threading.Thread(
                target=_poll_in_background,
                args=(jyo_cd, race_num, umabans, total_stake, ticket_type, race_key),
                daemon=True
            )
            t.start()
        else:
            self.logger.error("❌ 投票失敗")
            alert_msg = (
                "\n"
                "╔══════════════════════════════════════════════════╗\n"
                f"║  🚨 投票失敗: {race_key:<36} ║\n"
                "║  IPATの投票履歴を確認してください（投票されていない可能性）║\n"
                "╚══════════════════════════════════════════════════╝"
            )
            print(alert_msg, flush=True)

        self.save_state()
        time.sleep(5)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 equine_edge_auto_vote_v12_7.py YYYYMMDD [--dry-run]")
        sys.exit(1)
    
    date_str = sys.argv[1]
    is_dry = '--dry-run' in sys.argv
    
    # 安定化設定：しきい値1.5(150%)で実行
    system = AutoVoteSystemV12_7(date_str, dry_run=is_dry, threshold=1.5)
    system.run_vote_sequence()
