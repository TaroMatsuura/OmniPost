#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IPAT WIN5 Vote Driver (V1.0)
-----------------------------------------
1. WIN5専用の投票ページへのナビゲーション
2. 5レース分の馬番選択と合計金額のセット
3. 投票直前および投票完了後の自動スクリーンショット取得
4. 受付番号と合計金額のログ抽出
"""
from ipat_vote_driver import IPATVoteDriver, logger
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
import time
import os
import datetime

class IPATWin5VoteDriver(IPATVoteDriver):
    def __init__(self):
        super().__init__()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.evidence_dir = os.path.join(base_dir, "logs", "win5_evidence")
        os.makedirs(self.evidence_dir, exist_ok=True)

    def navigate_to_win5(self):
        """WIN5投票画面へ移動"""
        try:
            logger.info("🌐 WIN5メニューへの遷移を開始します...")
            # 既にWIN5画面にいるかチェック
            if "win5" in self.driver.current_url:
                logger.info("ℹ️ 既にWIN5セクションにいます")
                return True

            # 1. 属性ベースでボタンを探す (ng-clickが確実)
            win5_selectors = [
                "*[ng-click*='win5']",
                "a[href*='win5']",
                "button[href*='win5']"
            ]
            
            for selector in win5_selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed():
                        logger.info(f"🎯 WIN5メニューを発見しました (selector: {selector})")
                        self.driver.execute_script("arguments[0].click();", el)
                        time.sleep(3)
                        if "win5" in self.driver.current_url:
                            return True

            # 2. テキストベースで再試行
            elements = self.driver.find_elements(By.CSS_SELECTOR, "a, button, div")
            for el in elements:
                if el.is_displayed() and "WIN5" in el.text:
                    # テキストだけだと子要素の場合があるので、クリックしてみる
                    logger.info(f"🎯 テキスト 'WIN5' を発見しました ({el.tag_name})")
                    self.driver.execute_script("arguments[0].click();", el)
                    time.sleep(3)
                    if "win5" in self.driver.current_url:
                        return True
            
            # 発見できなかった場合のエラー処理
            logger.warning("❌ WIN5メニューが見つからないか、遷移に失敗しました。")
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            
            # 受付時間外などのキーワードがないか再度チェック
            maintenance_keywords = ["メンテナンス", "サービス時間外", "受付時間外", "運用時間外", "発売時間外"]
            for kw in maintenance_keywords:
                if kw in page_text:
                    logger.error(f"🚫 サービス提供時間外の可能性があります: {kw}")
                    break
            
            # スクリーンショットを証拠ディレクトリに保存
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            err_img = os.path.join(self.evidence_dir, f"win5_menu_not_found_{timestamp}.png")
            self.driver.save_screenshot(err_img)
            logger.info(f"📸 メニュー不在時のスクリーンショットを保存しました: {err_img}")
            
            return False
        except Exception as e:
            logger.error(f"❌ WIN5への遷移に失敗しました: {e}")
            return False

    def get_win5_deadline(self):
        """WIN5投票画面から締切時刻を取得する"""
        try:
            logger.info("⏱️ WIN5締切時刻を取得中...")
            # 締切時刻が表示されている要素を探す
            # 一般的に "14:45" などの形式
            selectors = [
                ".win5-limit-time", # 予想されるクラス名
                "//*[contains(text(), '締切時刻')]/following-sibling::*",
                "//*[contains(@class, 'limit')]//*[contains(text(), ':')]",
                "//div[contains(@class, 'win5')]//span[contains(text(), ':')]"
            ]
            
            for selector in selectors:
                try:
                    strategy = By.XPATH if selector.startswith("/") or selector.startswith("*") else By.CSS_SELECTOR
                    elements = self.driver.find_elements(strategy, selector)
                    for el in elements:
                        text = el.text.strip()
                        if ":" in text and len(text) <= 5: # "14:45" のような形式を期待
                            logger.info(f"✅ WIN5締切時刻を検出: {text}")
                            return text
                except:
                    continue
            
            # 見つからない場合はデフォルト値を返す（またはエラー）
            logger.warning("⚠️ WIN5締切時刻の自動取得に失敗しました。デフォルト 14:45 を使用します。")
            return "14:45"
        except Exception as e:
            logger.error(f"❌ 締切時刻取得中にエラー: {e}")
            return "14:45"

    def vote_win5(self, selections, total_amount):
        """WIN5の投票実行 (selections: list of lists)"""
        try:
            # 投票前にポップアップをクリアし、念のため全クリアを試みる
            self.clear_popups()
            try:
                # WIN5画面の「全取消」や「クリア」ボタンを探す
                clear_btn = self.driver.find_element(By.CSS_SELECTOR, "button[ng-click*='clear'], button[ng-click*='allClear']")
                if clear_btn.is_displayed():
                    self.driver.execute_script("arguments[0].click();", clear_btn)
                    logger.info("🧹 WIN5投票前の全クリア実行")
                    time.sleep(1)
            except:
                pass

            # 0. 投票方法の選択（もし「完全セレクト」が表示されていたらクリック）
            try:
                # 「完全セレクト」ボタンを探す (いろんなタグの可能性がある)
                logger.info("🔍 '完全セレクト' ボタンを探索中...")
                time.sleep(3)
                
                # ng-click を優先的に探す (vm.onSelectMode('all') など)
                fs_selectors = [
                    "*[ng-click*='all']",
                    "*[ng-click*='Mode']",
                    "button[contains(text(), '完全セレクト')]",
                    "div[contains(text(), '完全セレクト')]"
                ]
                
                found_fs = False
                for selector in fs_selectors:
                    elements = self.driver.find_elements(By.CSS_SELECTOR if not 'contains' in selector else By.XPATH, selector)
                    for el in elements:
                        if el.is_displayed() and ("完全セレクト" in el.text or "all" in (el.get_attribute("ng-click") or "")):
                            logger.info(f"🎯 '完全セレクト' を発見しました (tag: {el.tag_name})")
                            self.driver.execute_script("arguments[0].click();", el)
                            found_fs = True
                            time.sleep(3)
                            break
                    if found_fs: break
                
                if not found_fs:
                    # テキストベースで最終試行
                    candidates = self.driver.find_elements(By.XPATH, "//*[contains(text(), '完全セレクト')]")
                    for c in candidates:
                        if c.is_displayed():
                            logger.info(f"🎯 '完全セレクト' をテキストで発見 ({c.tag_name})")
                            self.driver.execute_script("arguments[0].click();", c)
                            found_fs = True
                            time.sleep(3)
                            break
                
                if not found_fs:
                    logger.warning("⚠️ '完全セレクト' ボタンが見つかりませんでした。")
                    if "win5/all" in self.driver.current_url:
                        logger.info("ℹ️ 既に完全セレクト画面にいるようです。")
                    else:
                        ts = datetime.datetime.now().strftime("%H%M%S")
                        tmp_img = os.path.join(self.evidence_dir, f"win5_debug_nav_{ts}.png")
                        self.driver.save_screenshot(tmp_img)
                        logger.info(f"📸 ナビゲーション中断時の画面を保存: {tmp_img}")
            except Exception as e:
                logger.warning(f"⚠️ '完全セレクト' 選択中にエラー（スキップします）: {e}")

            # 1. 各レースの馬番をクリック
            for i, horses in enumerate(selections):
                logger.info(f"📝 レース{i+1}: {horses} を選択中...")
                # そのレースのヘッダー（「nレース目」）をインデックスで特定する
                header_xpath = f"(//*[contains(text(), 'レース目')])[{i+1}]"
                
                # 馬番ボタンを探してクリック
                for horse in horses:
                    h_val = str(int(horse))
                    # そのレースのヘッダーより後に現れる最初の馬番ボタンを探す
                    btn_xpaths = [
                        f"({header_xpath}/following::button[text()='{h_val}'])[1]",
                        f"({header_xpath}/following::button[text()='0{h_val}'])[1]",
                        f"({header_xpath}/following::label[text()='{h_val}'])[1]",
                        f"({header_xpath}/following::label[text()='0{h_val}'])[1]",
                        f"({header_xpath}/following::label[contains(@for, 'no{h_val}')])[1]",
                        f"({header_xpath}/following::*[contains(@ng-click, 'selectHorse') and contains(text(), '{h_val}')])[1]"
                    ]
                    
                    clicked = False
                    for xpath in btn_xpaths:
                        try:
                            # 要素の存在を確認（waitは使わずリストでチェック）
                            btns = self.driver.find_elements(By.XPATH, xpath)
                            if btns and btns[0].is_displayed():
                                btn = btns[0]
                                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                                time.sleep(0.5)
                                self.driver.execute_script("arguments[0].click();", btn)
                                clicked = True
                                logger.info(f"✅ 馬番 {horse} を選択しました")
                                break
                        except:
                            continue
                    
                    if not clicked:
                        logger.warning(f"⚠️ 馬番 {horse} のボタンが見つかりません（レース {i+1}）")
                
                # 次のレースへ（もしあれば）
                if i < 4:
                    try:
                        logger.info(f"⏭️  次のレース（レース {i+2}）へ移動中...")
                        # 次のヘッダーが既に見えているかチェック
                        next_header_xpath = f"(//*[contains(text(), 'レース目')])[{i+2}]"
                        next_headers = self.driver.find_elements(By.XPATH, next_header_xpath)
                        
                        if next_headers and next_headers[0].is_displayed():
                            logger.info(f"ℹ️  次のレース（レース {i+2}）は既に表示されています")
                        else:
                            # 「次のレース」または「＞」ボタンなどを探す
                            next_btn_xpath = "//button[contains(text(), '次のレース')] | //a[contains(text(), '次のレース')] | //button[contains(@ng-click, 'nextRace')]"
                            next_btns = self.driver.find_elements(By.XPATH, next_btn_xpath)
                            if next_btns:
                                btn = next_btns[0]
                                if btn.is_displayed():
                                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                                    time.sleep(0.5)
                                    self.driver.execute_script("arguments[0].click();", btn)
                                    time.sleep(2)
                                else:
                                    logger.warning(f"⚠️ '次のレース' ボタンが非表示のためクリックをスキップします")
                            else:
                                logger.warning(f"⚠️ '次のレース' ボタンが見つかりませんが、スクロールを試みます")
                                self.driver.execute_script("window.scrollBy(0, 400);")
                                time.sleep(1)
                    except Exception as e:
                        logger.warning(f"⚠️ 次のレースへの遷移中にエラー (続行を試みます): {e}")

            # 2. 金額入力
            try:
                logger.info(f"💰 金額入力を開始します: {total_amount}円")
                
                # 待機を追加
                time.sleep(2)
                
                # XPathで「00円」というテキストの隣にあるinputを探す、またはng-modelで探す
                input_xpath = "//input[contains(@ng-model, 'Amount')] | //input[following-sibling::*[contains(text(), '00円')]] | //input[parent::*[contains(., '00円')]] | //input[@type='tel'] | //input[@type='number']"
                
                try:
                    # visibilityではなくpresenceで待つ（表示判定が不安定な場合があるため）
                    input_price = self.wait.until(EC.presence_of_element_located((By.XPATH, input_xpath)))
                    logger.info("🎯 金額入力フィールドを特定しました")
                except:
                    raise Exception("金額入力フィールドが見つかりません (XPATH検索タイムアウト)")

                # 強制的に表示状態にしてスクロール
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", input_price)
                time.sleep(1)
                
                # 値をセットしてイベントを発火 (JSで直接書き込む)
                # WIN5の場合、入力ボックスは「組ごとの金額（100円単位）」を期待している
                actual_points = 1
                for s in selections:
                    actual_points *= len(s)
                
                amount_val = str(total_amount // actual_points // 100)
                logger.info(f"🔢 1組あたりの金額 '{amount_val}' (100円単位) をセット中... (合計 {total_amount}円)")
                self.driver.execute_script(f"""
                    var el = arguments[0];
                    el.value = '{amount_val}';
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                """, input_price)
                time.sleep(1)
                
                # 3. セットボタン (ng-click="vm.onSet()")
                logger.info("🔘 'セット' ボタンをクリックします")
                set_selectors = [
                    "//button[contains(text(), 'セット')]",
                    "//button[contains(@ng-click, 'onSet')]",
                    "//*[contains(@class, 'ui-btn') and contains(., 'セット')]",
                    "//a[contains(., 'セット')]",
                    "//input[@value='セット']"
                ]
                set_btn = None
                for selector in set_selectors:
                    try:
                        strategy = By.XPATH if selector.startswith("/") else By.CSS_SELECTOR
                        els = self.driver.find_elements(strategy, selector)
                        for el in els:
                            if el.is_displayed():
                                set_btn = el
                                break
                        if set_btn: break
                    except: continue

                if not set_btn:
                    # 最後の手段：要素を探して待つ
                    set_btn = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'セット')] | //*[contains(@class, 'ui-btn') and contains(., 'セット')]")))

                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", set_btn)
                time.sleep(1)
                self.driver.execute_script("arguments[0].click();", set_btn)
                time.sleep(2)
                
                # 4. 入力終了ボタン
                logger.info("🔚 '入力終了' ボタンをクリックします")
                finish_selectors = [
                    "//button[contains(text(), '入力終了')]",
                    "//button[contains(@ng-click, 'onFinish')]",
                    "//*[contains(@class, 'ui-btn') and contains(., '入力終了')]",
                    "//a[contains(., '入力終了')]"
                ]
                finish_btn = None
                for selector in finish_selectors:
                    try:
                        strategy = By.XPATH if selector.startswith("/") else By.CSS_SELECTOR
                        els = self.driver.find_elements(strategy, selector)
                        for el in els:
                            if el.is_displayed():
                                finish_btn = el
                                break
                        if finish_btn: break
                    except: continue

                if finish_btn:
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", finish_btn)
                    time.sleep(0.5)
                    self.driver.execute_script("arguments[0].click();", finish_btn)
                    time.sleep(2)
            except Exception as e:
                logger.warning(f"⚠️ 金額セット中にエラー: {e}")
                # 念のためスクリーンショット
                self.driver.save_screenshot(os.path.join(self.evidence_dir, "win5_amount_error.png"))

            # 5. 証拠画像：確認画面
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            confirm_img = os.path.join(self.evidence_dir, f"win5_confirm_{timestamp}.png")
            self.driver.save_screenshot(confirm_img)
            logger.info(f"📸 投票確認画面を保存: {confirm_img}")

            # 6. 投票確定
            if os.getenv('CONFIRM_VOTE', 'True') == 'False':
                try:
                    # 確認用合計金額入力フィールドを探す
                    # (IPATのWIN5確認ダイアログでは合計金額の再入力が必要)
                    logger.info(f"✍️ 確認のため合計金額 '{total_amount}' を入力します")
                    
                    # より広範なセレクターを試す
                    sum_input = None
                    sum_selectors = [
                        "input[ng-model^='vm.cAmountTotal']",  # 通常投票ドライバーでのモデル名
                        "input[ng-model*='sumAmount']",
                        "input[ng-model*='SumAmount']",
                        "input[ng-model*='TotalAmount']",
                        "input[ng-model*='cAmount']",
                        "//input[contains(@ng-model, 'Amount')]",
                        "//input[contains(@id, 'SumAmount')]",
                        "//input[@type='tel' or @type='number' or @type='text'][ancestor::*[contains(., '合計金額')]]",
                        "//*[contains(text(), '合計金額入力')]/following::input[1]"
                    ]
                    
                    # モーダルが表示されるのを少し待つ
                    time.sleep(2)
                    
                    for sel in sum_selectors:
                        try:
                            strategy = By.XPATH if sel.startswith("/") or sel.startswith("*") else By.CSS_SELECTOR
                            els = self.driver.find_elements(strategy, sel)
                            for el in els:
                                if el.is_displayed():
                                    sum_input = el
                                    break
                            if sum_input: 
                                logger.info(f"🎯 合計金額入力フィールドを特定しました (selector: {sel})")
                                break
                        except: continue

                    if sum_input:
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", sum_input)
                        time.sleep(0.5)
                        
                        # JSで金額を入力し、確実にイベントを発火させる
                        self.driver.execute_script(f"""
                            var el = arguments[0];
                            el.value = '{total_amount}';
                            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                        """, sum_input)
                        time.sleep(1)
                    else:
                        logger.warning("⚠️ 合計金額入力フィールドが見つかりませんでした。")
                        # デバッグ用に全input属性をログ出力（隠し要素も含む可能性を考慮）
                        try:
                            inputs = self.driver.find_elements(By.TAG_NAME, "input")
                            logger.debug(f"🔍 発見したinput数: {len(inputs)}")
                            for i, inp in enumerate(inputs):
                                if inp.is_displayed():
                                    logger.debug(f"  [{i}] ID: {inp.get_attribute('id')}, Model: {inp.get_attribute('ng-model')}, Type: {inp.get_attribute('type')}")
                        except: pass

                    commit_btn_xpath = "//button[contains(text(), '購入する')] | //button[contains(text(), '投票する')] | //button[contains(@ng-click, 'onVote')]"
                    commit_btn = self.wait.until(EC.element_to_be_clickable((By.XPATH, commit_btn_xpath)))
                    self.driver.execute_script("arguments[0].click();", commit_btn)
                    logger.info("🔘 '購入する' ボタンをクリックしました")
                    
                    # 最終確認モーダル (OK/キャンセル) が出ることがあるのでハンドリング
                    try:
                        time.sleep(1.5)
                        ok_selectors = [
                            "//button[text()='OK']",
                            "//button[contains(text(), 'OK')]",
                            "//div[contains(@class, 'modal')]//button[contains(text(), 'OK')]",
                            "button[ng-click*='dismiss']",
                            "button[ng-click*='close']"
                        ]
                        
                        ok_btn = None
                        for sel in ok_selectors:
                            try:
                                els = self.driver.find_elements(By.XPATH if "/" in sel else By.CSS_SELECTOR, sel)
                                for el in els:
                                    if el.is_displayed():
                                        ok_btn = el
                                        break
                                if ok_btn: break
                            except: continue
                        
                        if ok_btn:
                            logger.info("🎯 最終確認ダイアログの 'OK' をクリックします")
                            self.driver.execute_script("arguments[0].click();", ok_btn)
                            time.sleep(2)
                        else:
                            logger.info("ℹ️ 最終確認ダイアログは表示されませんでした（または特定できませんでした）")
                    except Exception as e:
                        logger.warning(f"⚠️ 最終確認ダイアログの処理中にエラー（無視して続行）: {e}")
                        
                    logger.info("🚀 投票確定プロセスが完了しました")
                except Exception as e:
                    logger.error(f"❌ 投票確定プロセスでエラー: {e}")
            else:
                logger.info("🚀 [DRY-RUN] 投票確定ボタンのクリックをスキップしました (CONFIRM_VOTE=True)")

            # 7. 証拠画像：完了画面
            time.sleep(3)
            complete_img = os.path.join(self.evidence_dir, f"win5_complete_{timestamp}.png")
            self.driver.save_screenshot(complete_img)
            logger.info(f"📸 投票完了画面を保存: {complete_img}")
            
            return True
        except Exception as e:
            logger.error(f"❌ WIN5投票プロセスでエラー: {e}")
            # エラー時のスクリーンショット
            try:
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                err_file = os.path.join(self.evidence_dir, f"win5_error_{ts}.png")
                self.driver.save_screenshot(err_file)
                logger.info(f"📸 エラー画面を保存: {err_file}")
            except: pass
            return False

if __name__ == "__main__":
    # Test execution
    driver = IPATWin5VoteDriver()
    if driver.ensure_logged_in():
        driver.navigate_to_win5()
        # Test selection: 1-1-1-1-1
        driver.vote_win5([['1'], ['1'], ['1'], ['1'], ['1']], 100)
    driver.close()
