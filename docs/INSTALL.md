# 導入と起動

[README](../README.md) / [使い方](GUIDE.md)

## 確認した環境

Windows 11・64bit、Python 3.12.9、Indra OCR 1.1.2、Ollamaの`qwen3:4b`（Q4_K_M）、CPUで検証しました。必要なソフトウェア、検証機の構成、未確認の条件は[動作要件](REQUIREMENTS.md)にまとめています。

Python 3.12またはuv、日本語フォント、Ollamaを用意します。初回は依存関係・OCR重み・約2.5GBのQwenモデルをダウンロードします。数GBのディスク空き容量と、モデルを動かすRAMが必要です。最小RAMや処理速度の保証値はまだ測定していません。

Indra重みのライセンスはコードと異なります。[利用条件](../THIRD_PARTY.md)とリンク先の原文を確認してからセットアップしてください。利用者の条件に適合することをこのスクリプトが判断する仕組みはありません。

## セットアップ

1. [ソースZIP](https://github.com/studio-yebisu/ryoshu/archive/refs/heads/main.zip)を新しいフォルダーに展開するか、`git clone https://github.com/studio-yebisu/ryoshu.git`で取得します。
2. [Ollama公式](https://ollama.com/download)からOllamaを導入します。
3. このフォルダーでPowerShellを開いて実行します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

`setup.ps1`はユーザープロファイル内に専用venvを作成し、`requirements.lock.txt`の固定版を導入します。uvがあればPython 3.12を使用し、なければPATH上のPythonを使用します。後者では先に`python --version`で3.12を確認してください。管理者権限で実行する必要はありません。

Ollamaが起動していない場合はバックグラウンドで起動します。モデルは各配布元から取得し、有料OCRやクラウド推論へ切り替える機能はありません。ダウンロード先の更新により取得されるモデルrevisionやdigestは検証時と異なる可能性があります。検証時の識別子は[結果ページ](testing/README.md)に記録しています。

## 起動・終了

`Ryoshu.cmd`または`起動.cmd`をダブルクリックします。画面は`http://127.0.0.1:8766/`です。ブラウザーを開かず起動する場合は次を使います。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -NoBrowser
```

終了はこのフォルダーで実行します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\stop.ps1
```

終了操作は記録したアプリのプロセスを照合します。Ollamaの共用プロセスや帳簿ファイルは削除しません。8766番が別のアプリに使われている場合は起動を止めます。

## モデルが準備できないとき

OllamaがPATHから見つかるか、`ollama list`に`qwen3:4b`があるか確認します。OCRの初回取得に失敗した場合は接続・空き容量を確認して`setup.ps1`を再実行します。読み取り処理は外部への自動再取得をしないため、キャッシュが欠けている場合もセットアップをやり直します。

診断ログは`%USERPROFILE%\StudioYebisu\ReceiptLedger\runtime`です。問い合わせに実際の領収書、帳簿DB、バックアップZIP、個人名やAPIキーを貼らないでください。

## バックアップと復元

アプリの「設定・保存」から原本とSQLiteのスナップショットをZIPに保存できます。[復元手順](GUIDE.md)では、新しい空のフォルダーに展開し、`RECEIPT_LEDGER_DATA`をその`data`フォルダーへ向けて起動します。通常の起動は既定の保存先に戻ります。モデルと専用venvはバックアップに含みません。
