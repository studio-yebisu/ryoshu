# Windowsセットアップのビルド

アプリの公開ソースZIPから、管理者権限が不要な利用者単位のセットアップEXEを作ります。Python・ライブラリ・OCR重み・LLM重み・Ollamaは同梱せず、既存の同意付き初回セットアップで取得します。コード署名はありません。

## ビルドツール

公式の[Inno Setup 7.1.0 x64](https://github.com/jrsoftware/issrc/releases/tag/is-7_1_0)を使います。コンパイラを自動ダウンロード・自動実行する処理はビルドスクリプトにありません。

- 公式ファイル：`innosetup-7.1.0-x64.exe`
- SHA-256：`0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f`
- インストーラのAuthenticode署名：検証時`Valid`、署名者`Pyrsys B.V.`。
- `ISCC.exe`のSHA-256：`d06ebd38f38e3cee60a3c50cc45bd449d77e0bc6a5cabc607ea9886808e4de1a`。ビルドスクリプトでも照合します。
- [Inno Setupのライセンス](https://github.com/jrsoftware/issrc/blob/is-7_1_0/license.txt)

コンパイラ導入後、次を実行します。Node.jsは不要です。

```powershell
python scripts/build_windows_package.py --source-zip Ryoshu-1.3.4-source.zip --output-dir dist/windows-1.3.4 --iscc 'C:\path\to\ISCC.exe'
```

ZIP内のルートは`Ryoshu-<version>/`で、アプリの`__version__`と一致する必要があります。起動に使う`python-runtime.ps1`・`bootstrap-uv.ps1`・`launcher-process.ps1`も必須ファイルとして照合します。公開用ソース以外の`.local`・DB・鍵・ログ・モデル・仮想環境、危険なパスやシンボリックリンクは拒否します。出力は`Ryoshu-<version>-windows-x64-setup.exe`と`.build.json`です。後者には入力ZIP・コンパイラ・全同梱ファイル・出力EXEのSHA-256を記録します。同名の成果物は上書きしません。

## インストール仕様

- Windows 11 x64。既定は`%LOCALAPPDATA%\Programs\StudioYebisu\Ryoshu`。
- スタートメニューに「Ryoshu」「Ryoshuを終了」「Ryoshuの導入ガイド」を作成。デスクトップは任意。
- Ollama未導入なら起動時にダイアログで案内。本人が選んだ場合だけ公式ページを開きます。導入済みなら既存`start.ps1`へ進みます。
- 既存の暗号化帳簿は`%USERPROFILE%\StudioYebisu\ReceiptLedger`に分離。帳簿の保存先へアプリを配置する操作は拒否します。
- 起動中の対象フォルダーを更新・削除しません。判定は公開healthと非秘密の`runtime/server.json`の起動元（`source`）・PIDのみを参照し、帳簿・セッション・鍵・原本を開きません。起動元を確認できない場合も止まります。
- アンインストールはインストーラが配置したアプリとショートカットを削除します。帳簿・原本・復元情報・実行環境・AIモデルは削除しません。利用者が追加したファイルも一括削除しません。

`packaging/windows/launch.ps1 -CheckOnly`は、通常の起動やセットアップを実行せず、帳簿にも接続せずにJSONを返します。Ryoshuが実際に使う`%USERPROFILE%\StudioYebisu\ReceiptLedger\venv\Scripts\python.exe`を起動し、Python 3.12.15以降の3.12系・64bitと必要な暗号化ライブラリを確認します。検査の正本は[Get-RyoshuPythonStatus](../../scripts/python-runtime.ps1)です。Pythonの検査は`-I -B`で実行し、ユーザーのPython設定の影響と診断によるバイトコードの書き込みを避けます。

`python_prepared`はこの起動・互換性・ライブラリ検査の結果です。実行ファイルが存在するだけでは`true`になりません。`python_exists`・`python_runnable`・`python_compatible`・`python_version`・`python_reason`で状態を区別し、`source_complete`では補助スクリプトの同梱も確認します。OCR・Ollamaモデルの準備完了や推論品質はこの診断では確認しません。初回セットアップ全体の完了は、依存ライブラリ・OCRモデル・Ollamaモデルの準備成功後に別途記録します。

## 配布EXEの実インストール検証

既存のインストール登録がないWindows検証機で、空の専用出力先を指定します。既存の登録があれば試験を拒否します。

```powershell
python scripts/verify_windows_package.py --installer dist/windows-1.3.4/Ryoshu-1.3.4-windows-x64-setup.exe --source-zip Ryoshu-1.3.4-source.zip --qa-python C:\path\to\qa\python.exe --output-dir .local/windows-install-check
```

検査はインストール・全payloadのソースZIPとのバイト照合・3つのショートカット・Windows PowerShell起動・更新の起動元保護・別ポートのアプリ起動・アンインストールを実行します。新しい架空の暗号化帳簿と追加した利用者ファイルが削除・変更されないことを照合します。通常の帳簿を開かず、OCR・AIの新規推論も行いません。結果の`verification.json`には絶対パスや起動トークンを含めません。失敗時も、自分で作った検証用アプリの削除と自分の検証サーバーの停止だけを行います。

別ポートのアプリ起動には、`--qa-python`で指定した準備済みの検証用Pythonを使います。この結果が確認するのはインストールされたコードの実行であり、スタートメニューからの通常起動や、Ryoshu専用Pythonの作成・依存とモデルの取得を含む初回セットアップの成否は別に確認する必要があります。`-CheckOnly`のPython検査も、OCR・モデル取得の完了を示すものではありません。[v1.3.4の実行範囲と検証待ち項目](../../docs/testing/v1.3.4/README.md)
