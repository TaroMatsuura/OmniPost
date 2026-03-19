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
USER_ID=...
JRA_ID=...
PASSWORD=...
BIRTH_CODE=...
CONFIRM_VOTE=False
```

- CONFIRM_VOTE=True の場合は最終購入クリックを止める
- OMNIPOST_EXECUTE_VOTES=False の場合は検証とアーカイブのみ行う
- OMNIPOST_SIMULATE_IPAT=True の場合は IPAT 接続を行わず、投票成功相当の execution 記録だけを残す
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
    }
  ]
}
```

## 現在の検証内容

- version, sender, request_id, timestamp は必須
- orders[*].order_id, race_id, post_time, ticket_type, amount は必須
- orders[*].post_time は HH:MM
- 通常馬券では orders[*].horse_no は 1 から 18 の整数
- ticket_type は tan, fuku, ren, wide, waku, umatan, sanpuku, santan, win5 を受付
- amount と unit_amount は 100 円単位
- win5 では win5_details, unit_amount, total_combinations が必須
- win5 の amount は unit_amount × total_combinations と一致必須
- min_odds, expected_ev, memo は任意
- request_id は過去ログに存在する場合 duplicate としてスキップ
- パース可能な JSON は archive 側へ移動する際に omnipost_result を追記

## 執行ポリシー

- 受信後すぐに投票はせず、各レースの発走 5 分前を準備時刻として処理する
- 最初の execution cohort は JSON 指定金額のまま投票する
- 2 件目以降の cohort は投票直前の購入限度額を確認し、TARGET_BALANCE_AMOUNT 未達ならころがしとして残高を使う
- 同じレース番号の cohort が複数場にまたがる場合は、その cohort 内の JSON 金額比率で残高を配分する
- 受信後に IPAT へログインし、購入限度額を確認してから投票する
- 非開催日や受付時間外のテストでは OMNIPOST_SIMULATE_IPAT=True を使う
- 購入限度額が不足している注文は skip として archive/report に記録する
- 締切 5 分前を切った注文は skip として記録する
- ticket_type が win5 の場合は専用投票ルーチンを使う
- 通常馬券で現在のドライバーが対応しているのは tan, fuku, ren, umatan, sanpuku, santan
- wide と waku は現行 IPAT ドライバー未対応のため skip になる
- archive JSON の omnipost_result.execution に order ごとの executed/skipped/failed が残る