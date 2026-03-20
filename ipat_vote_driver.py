#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IPAT自動投票ドライバー V2
Zennの記事（https://zenn.dev/_lambda314/articles/e4ceaa81b045c5）を参考にした実装
"""

import os
import re
import time
import logging
import datetime
import subprocess
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

logger = logging.getLogger(__name__)

class IPATVoteDriver:
    def __init__(self):
        load_dotenv()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # 起動前に既存のブラウザプロセスをクリーンアップ (User要請)
        self._cleanup_previous_chrome()
        
        # 待機時間設定
        self.wait_sec = 2
        
        # 競馬場リスト（Zennの記事を参考）
        self.place_lst = ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]
        
        # 曜日リスト
        self.dow_lst = ["月", "火", "水", "木", "金", "土", "日"]
        
        # Chrome オプション設定
        chrome_options = Options()
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--disable-features=VizDisplayCompositor")
        
        # ヘッドレスモードの設定 (デフォルトはTrue)
        is_headless = os.getenv("HEADLESS", "True") == "True"
        if is_headless:
            logger.info("🌐 ヘッドレスモードでブラウザを起動します")
            chrome_options.add_argument("--headless=new")
        else:
            logger.info("🌐 通常モード（画面表示）でブラウザを起動します")
        
        try:
            # ChromeDriverのパスを優先順位をつけて確認
            chromedriver_candidates = [
                os.path.join(base_dir, "chromedriver-linux64", "chromedriver"),
                "/mnt/ec8c980f-4065-45d3-bb42-e85e6200f46e/EquineProject/SynologyDrive/T58_Box/chromedriver-linux64/chromedriver",
                "/usr/local/bin/chromedriver"
            ]
            
            chromedriver_path = None
            for path in chromedriver_candidates:
                if os.path.exists(path):
                    chromedriver_path = path
                    break
            
            if chromedriver_path:
                service = Service(chromedriver_path)
                self.driver = webdriver.Chrome(service=service, options=chrome_options)
                logger.info(f"✅ 明示的パスでChromeDriverを設定: {chromedriver_path}")
            else:
                # フォールバック: システムパス
                self.driver = webdriver.Chrome(options=chrome_options)
                logger.info("✅ システムパスのChromeDriverを使用")
        except Exception as e:
            logger.error(f"❌ ChromeDriver設定失敗: {e}")
            raise
        
        self.wait = WebDriverWait(self.driver, 30)  # タイムアウト延長
        
        # 環境変数の取得（.envファイルと整合性をとる）
        self.inet_id = os.getenv("USER_ID") or ""        # INETID
        self.kanyusha_no = os.getenv("JRA_ID") or ""     # 加入者番号  
        self.password_pat = os.getenv("PASSWORD") or ""   # PATのパスワード
        self.pras_no = os.getenv("BIRTH_CODE") or ""     # P-RAS番号
        self.confirm_vote = os.getenv("CONFIRM_VOTE", "True") == "True"
        self.last_vote_cancelled = False
        self.last_vote_status = "idle"
        self.last_vote_message = ""
        
        # IPATのURL
        self.pat_url = "https://www.ipat.jra.go.jp/index.cgi"
        
        # IPAT URL
        self.pat_url = "https://www.ipat.jra.go.jp/index.cgi"

    def _cleanup_previous_chrome(self):
        """前回起動したGoogle ChromeやChromeDriverの残骸をクリーンアップする"""
        try:
            logger.info("🧹 既存のブラウザプロセスを確認中...")
            
            # chromeとchromedriverの両方をターゲットにする
            process_names = ["google-chrome", "chrome", "chromedriver"]
            
            found_any = False
            for name in process_names:
                # pgrepでプロセスが存在するか確認
                result = subprocess.run(["pgrep", "-f", name], capture_output=True, text=True)
                if result.stdout.strip():
                    logger.info(f"⚠️ 実行中の '{name}' プロセスを検知しました。終了を試みます。")
                    subprocess.run(["pkill", "-9", "-f", name])
                    found_any = True
            
            if found_any:
                time.sleep(2) # 終了を待機
                logger.info("✅ 既存プロセスのクリーンアップが完了しました。")
            else:
                logger.info("ℹ️ 実行中のブラウザプロセスはありません。")
                
        except Exception as e:
            logger.warning(f"⚠️ クリーンアップ処理中にエラー（無視して続行）: {e}")

    def judge_day_of_week(self, date_nm):
        """曜日判定（Zennの記事を参考）"""
        date_dt = datetime.datetime.strptime(str(date_nm), "%Y%m%d")
        nm = date_dt.isoweekday()
        return self.dow_lst[nm - 1]
    
    def click_css_selector(self, selector, nm=0):
        """CSSセレクタークリック（Zennの記事を参考）"""
        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
        if len(elements) > nm:
            self.driver.execute_script("arguments[0].click();", elements[nm])
            time.sleep(self.wait_sec)
        else:
            filename = f"click_error_{datetime.datetime.now().strftime('%H%M%S')}.png"
            try:
                self.driver.save_screenshot(filename)
                logger.error(f"📸 セレクター {selector} が見つからないためスクリーンショットを保存しました: {filename}")
            except:
                pass
            raise Exception(f"セレクター {selector} の {nm} 番目の要素が見つかりません (URL: {self.driver.current_url})")

    def start(self):
        """IPAT接続開始"""
        try:
            logger.info("🌐 IPAT サイトに接続中...")
            self.driver.get(self.pat_url)
            logger.info("✅ IPAT サイト接続完了")
        except Exception as e:
            logger.error(f"❌ IPAT接続失敗: {e}")
            raise

    def login(self):
        """IPATログイン（Zennの記事を参考）"""
        try:
            logger.info("🔐 IPAT ログイン中...")
            
            # INETIDを入力
            inet_id_input = self.wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[name^='inetid']")))
            inet_id_input.send_keys(self.inet_id)
            
            # 次へボタン
            self.click_css_selector("a[onclick^='javascript']", 0)
            time.sleep(self.wait_sec)
            
            # 加入者番号、パスワード、P-RAS番号を入力
            password_input = self.wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[name^='p']")))
            password_input.send_keys(self.password_pat)
            
            jra_id_inputs = self.driver.find_elements(By.CSS_SELECTOR, "input[name^='i']")
            if len(jra_id_inputs) >= 3:
                jra_id_inputs[2].send_keys(self.kanyusha_no)
            
            birth_inputs = self.driver.find_elements(By.CSS_SELECTOR, "input[name^='r']")
            if len(birth_inputs) >= 2:
                birth_inputs[1].send_keys(self.pras_no)
            
            # ログインボタン
            self.click_css_selector("a[onclick^='JavaScript']", 0)
            
            # お知らせページの処理
            time.sleep(3)
            if "announce" in self.driver.current_url:
                try:
                    # 優先度の高い順に OK ボタンなどを探す
                    found_ok = False
                    # 1. まずは「OK」や「確認」などのテキストを持つボタン/リンクを探す
                    elements = self.driver.find_elements(By.CSS_SELECTOR, "button, a, input[type='button']")
                    for el in elements:
                        try:
                            text = (el.text or "").strip() or el.get_attribute("value") or ""
                            # OKボタン、メニューボタン、閉じるボタンなどのテキストをチェック
                            if any(target in text for target in ["OK", "ＯＫ", "確認", "メニュー", "閉じる"]):
                                logger.info(f"🎯 お知らせページのボタンを発見: '{text}'")
                                self.driver.execute_script("arguments[0].click();", el)
                                time.sleep(3)
                                found_ok = True
                                break
                        except:
                            continue
                except Exception as e:
                    logger.warning(f"⚠️ お知らせページの処理中にエラーが発生しました: {e}")
            
            # URLに announce が含まれなくなるまで少し待機
            for _ in range(5):
                if "announce" not in self.driver.current_url:
                    break
                time.sleep(1)
                
            # メッセージ（メンテナンス、時間外等）の有無をチェック
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            maintenance_keywords = ["メンテナンス", "サービス時間外", "受付時間外", "運用時間外"]
            for kw in maintenance_keywords:
                if kw in page_text:
                    logger.error(f"❌ IPATサービス制限を検知しました: {kw}")
                    raise RuntimeError(f"IPATサービス時間外またはメンテナンス中です ({kw})")

            logger.info(f"✅ ログイン後の画面に到達しました。URL: {self.driver.current_url}")
            return True
            
        except Exception as e:
            if isinstance(e, RuntimeError):
                logger.error(f"🚫 サービス時間外: {e}")
            else:
                logger.error(f"❌ IPATログイン失敗: {e}")
            
            # エラー発生時のスクリーンショット保存
            try:
                timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"login_error_{timestamp}.png"
                self.driver.save_screenshot(filename)
                logger.info(f"📸 エラー時のスクリーンショットを保存しました: {filename}")
            except:
                pass
            raise

    def get_purchase_limit(self):
        """購入限度額を取得する"""
        try:
            # ログイン直後のメニュー画面や通常投票画面に表示されている
            self.clear_popups()
            
            selectors = [
                "span[ng-bind*='purchaseLimit']",
                "//th[contains(text(), '購入限度額')]/following-sibling::td",
                "//div[contains(text(), '購入限度額')]/following-sibling::div//span"
            ]
            
            for selector in selectors:
                try:
                    if selector.startswith("//"):
                        el = self.driver.find_element(By.XPATH, selector)
                    else:
                        el = self.driver.find_element(By.CSS_SELECTOR, selector)
                    
                    if el.is_displayed():
                        text = el.text.replace(",", "").replace("円", "").strip()
                        if text:
                            limit = int(text)
                            logger.info(f"💰 購入限度額を取得しました: {limit:,}円")
                            return limit
                except:
                    continue

            try:
                body_text = self.driver.find_element(By.TAG_NAME, "body").text
                match = re.search(r"購入限度額\s*([0-9,]+)円", body_text)
                if match:
                    limit = int(match.group(1).replace(",", ""))
                    logger.info(f"💰 購入限度額を本文テキストから取得しました: {limit:,}円")
                    return limit
            except Exception:
                pass
            
            logger.warning("⚠️ 購入限度額の取得に失敗しました（要素が見つかりません）")
            return None
        except Exception as e:
            logger.error(f"❌ 購入限度額取得エラー: {e}")
            return None

    def select_normal_bet(self):
        """通常投票方式を選択"""
        try:
            logger.info("🎯 通常投票を選択中...")
            
            # ページ読み込み待機
            time.sleep(2)
            
            # ボタンを探す（複数パターン）
            found = False
            
            # パターン1: テキストで探す（最も確実）
            elements = self.driver.find_elements(By.CSS_SELECTOR, "button, a")
            for el in elements:
                try:
                    if "通常投票" in el.text:
                        logger.info("🎯 テキスト '通常投票' でボタンを発見しました")
                        self.driver.execute_script("arguments[0].click();", el)
                        found = True
                        break
                except:
                    continue
            
            # パターン2: セレクターで探す
            if not found:
                try:
                    self.click_css_selector("button[href^='#!/bet/basic'], a[href^='#!/bet/basic']", 0)
                    found = True
                except:
                    pass
            
            if not found:
                raise Exception(f"通常投票ボタンが見つかりません。URL: {self.driver.current_url}")

            time.sleep(2)
            self._continue_without_charge_if_needed()
                
            logger.info("✅ 通常投票選択完了")
        except Exception as e:
            logger.error(f"❌ 通常投票選択失敗: {e}")
            raise

    def _continue_without_charge_if_needed(self):
        """残高 0 円時のチャージ案内で「このまま進む」を押す"""
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            if "このまま進む" not in body_text:
                return False

            elements = self.driver.find_elements(By.CSS_SELECTOR, "button, a, input[type='button']")
            for el in elements:
                try:
                    if not el.is_displayed():
                        continue
                    text = (el.text or "").strip() or el.get_attribute("value") or ""
                    if "このまま進む" in text:
                        logger.info("🎯 残高不足案内の『このまま進む』をクリックします")
                        self.driver.execute_script("arguments[0].click();", el)
                        time.sleep(2)
                        return True
                except:
                    continue
            return False
        except Exception as e:
            logger.warning(f"⚠️ 残高不足案内の処理中にエラーが発生しました: {e}")
            return False

    def ensure_logged_in(self):
        """ログイン状態を確認し、必要であれば再ログインする"""
        try:
            # INETID入力フィールドが存在するかチェック
            # (URLに pw_890_i.cgi が含まれていてもログイン済みのケースがあるため、URLだけでなく要素で判断)
            login_elements = self.driver.find_elements(By.CSS_SELECTOR, "input[name^='inetid']")
            is_logged_out = len(login_elements) > 0 and login_elements[0].is_displayed()
            
            if is_logged_out:
                logger.warning("🔄 ログイン画面（またはセッション切れ）を検知しました。再ログインを試みます。")
                if self.login():
                    self.select_normal_bet()
                    return True
                return False
            return True
        except Exception as e:
            logger.error(f"⚠️ ログイン状態確認中にエラーが発生しました: {e}")
            return False

    def clear_popups(self, timeout=None):
        """お知らせや締切、組合せ確認などのポップアップをクリアする"""
        try:
            # 頻出するボタンのテキストやセレクター
            target_texts = ["OK", "ＯＫ", "確認", "閉じる", "手続きを完了する", "終了", "戻る"]
            
            # 1. まずはオーバーレイやダイアログのボタンを探す
            found_popup = False
            
            # セレクターでのクイックチェック
            selectors = [
                "div.ui-dialog button", 
                "div.ui-popup button", 
                "button.ui-btn-active",
                "a[ng-click*='dismiss']",
                "button[ng-click*='dismiss']",
                "button[ng-click*='close']",
                "button.ui-btn-icon-notext", # 閉じるアイコン
                "a.ui-btn-icon-notext"
            ]
            
            for selector in selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for el in elements:
                        if el.is_displayed():
                            text = (el.text or "").strip()
                            if not text:
                                text = el.get_attribute("value") or ""
                                
                            if any(target in text for target in target_texts):
                                logger.info(f"🎯 ポップアップボタンをセレクターで検知: '{text}'")
                                self.driver.execute_script("arguments[0].click();", el)
                                time.sleep(1)
                                found_popup = True
                                break
                    if found_popup: break
                except:
                    continue

            if found_popup:
                return True

            # 2. 全てのボタン/リンクをスキャン（重いが確実）
            elements = self.driver.find_elements(By.CSS_SELECTOR, "button, a, input[type='button']")
            for el in elements:
                try:
                    if not el.is_displayed():
                        continue
                        
                    text = (el.text or "").strip() or el.get_attribute("value") or ""
                    if any(target in text for target in target_texts):
                        # 特定の特定のポップアップ（組合せ確認など）に特化した判定
                        logger.info(f"🎯 ポップアップを検知・クリアします: '{text}'")
                        self.driver.execute_script("arguments[0].click();", el)
                        time.sleep(1)
                        found_popup = True
                        break
                except:
                    continue
            return found_popup
        except Exception as e:
            logger.warning(f"⚠️ ポップアップクリア中にエラーが発生しました: {e}")
            return False

    def _get_visible_dialog_text(self):
        selectors = [
            "div.ui-dialog",
            "div.ui-popup",
            "div.ui-popup-container",
            "div[role='dialog']",
            "div.modal",
            "div.modal-dialog",
        ]

        texts = []
        for selector in selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if not el.is_displayed():
                        continue
                    text = (el.text or "").strip()
                    if text:
                        texts.append(text)
            except Exception:
                continue

        return "\n".join(texts).strip()

    def _classify_purchase_dialog(self):
        try:
            alert = self.driver.switch_to.alert
            text = alert.text or ""
            if any(keyword in text for keyword in ["購入限度額を超えました", "残高不足"]):
                return "insufficient_funds", text, "alert"
            if any(keyword in text for keyword in ["締切", "受付時間外", "発売を終了"]):
                return "cutoff", text, "alert"
            return "confirm", text, "alert"
        except Exception:
            pass

        dialog_text = self._get_visible_dialog_text()
        if any(keyword in dialog_text for keyword in ["購入限度額を超えました", "残高不足"]):
            return "insufficient_funds", dialog_text, "dialog"
        if any(keyword in dialog_text for keyword in ["締切", "受付時間外", "発売を終了"]):
            return "cutoff", dialog_text, "dialog"
        if dialog_text:
            return "confirm", dialog_text, "dialog"
        return "none", "", "none"

    def _click_purchase_dialog_ok(self, confirm_texts=None):
        confirm_texts = confirm_texts or ["OK", "ＯＫ", "確認", "はい", "購入"]

        try:
            alert = self.driver.switch_to.alert
            alert_text = alert.text
            logger.info(f"🔔 ブラウザalertを検知: '{alert_text}' → accept()")
            alert.accept()
            return True
        except Exception:
            pass

        for attempt in range(3):
            try:
                dialog_buttons = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    "button[ng-click*='close'], button[ng-click*='dismiss'], div.ui-dialog button, div.ui-popup button",
                )
                for btn in dialog_buttons:
                    if not btn.is_displayed():
                        continue
                    text = (btn.text or "").strip()
                    if text in confirm_texts:
                        logger.info(f"🎯 購入ダイアログの '{text}' をクリックします (試行 {attempt + 1})")
                        self.driver.execute_script("arguments[0].click();", btn)
                        return True
            except Exception:
                pass
            time.sleep(1)

        try:
            all_buttons = self.driver.find_elements(By.CSS_SELECTOR, "button, a, input[type='button']")
            for btn in all_buttons:
                if not btn.is_displayed():
                    continue
                text = (btn.text or "").strip() or btn.get_attribute("value") or ""
                if text in confirm_texts:
                    logger.info(f"🎯 全ボタンスキャンで '{text}' を発見してクリック")
                    self.driver.execute_script("arguments[0].click();", btn)
                    return True
        except Exception:
            pass

        return False

    def _prompt_for_manual_top_up(self, dialog_text, actual_total_yen):
        target_amount = f"{actual_total_yen:,}円" if actual_total_yen else "不明"
        print(
            "\n"
            "╔══════════════════════════════════════════════╗\n"
            "║  残高不足です。入金後に処理を再開できます。    ║\n"
            "╠══════════════════════════════════════════════╣\n"
            f"║  投票金額: {target_amount:<30}║\n"
            "║  入金後は ok、終了する場合は end を入力。     ║\n"
            "╚══════════════════════════════════════════════╝"
        , flush=True)
        if dialog_text:
            print(dialog_text, flush=True)

        while True:
            response = input("\n入金後に再試行する場合は ok、終了する場合は end: ").strip().lower()
            if response in {"ok", "end"}:
                return response
            print("ok または end を入力してください。", flush=True)

    def _finalize_purchase(self, actual_total_yen):
        confirm_texts = ["OK", "ＯＫ", "確認", "はい", "購入"]

        while True:
            logger.info("🚀 投票実行ボタン（購入）をクリックします")
            self.click_css_selector("button[ng-click^='vm.clickPurchase()']", 0)
            time.sleep(2)

            try:
                diag_buttons = self.driver.find_elements(By.CSS_SELECTOR, "button")
                visible_btns = [(b.text.strip(), b.get_attribute('ng-click'), b.is_displayed()) for b in diag_buttons]
                logger.info(f"🔍 [診断] 購入後ボタン一覧({len(visible_btns)}個): {visible_btns}")
            except Exception as diag_e:
                logger.warning(f"⚠️ [診断] ボタン一覧取得失敗: {diag_e}")

            dialog_kind, dialog_text, _dialog_source = self._classify_purchase_dialog()
            if dialog_kind == "insufficient_funds":
                logger.warning("⚠️ IPATで残高不足ダイアログを検知しました")
                action = self._prompt_for_manual_top_up(dialog_text, actual_total_yen)
                self._click_purchase_dialog_ok(confirm_texts)
                time.sleep(1)
                if action == "ok":
                    logger.info("🔁 入金後の再試行を開始します")
                    continue
                self.last_vote_status = "operator_ended"
                self.last_vote_message = "ended by operator after insufficient funds"
                return False

            if dialog_kind == "cutoff":
                logger.warning("⚠️ IPATで締切ダイアログを検知しました")
                self._click_purchase_dialog_ok(confirm_texts)
                self.last_vote_status = "cutoff"
                self.last_vote_message = dialog_text or "ipat cutoff"
                return False

            success_final = self._click_purchase_dialog_ok(confirm_texts)
            if not success_final:
                msg = (
                    "\n"
                    "╔══════════════════════════════════════════╗\n"
                    "║  🚨 投票未完了: 購入確認OKボタンが見つかりません  ║\n"
                    "║  IPAT画面でボタンを手動で確認してください          ║\n"
                    "╚══════════════════════════════════════════╝"
                )
                print(msg, flush=True)
                logger.critical("🚨 [投票未完了] 購入確認OKボタンが特定できませんでした。IPATに投票が記録されていない可能性があります。")
                self.last_vote_status = "error"
                self.last_vote_message = "purchase confirmation button not found"
                return False

            logger.info("✅ 購入確認OKボタンのクリックに成功しました")
            time.sleep(2)

            result_kind, result_text, _result_source = self._classify_purchase_dialog()
            if result_kind == "insufficient_funds":
                logger.warning("⚠️ 購入確認後に残高不足ダイアログを検知しました")
                action = self._prompt_for_manual_top_up(result_text, actual_total_yen)
                self._click_purchase_dialog_ok(confirm_texts)
                time.sleep(1)
                if action == "ok":
                    logger.info("🔁 入金後の再試行を開始します")
                    continue
                self.last_vote_status = "operator_ended"
                self.last_vote_message = "ended by operator after insufficient funds"
                return False

            if result_kind == "cutoff":
                logger.warning("⚠️ 購入確認後に締切ダイアログを検知しました")
                self._click_purchase_dialog_ok(confirm_texts)
                self.last_vote_status = "cutoff"
                self.last_vote_message = result_text or "ipat cutoff"
                return False

            self.last_vote_status = "executed"
            self.last_vote_message = "purchase submitted"
            return True

    def handle_continue_voting(self):
        """「続けて投票する」ボタンをクリックして通常投票画面に戻る"""
        try:
            # 1. テキストで探す
            elements = self.driver.find_elements(By.CSS_SELECTOR, "button, a")
            for el in elements:
                if el.is_displayed() and "続けて投票する" in el.text:
                    logger.info("🎯 '続けて投票する' ボタンを発見しました")
                    self.driver.execute_script("arguments[0].click();", el)
                    time.sleep(2)
                    return True
            
            # 2. セレクターで探す（IPATの仕様が変わった場合用）
            selectors = ["button.ui-btn-active", "a.ui-btn-active"]
            for selector in selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed() and "続けて" in el.text:
                        logger.info(f"🎯 セレクター {selector} で続けて投票ボタンを発見")
                        self.driver.execute_script("arguments[0].click();", el)
                        time.sleep(2)
                        return True
            return False
        except Exception as e:
            logger.warning(f"⚠️ '続けて投票する' の処理中にエラーが発生しました: {e}")
            return False

    def select_course_and_race(self, jyo_name, race_num, expected_time=None):
        """
        競馬場とレースを順に選択 (equine_edge_auto_vote_v9.py 互換用)
        """
        try:
            # セッションチェックと自動復旧
            if not self.ensure_logged_in():
                logger.error("❌ セッション復旧に失敗しました")
                return None
                
            self.select_course(jyo_name)
            self.select_race(race_num)
            # 現状は実際の画面から取得せず、期待値を返す
            return expected_time
        except Exception as e:
            logger.error(f"❌ 競馬場・レース選択エラー: {e}")
            return None

    def select_course(self, jyo_name):
        """競馬場を選択（実際のIPAT要素調査結果に基づく）"""
        try:
            # 操作前にポップアップをクリア
            self.clear_popups()
            
            logger.info(f"🏟️ 競馬場選択中: {jyo_name}")
            
            # 曜日判定
            today = datetime.datetime.now()
            dow = self.dow_lst[today.weekday()]
            
            # もしログイン直後の「通常投票」などのメニューにいない場合を考慮
            # 競馬場選択ボタンが見つからない場合、一度メニューに戻ってみる
            max_retries = 2
            for retry in range(max_retries):
                course_buttons = self.driver.find_elements(By.CSS_SELECTOR, "button[ng-click*='selectCourse']")
                # 表示されているボタンを抽出
                active_buttons = [b for b in course_buttons if b.is_displayed()]
                
                if not active_buttons and retry == 0:
                    logger.warning("⚠️ 競馬場選択ボタンが見つかりません。メニューへの復帰を試みます。")
                    self.handle_continue_voting() # もし「続けて投票する」状態なら
                    self.clear_popups()
                    time.sleep(2)
                    continue
                
                for button in active_buttons:
                    button_text = button.text.strip()
                    # 競馬場名（阪神、中山など）が含まれているか、または特定のクラスを持っているか
                    # IPATのボタンテキストは「阪神」「阪神（土）」「阪神(土)」など揺れがある
                    if jyo_name in button_text:
                        # 曜日が一致するか、または曜日指定がない場合にマッチ
                        if not dow or (f"（{dow}）" in button_text or f"({dow})" in button_text or retry > 0):
                            logger.info(f"🎯 一致するボタンを発見: {button_text}")
                            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                            time.sleep(1)
                            self.driver.execute_script("arguments[0].click();", button)
                            time.sleep(self.wait_sec)
                            return
            
            raise Exception(f"競馬場 {jyo_name} が見つかりません。")
            
        except Exception as e:
            logger.error(f"❌ 競馬場選択失敗: {jyo_name} - {e}")
            raise

    def select_race(self, race_num):
        """レース番号を選択（実際のIPAT要素調査結果に基づく）"""
        try:
            # 操作前にポップアップをクリア
            self.clear_popups()
            
            logger.info(f"🏁 レース選択中: {race_num}R")
            
            # レース選択ボタンを探す
            race_buttons = self.driver.find_elements(By.CSS_SELECTOR, 
                "button[ng-click*='selectRace']")
            
            for i, button in enumerate(race_buttons):
                try:
                    # 表示されているボタンのみを対象にする
                    if not button.is_displayed():
                        continue
                        
                    button_text = button.text.strip()
                    # レース番号を検索（"1R", "2R"...の形式）
                    if f"{race_num}R" in button_text or f"{race_num}" == button_text:
                        logger.info(f"🎯 一致するレースボタンを発見: {race_num}R")
                        self.driver.execute_script("arguments[0].click();", button)
                        time.sleep(self.wait_sec)
                        return
                except:
                    continue
            
            raise Exception(f"レース {race_num}R が見つかりません")
            
        except Exception as e:
            logger.error(f"❌ レース選択失敗: {race_num}R - {e}")
            raise
            
    def select_bet_type(self, bet_type_key):
        """馬券種を選択（ドロップダウン対応・超堅牢版）"""
        bet_type_map = {
            'tansho':    '単勝',
            'fukusho':   '複勝',
            'umaren':    '馬連',
            'umatan':    '馬単',
            'sanrenpuku': '3連複',
            'sanrentan': '3連単',
        }
        
        target_text = bet_type_map.get(bet_type_key)
        if not target_text:
            logger.warning(f"⚠️ 未対応の馬券種キー: {bet_type_key}")
            return False
            
        try:
            logger.info(f"🎫 馬券種を選択中: {target_text}")
            from selenium.webdriver.support.ui import Select
            
            # 馬券種（式別）ドロップダウンに特化したセレクター
            selectors = [
                "select[ng-model*='selectedBetType']",
                "select[ng-model*='vm.selectedBetType']",
                "select[name='betType']",
                "//div[contains(text(), '式別')]/following-sibling::div//select",
                "//span[contains(text(), '式別')]/following-sibling::select"
            ]
            
            dropdown_element = None
            for selector in selectors:
                try:
                    if selector.startswith("//"):
                        el = self.driver.find_element(By.XPATH, selector)
                    else:
                        el = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if el.is_displayed():
                        dropdown_element = el
                        break
                except: continue
            
            if not dropdown_element:
                # 最終手段：ページ内の全 select をチェック
                selects = self.driver.find_elements(By.CSS_SELECTOR, "select")
                for s in selects:
                    if s.is_displayed() and ("単勝" in s.text or "単　勝" in s.text):
                        dropdown_element = s
                        break
            
            if not dropdown_element:
                dropdown_element = self.wait.until(EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, "select[ng-model*='selectedBetType']")))
            
            select = Select(dropdown_element)
            
            # 既に選択されているか確認
            try:
                current_selected = select.first_selected_option.text.replace(" ", "").replace("　", "").strip()
                if target_text in current_selected:
                    logger.info(f"✅ すでに {target_text} が選択されています")
                    return True
            except: pass

            # 選択肢のテキストを正規化してマッチング
            found_option = None
            for option in select.options:
                normalized_opt = option.text.replace(" ", "").replace("　", "").strip()
                if target_text in normalized_opt:
                    found_option = option.text
                    break
            
            if found_option:
                self.driver.execute_script("arguments[0].scrollIntoView(true);", dropdown_element)
                time.sleep(0.5)
                select.select_by_visible_text(found_option)
                logger.info(f"✅ 馬券種を選択しました: {found_option}")
                # 選択反映の待ち時間を増やす
                time.sleep(2.5)
                return True
            else:
                logger.error(f"❌ 馬券種オプションが見つかりません: {target_text}")
                logger.info(f"📋 利用可能なオプション: {[opt.text for opt in select.options]}")
                return False
        except Exception as e:
            logger.error(f"❌ 馬券種選択エラー: {e}")
            return False

    def select_method(self, method_key):
        """投票方式を選択（通常/BOX/流し）"""
        method_map = {'SINGLE': '通常', 'BOX': 'ボックス', 'NAGASHI': 'ながし'}
        target_text = method_map.get(method_key)
        if not target_text: return False
            
        try:
            # 選択前に一度ポップアップを掃除
            self.clear_popups()
            logger.info(f"📋 投票方式を選択中: {target_text}")

            dropdown = None
            method_selectors = [
                "select[ng-model*='selectedBetMethod']",
                "select[ng-model*='oSelectMethod']",
                "#bet-basic-method",
            ]
            for selector in method_selectors:
                try:
                    candidate = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
                    if candidate.is_displayed():
                        dropdown = candidate
                        break
                except:
                    continue

            if dropdown is None:
                raise Exception("投票方式の選択ドロップダウンが見つかりません")

            from selenium.webdriver.support.ui import Select
            select = Select(dropdown)
            
            if target_text in select.first_selected_option.text:
                return True

            # 部分マッチで選択肢を探す（「ながし」→「軸1頭ながし」「1着ながし」等にマッチ）
            found_option = None
            for option in select.options:
                if target_text in option.text:
                    found_option = option.text
                    break
            if found_option:
                select.select_by_visible_text(found_option)
                logger.info(f"✅ 投票方式を選択: {found_option}")
            else:
                logger.warning(f"⚠️ 投票方式オプションが見つかりません: {target_text} / 利用可能: {[o.text for o in select.options]}")
            time.sleep(1.5)
            # 選択後に確認ダイアログ（組合せ確認など）が出たら消す
            self.clear_popups()
            return True
        except Exception as e:
            logger.warning(f"⚠️ 投票方式選択エラー: {e}")
            self.clear_popups()
            return False

    def vote_horses(self, horse_amount_list, bet_type=None, formation='SINGLE', finalize=True, clear_cart=True, calculated_total=None):
        """
        改良版投票システム - 個別馬番・金額処理 + クリア処理対応
        
        Args:
            horse_amount_list: List of (umaban, amount_in_yen)
            bet_type: Horse bet type (tansho, fukusho, wide, etc.)
            formation: SINGLE, BOX, NAGASHI
            finalize: If True, execute the purchase. If False, just add to cart.
            clear_cart: If True, clear existing bets before starting.
            calculated_total: Explicitly calculated total amount (used as fallback for finalizing).
        """
        try:
            self.last_vote_cancelled = False
            self.last_vote_status = "running"
            self.last_vote_message = ""
            logger.info(f"🎯 投票開始: {len(horse_amount_list)}頭 ({bet_type} / {formation}) [finalize={finalize}, clear_cart={clear_cart}]")
            
            # 1. まずは既存の投票があればクリアする (UI衝突回避の最優先事項)
            if clear_cart:
                try:
                    # 操作前にポップアップをクリアしておく
                    self.clear_popups()
                    clear_all_button = self.driver.find_element(By.CSS_SELECTOR, "button[ng-click*='clear']")
                    if clear_all_button.is_displayed():
                        self.driver.execute_script("arguments[0].click();", clear_all_button)
                        logger.info("🧹 投票開始前の全クリア実行")
                        time.sleep(2)
                        # クリア後の確認ポップアップ等が出る場合があるため再度ケア
                        self.clear_popups()
                except:
                    # logger.info("ℹ️ 全クリアボタンが見つからないか、既にクリア済み")
                    pass
            
            # 2. 馬券種を選択
            if bet_type:
                self.select_bet_type(bet_type)
            
            # 3. 投票方式を選択
            self.select_method(formation)
                
            # BOXや流しの場合は、全馬番を選択してから一括セット
            
            # BOXや流しの場合は、全馬番を選択してから一括セット
            if formation in ['BOX', 'NAGASHI']:
                for umaban, _ in horse_amount_list:
                    logger.info(f"📋 馬番選択: {umaban}番")
                    horse_label = self.driver.find_element(By.CSS_SELECTOR, f"label[for^=no{umaban}]")
                    self.driver.execute_script("arguments[0].click();", horse_label)
                    time.sleep(0.5)
                
                # 金額入力（先頭の金額を使用）
                amount_yen = horse_amount_list[0][1]
                amount_units = amount_yen // 100
                amount_input = self.wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[ng-model^='vm.nUnit']")))
                amount_input.clear()
                amount_input.send_keys(str(amount_units))
                logger.info(f"💰 金額入力: {amount_units}単位")
                
                # セット
                set_button = self.driver.find_element(By.CSS_SELECTOR, "button[ng-click^='vm.onSet()']")
                self.driver.execute_script("arguments[0].click();", set_button)
                logger.info("📌 BOX/流しセット完了")
                time.sleep(2)
            else:
                # 従来通りの個別処理（SINGLE / 1頭ずつセット）
                for i, (umaban, amount_yen) in enumerate(horse_amount_list):
                    try:
                        logger.info(f"📋 {i+1}/{len(horse_amount_list)}: {umaban}番 {amount_yen:,}円")
                        
                        # 1. 馬番選択
                        try:
                            horse_label = self.driver.find_element(By.CSS_SELECTOR, f"label[for^=no{umaban}]")
                            self.driver.execute_script("arguments[0].click();", horse_label)
                        except Exception as e:
                            logger.warning(f"⚠️ {umaban}番の要素が見つかりません。ポップアップをチェックします: {e}")
                            if self.clear_popups():
                                time.sleep(1)
                                # 再試行
                                horse_label = self.driver.find_element(By.CSS_SELECTOR, f"label[for^=no{umaban}]")
                                self.driver.execute_script("arguments[0].click();", horse_label)
                            else:
                                raise
                        logger.info(f"✅ {umaban}番選択完了")
                        time.sleep(1.5)
                        
                        # 2. 金額入力（100円単位）
                        amount_units = amount_yen // 100
                        amount_input = self.wait.until(EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "input[ng-model^='vm.nUnit']")))
                        amount_input.clear()
                        time.sleep(0.5)
                        amount_input.send_keys(str(amount_units))
                        logger.info(f"💰 金額入力完了: {amount_units}単位")
                        time.sleep(1.5)
                        
                        # 3. セットボタンクリック
                        set_button = self.driver.find_element(By.CSS_SELECTOR, "button[ng-click^='vm.onSet()']")
                        self.driver.execute_script("arguments[0].click();", set_button)
                        logger.info(f"📌 セット完了: {umaban}番")
                        time.sleep(2)
                        
                        # 4. 馬番クリア (次の選択に備えて)
                        checkbox_element = self.driver.find_element(By.CSS_SELECTOR, f"input[id^=no{umaban}]")
                        if checkbox_element.is_selected():
                            self.driver.execute_script("arguments[0].click();", checkbox_element)
                            time.sleep(1)
                        
                    except Exception as e:
                        logger.error(f"❌ {umaban}番処理失敗: {e}")
                        continue
            
            if not finalize:
                logger.info("ℹ️ finalize=False のため、カートへの追加のみ行い終了します")
                return True

            # 投票リスト表示
            self.click_css_selector("button[ng-click^='vm.onShowBetList()']", 0)
            logger.info("📋 投票リスト表示完了")
            time.sleep(4) # カート集計の反映待ちを少し増やす
            
            # 総投票金額の自動取得を試みる (より確実な待機とセレクター)
            actual_total_yen = 0
            try:
                # 複数のセレクターで合計金額を探す
                total_selectors = [
                    "span[ng-bind*='vm.betListRecordInfo.sumAmount']",
                    "span[ng-bind*='sumAmount']",
                    "div.total_amount span.amount",
                    "//span[contains(@ng-bind, 'sumAmount')]",
                    "//div[contains(text(), '合計')]/following-sibling::div//span"
                ]
                
                total_el = None
                for ts in total_selectors:
                    try:
                        if ts.startswith("//"):
                            el = self.driver.find_element(By.XPATH, ts)
                        else:
                            el = self.driver.find_element(By.CSS_SELECTOR, ts)
                        if el.is_displayed() and el.text:
                            text = el.text.replace(",", "").replace("円", "").strip()
                            if text:
                                total_el = el
                                break
                    except: continue
                
                if total_el:
                    actual_total_str = total_el.text.replace(",", "").replace("円", "").strip()
                    if actual_total_str:
                        actual_total_yen = int(actual_total_str)
                        logger.info(f"💰 カート内の合計金額を取得成功: {actual_total_yen:,}円")
            except Exception as e:
                logger.warning(f"⚠️ 合計金額の取得に失敗しました: {e}")
            
            # 取得に失敗した場合の最終手段
            if actual_total_yen == 0:
                if calculated_total:
                    actual_total_yen = calculated_total
                    logger.info(f"⚠️ UIから取得失敗のため外部計算値を使用: {actual_total_yen:,}円")
                else:
                    actual_total_yen = sum(amount for _, amount in horse_amount_list)
                    logger.info(f"⚠️ UIから取得失敗のため暫定計算値を使用: {actual_total_yen:,}円")

            if actual_total_yen == 0:
                logger.error("❌ 合計金額が0円のため、投票を中止します")
                return False

            # 総額入力（カート内の実際の金額を使用）
            total_input = self.wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[ng-model^='vm.cAmountTotal']")))
            total_input.clear()
            time.sleep(1)
            total_input.send_keys(str(actual_total_yen))
            logger.info(f"💰 総額入力完了: {actual_total_yen}円")
            
            if not self.confirm_vote:
                success_final = self._finalize_purchase(actual_total_yen)

                logger.info(f"自動投票完了信号を送信 (success={success_final})")
                time.sleep(5) # 完了後の画面遷移待ちを十分に取る
                
                try:
                    ts = datetime.datetime.now().strftime('%H%M%S')
                    self.driver.save_screenshot(f"vote_result_{ts}.png")
                    logger.info(f"📸 投票完了後のスクリーンショットを保存しました: vote_result_{ts}.png")
                    # 失敗時はHTML構造も保存して次回診断に使う
                    if not success_final:
                        html_path = f"vote_fail_html_{ts}.html"
                        with open(html_path, 'w', encoding='utf-8') as hf:
                            hf.write(self.driver.page_source)
                        logger.info(f"🔎 投票失敗時のHTMLを保存しました: {html_path}")
                except:
                    pass
                return success_final
            else:
                logger.info(f"📋 [手動確認モード] 投票確認を実施 (総額: {actual_total_yen:,}円)")
                print(f"\n🎯 投票内容確認 (総額: {actual_total_yen:,}円)")
                response = input("\n投票を実行しますか？ (y/n): ")
                if response.lower() == 'y':
                    success_final = self._finalize_purchase(actual_total_yen)
                    if success_final:
                        logger.info("✅ 手動投票完了")
                    else:
                        return False
                else:
                    self.last_vote_cancelled = True
                    self.last_vote_status = "manual_cancelled"
                    self.last_vote_message = "manual confirmation cancelled"
                    logger.info("❌ 投票をキャンセルしました")
                    return False
            
            return True
                    
        except Exception as e:
            logger.error(f"❌ 投票(vote_horses)失敗: {e}")
            return False

    def close(self):
        """ブラウザを終了"""
        try:
            logger.info("🔚 ブラウザ終了中...")
            time.sleep(self.wait_sec)
            self.driver.close()
            self.driver.quit()
            logger.info("✅ ブラウザ終了完了")
        except Exception as e:
            logger.warning(f"⚠️ ブラウザ終了時にエラー: {e}")

if __name__ == "__main__":
    # テスト用コード
    load_dotenv()
    
    # テスト用のhorse_amount_list
    horse_amount_list = [(7, 10), (3, 15), (5, 8)]  # 7番1000円, 3番1500円, 5番800円
    
    driver = IPATVoteDriver()
    try:
        driver.start()
        driver.login()
        driver.select_normal_bet()
        # driver.select_course("東京")  # 実際の競馬場名に変更
        # driver.select_race(11)       # 実際のレース番号に変更
        # driver.vote_horses(horse_amount_list)
        print("✅ IPATテスト完了")
    except Exception as e:
        print(f"❌ IPATテスト失敗: {e}")
    finally:
        driver.close()