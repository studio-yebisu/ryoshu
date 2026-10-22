# 開発と検証

Windows・Python 3.12.15以降の3.12系を使います。暗号化・DPAPIは実際のWindows APIで検査します。依存の固定版は`requirements.lock.txt`です。アプリの通常データを使わず、検査ごとに独立した一時フォルダーを指定してください。

## モデル不要の検査

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/check_publication.py
```

この検査はモデルのダウンロード・外部推論をしません。OCR根拠の照合、科目条件、SQLite移行・外部キー・仕訳、確定の前提、CSVの列・文字コード・借貸・拒否条件、HTTPの保護、サンプル配布を検査します。実OCR・AI精度は別です。

公開結果のHTML表示データは`python scripts/build_evidence.py`で生成します。期待値・観測値・採点値を表示の都合で変更しないでください。

## ブラウザーでの操作検査

開発用の追加検査にはNode.js 20以降を使います。アプリの通常利用にNode.jsは必要ありません。

```powershell
npm ci --ignore-scripts --no-audit --no-fund
npx playwright install chromium
$env:RYOSHU_QA_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm run test:browser
```

毎回新しい検証用DBとループバック専用サーバーを作り、終了時にそのサーバーだけを閉じます。100ファイルの選択、100ページPDFの分割・取り込み方のやり直し、フォルダー、停止・再開、検索、日本語入力の変換イベント、一括確認・対象外・復帰、CSV、375px幅の画面を操作します。2000ファイルの結果表示も、100件ずつの描画を確認します。

この検査のOCR・AI結果は**保存済みの架空データを再現したもの**です。原本保存、HTTP、SQLite、CSVは実処理ですが、新しいOCR・AI精度や、2000件の実領収書処理性能の検査ではありません。変換イベントの検査はOSの日本語IMEそのものを操作するものではありません。画面にも検証用と表示します。記録と画面画像は`.local/browser-qa`に保存します。

## CSVの統合検査

```powershell
.\.venv\Scripts\python.exe scripts/verify_csv_workflow.py --out .local/csv-proof
```

新しい保存先を指定します。HTTPの取込・手動確認・プレビュー・出力を通し、6件・7仕訳行を4形式で生成して再読込します。OCR・AIは呼ばず、期待値で入力した架空の確認シナリオを使います。AIの点数とは別の検査です。

## 実OCR・モデルを含む100件

モデル利用条件を確認し、先に`setup.ps1`で環境を準備します。通常の帳簿とは別のデータと未使用ポートを使います。

```powershell
& "$env:USERPROFILE\StudioYebisu\ReceiptLedger\venv\Scripts\python.exe" scripts/audit_receipts_100.py --fixtures samples/receipts-100-v2 --output .local/audit-001 --port 8771
```

実行元を凍結コピーし、100件をHTTP取込から処理します。期待値・ケースID・各件の業務背景はモデルへ渡しません。既存の証拠を上書きしない新しい出力先を指定します。停止判定や未確定も含めて採点し、結果の良いケースだけを選ばないでください。`--cases V001,V002`は変更の影響を確認する部分検査です。

Ollamaと指定モデルの準備ができていなければ、取り込み前に終了します。AI停止時の診断を目的にする場合だけ`--allow-model-offline`を指定し、その実行を通常のAI検証の成功として扱わないでください。

CPUで多数の推論をするため、時間がかかります。出力にはOCR本文、候補、帳簿DB、ローカルパス等が入ります。`.local`はGit対象外です。そのまま公開しないでください。再実行時のモデル更新・非決定性によって、同じ結果になる保証はありません。

## 変更と報告

科目初期値の変更では利用者が編集した科目・確定済み仕訳を保持します。私用分、返金、外貨、用途不明の飲食、資産、混在税率を通常の経費へ黙って丸めないでください。CSVの対応先や税区分を変更する場合は各社の一次資料と拒否条件を更新します。

不具合報告には再現手順、OS・Python・アプリ版、架空の最小サンプルを使います。実際の領収書やDB、バックアップ、秘密情報は添付しないでください。公開直前のローカル検査と、GitHub Actionsで実行された検査は区別して報告します。

## 暗号化・認証を変更する場合

[仕様](docs/SECURITY-DESIGN.md)と[最新の点検](docs/testing/security/README.md)を確認してください。鍵なしの平文保存や、テスト用の認証無効フラグを本番コードへ追加しないでください。テストは明示的に保護設定した架空データを使います。旧版の固定SQLと公開PDFを使い、全行・仕訳・原本・異常終了・負例を照合します。実際の利用者の帳簿や認証ファイルをレビュー用モデルへ渡してはいけません。
