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
python scripts/build_windows_package.py --source-zip Ryoshu-1.3.2-source.zip --output-dir dist/windows-1.3.2 --iscc 'C:\path\to\ISCC.exe'
```

ZIP内のルートは`Ryoshu-<version>/`で、アプリの`__version__`と一致する必要があります。公開用ソース以外の`.local`・DB・鍵・ログ・モデル・仮想環境、危険なパスやシンボリックリンクは拒否します。出力は`Ryoshu-<version>-windows-x64-setup.exe`と`.build.json`です。後者には入力ZIP・コンパイラ・全同梱ファイル・出力EXEのSHA-256を記録します。同名の成果物は上書きしません。

## インストール仕様

- Windows 11 x64。既定は`%LOCALAPPDATA%\Programs\StudioYebisu\Ryoshu`。
- スタートメニューに「Ryoshu」「Ryoshuを終了」「Ryoshuの導入ガイド」を作成。デスクトップは任意。
- Ollama未導入なら起動時にダイアログで案内。本人が選んだ場合だけ公式ページを開きます。導入済みなら既存`start.ps1`へ進みます。
- 既存の暗号化帳簿は`%USERPROFILE%\StudioYebisu\ReceiptLedger`に分離。帳簿の保存先へアプリを配置する操作は拒否します。
- 起動中の対象フォルダーを更新・削除しません。判定は公開healthと非秘密の`runtime/server.json`の起動元（`source`）・PIDのみを参照し、帳簿・セッション・鍵・原本を開きません。起動元を確認できない場合も止まります。
- アンインストールはインストーラが配置したアプリとショートカットを削除します。帳簿・原本・復元情報・実行環境・AIモデルは削除しません。利用者が追加したファイルも一括削除しません。

インストール用の初回設定は`launch.ps1 -CheckOnly`でDBに接続せず検査できます。これは導入済みコンポーネントの有無の確認であり、OCR・モデルの推論テストではありません。

## 配布EXEの実インストール検証

既存のインストール登録がないWindows検証機で、空の専用出力先を指定します。既存の登録があれば試験を拒否します。

```powershell
python scripts/verify_windows_package.py --installer dist/windows-1.3.2/Ryoshu-1.3.2-windows-x64-setup.exe --source-zip Ryoshu-1.3.2-source.zip --qa-python C:\path\to\qa\python.exe --output-dir .local/windows-install-check
```

検査はインストール・全payloadのソースZIPとのバイト照合・3つのショートカット・Windows PowerShell起動・更新の起動元保護・別ポートのアプリ起動・アンインストールを実行します。新しい架空の暗号化帳簿と追加した利用者ファイルが削除・変更されないことを照合します。通常の帳簿を開かず、OCR・AIの新規推論も行いません。結果の`verification.json`には絶対パスや起動トークンを含めません。失敗時も、自分で作った検証用アプリの削除と自分の検証サーバーの停止だけを行います。
