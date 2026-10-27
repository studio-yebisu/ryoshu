# Ryoshu の動作要件と確認環境

この文書のllama.cppとMicrosoft Visual C++に関する要件はv1.4.0候補向けである。候補版の実装・最低RAM・実インストーラー・新しい100件推論の検証は未完了である。後述のv1.3系の確認環境と区別して読む。

## 1. 一般的な動作要件

導入・起動の全体像は [README](../README.md)、手順は [インストール案内](INSTALL.md)、検証の案内は [テスト記録](testing/README.md) も参照する。以下は利用に必要な一般的な条件であり、次節のコード上の制約と、後述の確認環境は区別する。

| 項目 | 内容 |
| --- | --- |
| OS・CPU構成 | Windows 11 x64のみ。Windows 10は未検証で対象として案内しない。Windows on ARM（arm64）も対象外。Pythonも64bitを使用 |
| Python | Python 3.12.15・64bit。バージョンを固定して検証したuvで専用環境に自動で準備。手動インストールは不要 |
| PowerShell | Windows PowerShell 5.1以降。起動用cmdは`powershell`を呼び出す |
| ローカルAI | Ryoshu専用のllama.cpp b11700を初回セットアップで取得。OllamaやLM Studioの導入・起動は不要 |
| Microsoft実行環境 | Microsoft Visual C++ v14 Redistributable 14.51.36247.0 x64。各利用者がMicrosoftから直接取得し、条件へ明示同意した後にDLLだけをRyoshu専用の保存先へ配置。Visual Studio・コンパイラ・Windows全体へのVC++導入は不要 |
| モデル | Indra OCRの重みとQwen3-4B-Q4_K_M.gguf（約2.5GB）。`setup.ps1`で取得。配布ファイルにはモデルを同梱しない |
| フォント | Windowsの日本語フォント。確認画面で原本と日本語の入力欄を表示できること |
| ブラウザー | JavaScript・ファイル選択・ダウンロードに対応したブラウザー。画面はChromiumで検証。大きなバックアップの保存はChrome/Edge推奨 |
| GPU | OCR・QwenともCPU実行。RyoshuはGPUを使わず、QwenはGPU使用数0、CPUスレッド数6に設定。専用GPUは必要としない |
| RAM | OS・ブラウザー・OCR・Qwenを同時に動かす余裕が必要。モデルファイルが約2.5GBでも、必要なメモリ容量が2.5GBで足りるという意味ではない。最低RAMは未測定 |
| ディスク | 初回に依存ライブラリ、アプリ専用のPython環境、OCRモデルのキャッシュ、llama.cpp実行環境、Microsoft DLL、約2.5GBのQwenモデルを保存する数GBの空き容量。加えて原本・プレビュー・DB・バックアップ分が増える。最低空き容量は未測定 |
| ネット接続 | 初回セットアップ・更新時に必要。Microsoftパッケージは`download.visualstudio.microsoft.com`、利用条件は`visualstudio.microsoft.com`から直接取得する。準備後の読み取り・科目判断では外部OCRやLLMへ送信しない |
| ローカル接続 | アプリは`127.0.0.1:8766`、Ryoshu専用AIも`127.0.0.1`だけを使用。これらをローカルで利用できること |

## 2. 実装から確認できる追加条件

インストーラーの対象アーキテクチャは `x64compatible`、Windowsの `MinVersion` は10.0.22000、権限レベルは `lowest` である。ショートカットは `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass` を使う。アプリ本体を帳簿ルートと重なる場所へインストールすることは拒否される。

llama.cpp実行環境とモデルはRyoshu専用の保存先に置き、固定したサイズとSHA-256が一致するものだけを使う。Ryoshuが専用のAIプロセスを必要時に起動し、利用者がOllamaをPATHへ登録したりサーバーを起動したりする必要はない。環境変数 `RECEIPT_LEDGER_PORT` による指定は可能だが、通常の起動に使う `start.ps1` はTCPポート8766を設定し、このポートが競合している場合は起動を拒否する。

MicrosoftのVC_redist.x64.exeは実行せず、データとして展開する。4つのDLLをRyoshu専用の`crt/14.51.36247.0`とPython保存先へ、うち3つをllama.cpp実行環境へ配置する。置き換えるPython DLLはハッシュ付きのバックアップを`crt/python-backup-*`へ残す。管理者権限やシステム全体への導入は使わない。

アプリが要求するPythonは3.12.xのうち3.12.15以降で、`sys.maxsize > 2**32` の64bitである。管理Pythonは `<DEFAULT_ROOT>/python/cpython-3.12.15-windows-x86_64-none`、venvは `<DEFAULT_ROOT>/venv` に置く。固定ライブラリは `indra-ocr==1.1.2`、`sqlcipher3==0.6.4`、`cryptography==50.0.2` で、範囲指定は `fastapi>=0.116,<1`、`uvicorn>=0.35,<1`、`python-multipart>=0.0.20,<1`、`httpx>=0.28,<1`、`Pillow>=11,<14` である。

入力の上限は、1ファイル20,971,520バイト（20 MiB）、PDF10ページ、一度に50ファイル、分割後300ページ、リクエスト全体67,108,864バイト（64 MiB）、画像25,000,000ピクセルである。形式はJPEG、PNG、WEBP、PDFに限り、アニメーション画像は拒否する。バックアップでは、manifest、DB、原本などを展開した内容の合計サイズが21,474,836,480バイト（20 GiB）に制限される。

## 3. 確認できた環境

次の表は、2026-10-22という日付が付されたv1.3系の単一の確認環境を示す。v1.4.0候補の確認結果ではない。最低動作要件や、この構成以外での非対応を示すものでもない。表の日付と後述の過去の監査記録は別の文脈として扱う。

| 項目 | 確認値 |
| --- | --- |
| OS | Windows 11 Pro・64bit |
| CPU | AMD Ryzen 9 7950X3D |
| 搭載メモリ | 64GB（OS認識量は約63GiB） |
| Python | 3.12.15・64bit |
| Windows PowerShell | 5.1 |
| Ollama | 0.40.7（v1.3系の記録） |
| OCR | Indra OCR 1.1.2 / CPUExecutionProvider |
| 意味判断 | Qwen3 4B、Q4_K_M、`qwen3:4b` / CPU・6スレッド・コンテキスト8,192（v1.3系の記録） |
| 保存 | SQLCipher 4.19.0 / SQLite 3.53.4 / スキーマ3 |

v1.4.0候補の検証に使うPCの搭載メモリは128GBである。これは候補版の動作確認が完了したことや、128GBを最低RAMとする意味ではない。候補版の意味判断はllama.cpp b11700上のQwen3-4B-Q4_K_Mで、CPU・6スレッド・コンテキスト8,192に固定する設計である。

## 4. 未測定の値と検証記録の扱い

最低RAM、最低空きディスク容量、処理速度は未測定である。Qwenの約2.5GBはモデルファイルのサイズであり、必要RAMの測定値ではない。KDFの理論メモリコストをアプリのRAM要件に使うこともできない。

[テスト記録](testing/README.md) のうち過去のセキュリティ監査はv1.1.0 candidateを対象とした記録であり、現在版の監査ではない。現在の実装仕様、単一環境の確認値、過去のテスト対象範囲を結合せず、新しい100ケースの推論テストがあったとも扱わない。旧Ollamaの結果をllama.cppと新しいモデルの結果として引用しない。

## 5. 結果の確認に必要な判断

OCRと科目の出力は候補なので、最終的な経費判断や仕訳の確認は利用者と税理士が行う。

## 6. 関連する情報

依存ソフトウェアの情報は [THIRD_PARTY](../THIRD_PARTY.md)、データの保存と通信の扱いは [プライバシー説明](PRIVACY.md) を参照する。
