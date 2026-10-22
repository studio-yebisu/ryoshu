# 動作要件と確認済み環境

[README](../README.md) / [導入手順](INSTALL.md) / [検証結果](testing/README.md)

Windows向けのローカルWebアプリです。以下は必要なソフトウェアと、2026-10-22に確認した範囲です。最低RAM・CPU性能・処理速度を保証する測定はまだ行っていません。

## 用意するもの

| 項目 | 内容 |
| --- | --- |
| OS・CPU構成 | Windows 11の64bit環境で確認。Pythonも64bitを使用 |
| Python | Python 3.12.15・64bit。固定・検証したuvで専用環境へ自動準備。利用者の別途導入は不要 |
| PowerShell | Windows PowerShell 5.1以降。起動用cmdは`powershell`を呼び出す |
| Ollama | Windows版Ollamaをインストールし、CLIの`ollama`をPATHから呼び出せること |
| モデル | Indra OCRの重みと`qwen3:4b`。`setup.ps1`が取得。モデルはソースに同梱しない |
| フォント | Windowsの日本語フォント。確認画面で原本と日本語の入力欄を表示できること |
| ブラウザー | JavaScript・ファイル選択・ダウンロードに対応したブラウザー。画面はChromiumで検証。大きなバックアップの保存はChrome/Edge推奨 |
| GPU | 専用GPU不要。OCR・QwenともCPU実行。QwenはGPU使用数0、CPUスレッド数6に設定 |
| RAM | OS・ブラウザー・OCR・Qwenを同時に動かす余裕が必要。モデルファイルの約2.5GBはRAMの総必要量を示す値ではない。最低RAMは未測定 |
| ディスク | 初回に依存ライブラリ、専用venv、OCRキャッシュ、約2.5GBのQwenモデルを保存する数GBの空き容量。加えて原本・プレビュー・DB・バックアップ分が増える。最低空き容量は未測定 |
| ネット接続 | 初回セットアップ・モデル取得時に必要。準備後の読み取り・科目判断では外部OCRやLLMへ送信しない |
| ローカル接続 | アプリは`127.0.0.1:8766`、Ollamaは`127.0.0.1:11434`。これらをローカルで利用できること |

Gitはcloneする場合だけ必要です。ソースZIPから使う場合は不要です。セットアップと通常の起動に管理者権限は必要ありません。

## 確認した構成

| 項目 | 確認値 |
| --- | --- |
| OS | Windows 11 Pro・64bit |
| CPU | AMD Ryzen 9 7950X3D |
| 搭載メモリ | 64GB（OS認識量は約63GiB） |
| Python | 3.12.15・64bit |
| Windows PowerShell | 5.1 |
| Ollama | 0.40.7 |
| OCR | Indra OCR 1.1.2 / CPUExecutionProvider |
| 意味判断 | Qwen3 4B、Q4_K_M、`qwen3:4b` / CPU・6スレッド・コンテキスト8,192 |
| 保存 | SQLCipher 4.19.0 / SQLite 3.53.4 / スキーマ3 |

この検証機の構成は最低要件ではありません。小容量RAMのPC、他のCPU、Windows 10で同じ動作や速度を確認したものではありません。鍵保護はWindows専用で、macOS・Linuxはこの版の対象外です。OCRとモデルの識別子・100件の採点条件は[検証ページ](testing/README.md)にあります。

## 導入時の注意

IndraのコードとOCR重みはライセンスが異なります。重みには事業規模・資金調達・競合用途などの制限があるため、[ライセンス資料](../THIRD_PARTY.md)と原文を確認してからセットアップしてください。

初回に外部の配布元から依存・モデルを取得します。ダウンロード先の更新で、モデルrevisionやOllamaのdigestが検証時と異なる可能性があります。インストールしたモデルのdigestはAI候補にも保存します。

保存先の既定は`%USERPROFILE%\StudioYebisu\ReceiptLedger`です。帳簿・保存済み原本・プレビュー・バックアップを暗号化します。書き出すCSVや原本は暗号化されない通常のファイルです。保存先の権限と空き容量を管理してください。[保存と通信の範囲](PRIVACY.md)
