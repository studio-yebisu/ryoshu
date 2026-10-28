# 不具合の報告とデータの扱い

対象はローカルPC上で使うWindowsアプリです。認証付きの共有サーバーやインターネットへの公開は想定していません。

通常の不具合は架空の再現データで報告してください。実際の領収書・帳簿DB・バックアップ・復元情報・個人情報・APIキーを公開Issueやログへ貼らないでください。

外部への情報送信・権限・暗号化・認証に関わるセキュリティ上の問題は、[GitHubの非公開脆弱性報告](https://github.com/studio-yebisu/ryoshu/security/advisories/new)から非公開で報告してください。架空の最小限の再現データを使い、実際の領収書・帳簿DB・バックアップ・復元用パスワード・鍵・認証情報・APIキーは添付しないでください。専用の報奨金制度と対応期限の約束はありません。

[暗号化と保護範囲](docs/SECURITY-DESIGN.md) / [v1.4.0の検証記録](docs/testing/v1.4/README.md) / [v1.1.0 candidateの過去の監査記録](docs/testing/security/README.md)

暗号化は、同じWindows利用者の不正ソフトやOSの侵害に対する防御の境界ではありません。
