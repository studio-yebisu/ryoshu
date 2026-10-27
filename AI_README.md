# Ryoshu — コーディングエージェント向けガイド

この文書は、Ryoshu のソースコードを扱う AI コーディングエージェントと人間の開発者向けです。
アプリの利用方法は [README.md](README.md)、開発手順の詳細は [CONTRIBUTING.md](CONTRIBUTING.md) を参照してください。
本書とソースコードやリンク先文書に相違がある場合は、ソースコードとリンク先文書を正とします。

## 1. 製品の目的と境界

Ryoshu は Windows 向けの領収書整理アプリです。
PDF、JPG、PNG、WebP の領収書を取り込み、Indra OCR でローカルPC上の CPU を使って文字を読み取ります。
別プロセスとして起動する llama.cpp の `llama-server` と、固定された Qwen GGUF モデルが勘定科目などの候補を作ります。
生成物はあくまで下書きであり、利用者が内容を確認・修正して確定します。出力によって自動的に確認済みになることはありません。
汎用CSV、freee振替伝票、MFクラウド仕訳帳、弥生デスクトップ25項目形式の CSV を出力できます。
汎用CSVでは未確認を含む OCR 候補一覧も保存できますが、会計ソフト向けの仕訳CSVには確認済みの明細だけを含めます。

アプリはローカル動作を前提に設計されています。
HTTP サーバーはループバックの `HOST = "127.0.0.1"` で待ち受け、llama 子プロセスもループバックと起動ごとの認証キーを使います。
外部 AI や API キーを利用する構成ではありません。
`receipt_ledger/privacy.py` は Python の監査フックを設定し、ループバック以外への Python ソケット接続と名前解決を拒否します。
ただし、ネイティブ拡張や llama 子プロセスまでは対象外であり、OS やファイアウォールによる隔離でもありません。
初期セットアップと更新では、Python、依存パッケージ、OCR モデル、llama.cpp ランタイム、VC++ ファイル、Qwen モデルをダウンロードします。
通信と保護の正確な境界は [docs/PRIVACY.md](docs/PRIVACY.md) と [docs/SECURITY-DESIGN.md](docs/SECURITY-DESIGN.md) を参照してください。
リモート推論、クラウドへのアップロード、テレメトリー、サインイン機能を追加してはいけません。

売上・口座照合、減価償却、決算・申告、返金・外貨の仕訳は対象外です。
電子帳簿保存法の認証製品ではありません。
Node.js 20 以降は開発・検証専用であり、アプリの利用には不要です。
「確認済み」は利用者が確認した状態を表し、税理士などによる承認を意味しません。詳しくは [docs/REVIEW-POLICY.md](docs/REVIEW-POLICY.md) を参照してください。

## 2. リポジトリの地図

- `receipt_ledger/__main__.py`: 起動入口。Python 3.12.15 以降の 3.12 系を確認し、ネイティブモジュールの読み込み前に `crt_bootstrap.activate` を実行し、`privacy.py` のフックを設定する。
- `receipt_ledger/config.py`: パス、環境変数、制限値、`PAYMENTS`、`TAX_LABELS` を定義する。
- `receipt_ledger/crt_bootstrap.py`: 検証済みの Microsoft C++ ランタイムを読み込む。
- `receipt_ledger/runtime_pin.json` と `receipt_ledger/runtime_pin.py`: llama.cpp ランタイムとモデルの固定情報を一箇所で管理する。
- `receipt_ledger/runtime.py`: アプリが管理する `llama-server` 子プロセスを扱う。
- `receipt_ledger/pipeline.py`: 領収書ごとの OCR から候補生成までを処理する。
- `receipt_ledger/semantic.py`: モデルに購入内容の意味を判断させ、各項目は文書の記載に基づかせる。
- `receipt_ledger/importing.py`: PDF をページ単位の領収書として取り込み、原本を変更せず出所を記録する。
- `receipt_ledger/store.py`: 帳簿データベースを扱う。
- `receipt_ledger/protection.py`: Windows が管理する鍵と認証付き暗号を使ってデータを保護する。
- `receipt_ledger/privacy.py`: Python ソケットをループバックに限定する監査フックを提供する。
- `receipt_ledger/workflow.py`: 帳簿の作成と後からの確認作業を扱う。
- `receipt_ledger/accounting.py`: 仕訳のロジックを扱う。
- `receipt_ledger/catalog.py`: バージョン付きの初期勘定科目を扱う。実際に使われる科目は利用者のデータベース内にある。
- `receipt_ledger/schemas.py`: データのスキーマを定義する。
- `receipt_ledger/exports.py`: 確認済みの税込データから各種 CSV を出力し、汎用CSVでは未確認を含む OCR 候補一覧も出力する。
- `receipt_ledger/server.py`: HTTP アプリケーションを提供する。
- `data/account-catalog.json` と `data/catalog-history/*.json`: 勘定科目の初期データと履歴。
- `web/`: ブラウザー UI。主なファイルは `app.js`、`processing.js`、`import.js`、`security.js`。
- `scripts/check_publication.py`: 公開前の文書・リポジトリ検査。
- `scripts/verify_csv_workflow.py`: 架空データによる CSV ワークフロー検証。
- `scripts/serve_browser_qa.py`: 保存済みの架空データを使うブラウザー検証サーバー。
- `scripts/audit_receipts_100.py`: 実 OCR・実モデルを使う検証。
- `scripts/prepare_ocr.py`、`scripts/prepare_llama.py`: OCR とモデルの準備。
- `scripts/build_windows_package.py`、`scripts/make_source_zip.py`: Windows パッケージとソース ZIP の作成。
- `setup.ps1`、`start.ps1`、`stop.ps1`: セットアップと起動・停止のスクリプト。
- `packaging/windows/`: Windows インストーラーのビルド関連ファイル。
- `tests/`: pytest と Node の単体テスト。
- `samples/receipts-100-v2/`: 架空の検証用領収書。
- `docs/testing/`: 公開済みの検証記録。
- `.github/workflows/ci.yml`: CI で実行する検査の正本。

## 3. 正とするファイル

- ランタイムとモデルの固定情報: [receipt_ledger/runtime_pin.json](receipt_ledger/runtime_pin.json)。過去の Ollama 構成とは異なり、Ollama を再導入しない。
- パス・環境変数・制限値: [receipt_ledger/config.py](receipt_ledger/config.py)。
- Python 依存関係: [requirements.lock.txt](requirements.lock.txt) がハッシュ固定の正本、[requirements.txt](requirements.txt) は人間向けの一覧。
- 開発専用の Playwright 依存関係: [package.json](package.json) と [package-lock.json](package-lock.json)。
- 勘定科目の初期値: [data/account-catalog.json](data/account-catalog.json)。
- 設計と境界: [DESIGN.md](DESIGN.md)、[docs/SECURITY-DESIGN.md](docs/SECURITY-DESIGN.md)、[docs/PRIVACY.md](docs/PRIVACY.md)。
- 出力・科目・確認の規則: [docs/CSV-EXPORT.md](docs/CSV-EXPORT.md)、[docs/ACCOUNTS.md](docs/ACCOUNTS.md)、[docs/REVIEW-POLICY.md](docs/REVIEW-POLICY.md)。
- 第三者コンポーネント: [THIRD_PARTY.md](THIRD_PARTY.md)。
- 開発手順と CI: [CONTRIBUTING.md](CONTRIBUTING.md)、[.github/workflows/ci.yml](.github/workflows/ci.yml)。

固定情報とロックファイルは、それぞれのツールと供給元に従って更新します。
検査を通すためにハッシュ値を手で書き換えてはいけません。

## 4. モデルを使わない安全な検査

Windows PowerShell で、次を実行します。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/check_publication.py
.\.venv\Scripts\python.exe scripts/verify_csv_workflow.py --out .local/csv-proof
```

Python は 3.12.15 以降の 3.12 系を使い、Windows 上で実際の DPAPI を対象に検査します。
CI の手順は [.github/workflows/ci.yml](.github/workflows/ci.yml) を正本とします。
`.local/csv-proof` が既にある場合は、`.local/` の下にある別の新しいパスを自分で選びます。
既存の出力を削除したり上書きしたりしてはいけません。
この CSV 検証は架空のシナリオを使い、OCR や AI を実行しません。

Node.js 20 以降を使う開発・検証では、次を実行します。

```powershell
node --test tests/import-ui.test.cjs tests/account-choice.test.cjs tests/processing.test.cjs tests/detail-navigation.test.cjs
node --check web/app.js
```

CI では `web/processing.js`、`web/import.js`、`web/security.js` にも `node --check` を実行します。
これらの検査はモデルをダウンロードせず、推論の精度を示す証拠にもなりません。

## 5. 任意の検査（明確に分ける）

### A. ブラウザー検査（保存済み架空データの再現）

```powershell
npm ci --ignore-scripts --no-audit --no-fund
npx playwright install chromium
$env:RYOSHU_QA_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm run test:browser
```

`scripts/serve_browser_qa.py` は新しい空のデータディレクトリを必要とし、既定の帳簿を拒否します。
空いているループバックポートを自動選択し、UI には「検証用」と表示します。
OCR と AI の結果は保存済みの架空の観測データから再現するため、新規推論ではなく、性能の証拠でもありません。
記録は `.local/browser-qa` に残ります。その他のスモーク検査は CI 定義を参照してください。

### B. 実 OCR・実モデル推論

実行手順は [CONTRIBUTING.md](CONTRIBUTING.md) の「実OCR・モデルを含む100件」を参照し、ここではコマンドを複製しません。
OCR、モデル、ランタイム、パイプラインの動作に変更の影響がある場合に、影響ベースの検査として実行します。
モデルや VC++ の利用条件への同意とセットアップ時のダウンロードには、それぞれに必要な承認が要ります。
セットアップは USERPROFILE 配下のタスクルートも使うため、次節の注意を先に読んでください。
`--output` には自分で選んだ新しいディレクトリを、ポートには未使用のものを指定します。
既存の実帳簿を使わず、過去の証拠を上書きせず、CPU 負荷を考慮します。
出力には OCR テキスト、データベース、ローカルパスが含まれるため、Git 無視対象の `.local/` に留め、公開しません。
結果は非決定的です。`--allow-model-offline` を使う実行は診断であり、AI 側の成功を示しません。
文書だけの変更では、この検証を新たに実行する必要はありません。

## 6. アプリの起動・デバッグについての注意

- `start.ps1` はポート `8766`（`http://127.0.0.1:8766`）と、タスクルート `$env:USERPROFILE\StudioYebisu\ReceiptLedger`（仮想環境、`runtime`）を固定して使う。
- 帳簿は `-DataDir` の指定が優先され、指定がなければ記憶された `runtime\data-location.json`、それもなければ既定の `data` が選ばれる。
- `-DataDir` には既に帳簿があるフォルダーを指定する必要がある。指定してもタスクルート、ランタイム、ポートは共用される。
- `RECEIPT_LEDGER_PORT` を設定しても `start.ps1` のポートは変わらない。
- `setup.ps1` は USERPROFILE 配下のタスクルートに準備・ダウンロードを行う。`RECEIPT_LEDGER_DATA` と `RECEIPT_LEDGER_RUNTIME_ROOT` はセットアップやダウンロードを隔離しない。
- `python -m receipt_ledger` はネイティブモジュールの読み込み前に検証済み C++ ランタイムを有効化し、専用に準備されたランタイムを必要とする。
- `RECEIPT_LEDGER_DATA` がなければ `python -m receipt_ledger` は実際の既定 `data` ディレクトリを使う。`HOST` はループバック固定。
- `config.py` が読む `RECEIPT_LEDGER_DATA`、`RECEIPT_LEDGER_RUNTIME_ROOT`、`RECEIPT_LEDGER_PORT` は、モジュールをインポートする前に設定する。

自動テストやデバッグで実帳簿に対して通常のランチャーやモジュール起動を使ってはいけません。
`npm run test:browser`、`verify_csv_workflow.py`、pytest など、組み込みの架空データ検証を優先します。
既存の実帳簿を使うには利用者の承認が必要です。隔離された架空のパスとポートはエージェントが選びます。
詳細は [docs/INSTALL.md](docs/INSTALL.md) と [CONTRIBUTING.md](CONTRIBUTING.md) を参照してください。

## 7. セキュリティとデータの約束

帳簿データベースは SQLCipher、保存された原本・プレビュー・バックアップは AES-256-GCM で保護されます。
鍵は Windows 利用者ごとの DPAPI で保護し、復元用パスワードによる鍵のラップも備えます。
仕組みは [docs/SECURITY-DESIGN.md](docs/SECURITY-DESIGN.md) を参照してください。
鍵なしの平文保存、テスト専用の認証回避フラグ、ループバックや認証の弱化を製品コードに追加してはいけません。
テストでは架空の帳簿を実際の DPAPI とセッションの仕組みで明示的に暗号化・保護します。架空のデータであっても、保護を省略するものではありません。
実領収書、実帳簿、バックアップ、鍵・認証ファイル、その他の利用者データをレビューモデルや外部サービスへ送信してはいけません。
公開ソースコードの AI レビューは、必要な承認の範囲内で行えます。
出力 CSV と出力された原本は設計上平文なので、保護されているとは説明しません。詳細は [docs/PRIVACY.md](docs/PRIVACY.md) を参照してください。
不具合報告には架空の最小サンプルだけを使います。[SECURITY.md](SECURITY.md) も確認してください。

## 8. 分類・編集の約束

- 初期科目の変更時に、利用者が編集・追加した科目、☆ お気に入り、確認済みの仕訳を保持する。
- 提案された内容を、利用者に無断で確認済みへ昇格させない。
- 返金、外貨、資産、混在する税率、家事按分、用途不明の飲食や支出を、通常の経費へ無理に寄せない。
- CSV マッピングや税区分の変更には一次資料の根拠を示し、出力対象から外す条件や出力を中止する条件も更新する。

出力と科目の詳細は [docs/CSV-EXPORT.md](docs/CSV-EXPORT.md) と [docs/ACCOUNTS.md](docs/ACCOUNTS.md) を正本とします。

## 9. 検証記録の凍結

`samples/receipts-100-v2/`、期待値、採点・オラクルのロジック、`docs/testing/` 配下の公開結果は履歴資料です。
新しい出力に合わせたり見栄えを整えたりするために編集してはいけません。
ブラウザーの再現検査を、実 OCR・実モデルによる新規検証として扱ってはいけません。
v1.4 ではランタイムとモデルを同時に変更したため、差分をランタイムだけの高速化として表現しないでください。
古い Ollama の結果を移行後の結果として扱ってもいけません。
新しい証拠は新しい出力ディレクトリに分けて記録し、ローカル実行と GitHub Actions の結果を区別して報告します。

## 10. 変更時の検査と公開物

変更の影響に応じて、必要な検査を自律的に選びます。

- 文書のみ: `check_publication.py` とリンク確認。実 OCR・実モデルの新規検証は不要。
- Python: pytest。出力、会計、ワークフローに触れる場合は CSV ワークフロー検証も追加。
- Web: Node 単体テスト、`node --check`、ブラウザー検査。
- パッケージ・ランチャー: CI の PowerShell 構文検査と [packaging/windows/README.md](packaging/windows/README.md) の手順。
- 固定情報・ロックファイル: 完全な CI。
- OCR・モデル・パイプライン: 第5節Bの実推論検証。

ソース ZIP は `scripts/make_source_zip.py` が Git で追跡されたファイルを指定 ref から `git archive` で集めて作ります。
除外規則は `build_windows_package.py` と共有し、未追跡ファイルや非公開ファイルまで対象を広げないでください。
`THIRD_PARTY.md`、`LICENSE`、モデル利用条件の開示内容は、変更に合わせて同期させます。
公開済みリリース資産は変更せず、文書だけの変更で再ビルドやバージョン更新は行いません。

## 11. 関連文書

- 利用者向け概要: [README.md](README.md)
- 開発と検証: [CONTRIBUTING.md](CONTRIBUTING.md)
- 設計: [DESIGN.md](DESIGN.md)
- セキュリティ報告: [SECURITY.md](SECURITY.md)
- 導入と要件: [docs/INSTALL.md](docs/INSTALL.md)、[docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)
- 検証記録: [docs/testing/v1.4/README.md](docs/testing/v1.4/README.md)
- Windows パッケージ: [packaging/windows/README.md](packaging/windows/README.md)
