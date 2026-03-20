# OmniPost Inbox

OmniPost の inbox 監視と自動投票は、Standard Order Schema v1.3 に対応している。

## 起動

1 回だけスキャンして、検証と投票を行う:

```bash
/usr/bin/python3 run_omnipost_inbox.py --once
```

常時監視して、受信ごとに検証と投票を行う:

```bash
/usr/bin/python3 run_omnipost_inbox.py
```

## 自動投票の運用手順

### 1. 起動前の準備

1. .env に IPAT ログイン情報と運用フラグを設定する。
2. 予想 JSON を inbox に保存する。
3. 実運用では OMNIPOST_EXECUTE_VOTES=True になっていることを確認する。
4. 購入限度額が目標額以上なら自動投票を止めたい場合は OMNIPOST_STOP_TARGET_BALANCE_AMOUNT を設定する。
5. 再配分せず JSON 金額どおり投票したい場合は OMNIPOST_FORCE_JSON_AMOUNTS=True を設定する。
6. 手動確認を入れたい場合は CONFIRM_VOTE=True、自動で購入まで進める場合は CONFIRM_VOTE=False を設定する。

### 2. 起動方法

1. 1 回だけ処理する場合は run_omnipost_inbox.py --once を実行する。
2. 常時監視する場合は run_omnipost_inbox.py を実行する。
3. 発走 5 分前まで待ちたくないテスト時だけ OMNIPOST_SKIP_PREPARATION_WAIT=True を付けて起動する。

### 3. 通常時の流れ

1. OmniPost が inbox 内の JSON を検証する。
2. 検証を通過した JSON は archive/accepted へ移動し、失敗した JSON は archive/rejected へ移動する。
3. 実行対象の注文ごとに、JSON の orders[*].post_time を発走時刻として扱う。
4. 実行対象の注文ごとに IPAT へログインし、購入限度額を確認する。
5. JSON の post_time を基準に、発走 5 分前から投票処理を開始する。
6. 発走時刻と締切条件を満たしていれば、注文内容を IPAT 画面へ入力する。
7. CONFIRM_VOTE=True の場合は、最終購入前にターミナルで y / n を入力する。
8. 処理結果は logs/omnipost_inbox_report.csv、logs/omnipost_inbox_report.jsonl、および archive 側 JSON の omnipost_result に記録される。

### 4. 残高不足時の流れ

1. OmniPost が残高不足を検知すると、ターミナルに対象レースと必要額を表示して停止する。
2. ユーザーは IPAT 側で手動入金する。
3. 入金後にターミナルで ok を入力すると、OmniPost は購入限度額確認から再開する。
4. 今回の自動投票を終了する場合は end を入力する。
5. 購入ボタン押下後に IPAT 側で残高不足ダイアログが出た場合も、同じく ok / end で対応する。

### 5. 締切時の流れ

1. 締切 5 分前を過ぎた注文は投票せず skipped として記録する。
2. 購入操作中に IPAT 側で締切ダイアログが出た場合は、OmniPost が OK を押して処理を継続する。

### 6. 運用後の確認

1. logs/omnipost_inbox.log で実行ログを確認する。
2. logs/omnipost_inbox_report.csv または logs/omnipost_inbox_report.jsonl で executed / skipped / failed を確認する。
3. archive/accepted 内の JSON に追記された omnipost_result.execution を確認すると、注文単位の結果を追跡できる。
4. 目標残高到達で停止した場合は、ログに target balance reached が出力される。

## ディレクトリ

- inbox: 新しい注文 JSON の投入先
- archive/accepted: 検証通過後に退避された JSON
- archive/rejected: 検証失敗後に退避された JSON
- logs/omnipost_inbox.log: 実行ログ
- logs/omnipost_inbox_report.csv: 処理結果レポート
- logs/omnipost_inbox_report.jsonl: 処理結果レポート

## .env

自動投票に必要な最小構成は以下。

```dotenv
TARGET_BALANCE_AMOUNT=52500
OMNIPOST_STOP_TARGET_BALANCE_AMOUNT=0
USER_ID=...
JRA_ID=...
PASSWORD=...
BIRTH_CODE=...
CONFIRM_VOTE=False
```

- CONFIRM_VOTE=True の場合は最終購入クリックを止める
  - y を入力すると購入処理へ進む
  - n を入力すると今回の自動投票 run を終了する
- OMNIPOST_EXECUTE_VOTES=False の場合は検証とアーカイブのみ行う
- OMNIPOST_SIMULATE_IPAT=True の場合は IPAT 接続を行わず、投票成功相当の execution 記録だけを残す
- TARGET_BALANCE_AMOUNT は、ころがし運用と定額運用の切り替えに使う基準残高
- OMNIPOST_STOP_TARGET_BALANCE_AMOUNT は、購入限度額がこの金額以上になったら自動投票を終了する。0 の場合は無効
- OMNIPOST_FORCE_JSON_AMOUNTS=True の場合は再配分を行わず、全 cohort で JSON の amount をそのまま使う
- OMNIPOST_SKIP_PREPARATION_WAIT=True の場合は発走 5 分前までの待機をスキップする
- OMNIPOST_SIMULATED_PURCHASE_LIMIT でシミュレーション時の購入限度額を指定できる
- OMNIPOST_SIMULATION_IGNORE_CUTOFF=True の場合はシミュレーションで締切判定を無視する
- OMNIPOST_CUTOFF_MINUTES は既定で 5

ディレクトリ設定は未指定なら既定値を使う。

## ファイル名推奨

- [送信元]_[日時]_[固有ID].json
- 例: T58BoxV20_20260321_153000_001.json

## 標準 JSON

複数注文を含む形式:

```json
{
  "version": "1.3",
  "sender": "T58_Box_v20",
  "request_id": "REQ-20260322-COMBINED-001",
  "timestamp": "2026-03-22T10:00:00+09:00",
  "orders": [
    {
      "order_id": "ORD-001",
      "race_id": "20260322060211",
      "post_time": "15:40",
      "ticket_type": "win5",
      "win5_details": {
        "select_n1": [3, 5],
        "select_n2": [1, 8, 10],
        "select_n3": [2],
        "select_n4": [7, 12],
        "select_n5": [1, 15]
      },
      "unit_amount": 100,
      "total_combinations": 24,
      "amount": 2400,
      "expected_ev": 1.8
    },
    {
      "order_id": "ORD-002",
      "race_id": "20260322090212",
      "post_time": "16:25",
      "horse_no": 5,
      "ticket_type": "tan",
      "amount": 1000,
      "expected_ev": 1.4,
      "min_odds": 4.5
    },
    {
      "order_id": "ORD-003",
      "race_id": "20260322090212",
      "post_time": "16:25",
      "ticket_type": "umaren_box",
      "horses": [3, 7, 11],
      "unit_amount": 1000,
      "total_combinations": 3,
      "amount": 3000,
      "expected_ev": 1.6
    }
  ]
}
```

## 現在の検証内容

- version, sender, request_id, timestamp は必須
- orders[*].order_id, race_id, post_time, ticket_type, amount は必須
- orders[*].post_time は HH:MM
- 通常馬券では orders[*].horse_no は 1 から 18 の整数
- 馬連BOXでは orders[*].horses に 2 頭以上の重複しない馬番配列が必要
- ticket_type は tan, fuku, ren, umaren_box, wide, waku, umatan, sanpuku, santan, win5 を受付
- amount と unit_amount は 100 円単位
- win5 では win5_details, unit_amount, total_combinations が必須
- umaren_box では unit_amount, total_combinations が必須
- win5 の amount は unit_amount × total_combinations と一致必須
- umaren_box の amount は unit_amount × total_combinations と一致必須
- min_odds, expected_ev, memo は任意
- request_id は過去ログに存在する場合 duplicate としてスキップ
- パース可能な JSON は archive 側へ移動する際に omnipost_result を追記

## 執行ポリシー

- 受信後すぐに投票はせず、JSON の orders[*].post_time を発走時刻として、その 5 分前を準備時刻として処理する
- 最初の execution cohort は JSON 指定金額のまま投票する
- 2 件目以降の cohort は投票直前の購入限度額を確認し、TARGET_BALANCE_AMOUNT 未達ならころがしとして残高を使う
- 購入限度額が OMNIPOST_STOP_TARGET_BALANCE_AMOUNT 以上の場合は、その時点で自動投票 run を終了する
- OMNIPOST_FORCE_JSON_AMOUNTS=True の場合は上記の再配分を行わず、JSON の amount をそのまま使う
- 同じレース番号の cohort が複数場にまたがる場合は、その cohort 内の JSON 金額比率で残高を配分する
- 受信後に IPAT へログインし、購入限度額を確認してから投票する
- 非開催日や受付時間外のテストでは OMNIPOST_SIMULATE_IPAT=True を使う
- 残高確認時に購入限度額が不足している場合は、ターミナルに不足メッセージを表示して ok / end の入力待ちで停止する
- 残高不足で ok を入力すると、購入限度額の確認からやり直して処理を継続する
- 残高不足で end を入力すると、その時点で自動投票 run 全体を終了する
- 購入ボタン押下後に IPAT 側で購入限度額超過ダイアログが出た場合も、同じく ok / end で待機する
- 購入ボタン押下後に IPAT 側で締切ダイアログが出た場合は、自動で OK を押してその注文を skipped として次の処理へ進む
- 締切 5 分前を切った注文は skip として記録する
- 通常馬券の発走時刻は IPAT 画面からは自動取得せず、JSON の post_time をそのまま使う
- ticket_type が win5 の場合は専用投票ルーチンを使う
- 通常馬券で現在のドライバーが対応しているのは tan, fuku, ren, umatan, sanpuku, santan
- wide と waku は現行 IPAT ドライバー未対応のため skip になる
- archive JSON の omnipost_result.execution に order ごとの executed/skipped/failed が残る

## 残高不足時の運用

1. OmniPost が購入限度額不足を検知すると、ターミナルに対象レースと必要額を表示して停止する。
2. ユーザーが IPAT へ手動で入金する。
3. 入金後にターミナルで ok を入力すると、OmniPost は購入限度額確認に戻って処理を再開する。
4. 今回の自動投票をやめる場合は end を入力する。この場合、その run は終了する。
5. 入金待ち中に締切になった場合は、IPAT 側の締切ダイアログを OmniPost が OK で閉じて後続処理を継続する。