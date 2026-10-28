# 開発と検証

WindowsとPython 3.12.15以降の3.12系を使います。暗号化とDPAPIは実際のWindows APIで検査し、依存は`requirements.lock.txt`で固定します。通常の帳簿とは別に、検査ごとの一時フォルダーを使ってください。

## モデルを使わない検査

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/check_publication.py
```

モデルのダウンロードと外部推論は行いません。OCR根拠の照合・科目条件・SQLite移行・外部キー・仕訳・確認の前提・CSVの列と文字コード・借貸・拒否条件・HTTP保護・サンプル配布を検査します。実OCRとAIの精度は別の検証です。

公開結果のHTML表示データは`python scripts/build_evidence.py`で生成します。期待値・観測値・採点値を表示上の都合で書き換えないでください。

## ブラウザー操作の検査

追加の開発検査にはNode.js 20以降を使います。アプリの通常利用には不要です。

```powershell
npm ci --ignore-scripts --no-audit --no-fund
npx playwright install chromium
$env:RYOSHU_QA_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm run test:browser
```

検査用DBとループバック専用サーバーを毎回作成し、終了時にはそのサーバーだけを閉じます。100ファイルの選択・100ページPDFの分割と取り込み方のやり直し・フォルダー・停止と再開・検索・日本語入力の変換イベント・一括確認・対象外と復帰・CSV・375px幅を操作します。2000ファイルの結果表示は100件ずつの描画を確認します。

OCRとAIの結果は保存済みの架空データを再現したものです。原本保存・HTTP・SQLite・CSVは実処理ですが、新規の精度や2000件の実領収書の性能は測りません。変換イベントの検査はOSの日本語IMEそのものではなく、画面には検証用と表示します。記録と画像は`.local/browser-qa`へ保存します。

## CSV統合検査

```powershell
.\.venv\Scripts\python.exe scripts/verify_csv_workflow.py --out .local/csv-proof
```

新しい保存先を指定し、HTTPの取込・手動確認・プレビュー・出力を通して6件・7仕訳行を4形式で生成・再読込します。OCRとAIは呼ばず、期待値を入力した架空の確認シナリオを使います。

## 実OCR・モデルを含む100件

モデルの利用条件を確認して`setup.ps1`で環境を用意します。通常の帳簿とは別のデータと未使用ポートを使ってください。

```powershell
& "$env:USERPROFILE\StudioYebisu\ReceiptLedger\venv\Scripts\python.exe" scripts/audit_receipts_100.py --fixtures samples/receipts-100-v2 --output .local/audit-001 --port 8771
```

実行元を凍結コピーし、100件をHTTP取込から処理します。期待値・ケースID・業務背景はモデルへ渡しません。既存の証拠を上書きしない新しい出力先を指定し、停止判定と未確定も含めて採点します。結果の良いケースだけを選ばないでください。`--cases V001,V002`は影響を調べる部分検査です。

固定したllama.cpp実行環境またはQwenモデルがなければ取り込み前に終了します。AI停止時の診断だけを目的とする場合に`--allow-model-offline`を指定し、その実行を通常のAI検証の成功として扱わないでください。

CPUで多数の推論をするため時間がかかります。出力にはOCR本文・候補・帳簿DB・ローカルパスが含まれます。`.local`はGit対象外で、そのまま公開しないでください。モデルや実行環境の更新と非決定性により、再実行が同じ結果になるとは限りません。結果の主張には、その結果に記録された測定時の実行環境・モデル・対象ソースを対応付け、旧Ollama時代の結果をllama.cppの結果として示さないでください。

## 変更と報告

科目初期値を変更しても、利用者が編集した科目と確認済み仕訳を保持します。私用分・返金・外貨・用途不明の飲食・資産・混在税率を通常の経費へ黙って丸めないでください。CSVの対応先や税区分を変える場合は、各社の一次資料と拒否条件も更新します。

不具合報告には再現手順・OS・Python・アプリ版・架空の最小サンプルを使います。実際の領収書・DB・バックアップ・秘密情報は添付しないでください。公開前のローカル検査とGitHub Actionsの結果は区別して報告します。

## 暗号化・認証の変更

[仕様](docs/SECURITY-DESIGN.md)と[点検結果](docs/testing/security/README.md)を確認してください。鍵なしの平文保存や、テスト用の認証無効フラグを本番コードへ追加しないでください。テストは明示的に保護した架空データを使います。旧版の固定SQLと公開PDFで、全行・仕訳・原本・異常終了・負例を照合します。実際の利用者の帳簿や認証ファイルをレビュー用モデルへ渡してはいけません。
