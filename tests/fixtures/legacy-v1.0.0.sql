BEGIN TRANSACTION;
CREATE TABLE account_master (
              code TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,name_key TEXT NOT NULL UNIQUE,
              kind TEXT NOT NULL CHECK(kind IN ('expense','asset','cash','bank','liability','equity')),
              description TEXT NOT NULL,aliases_json TEXT NOT NULL,questions_json TEXT NOT NULL,sources_json TEXT NOT NULL,
              selectable INTEGER NOT NULL CHECK(selectable IN (0,1)),active INTEGER NOT NULL CHECK(active IN (0,1)),
              seeded INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL
            , hints_json TEXT NOT NULL DEFAULT '{}');
INSERT INTO "account_master" VALUES('E101','消耗品費','消耗品費','expense','文具、用紙、少額の事務用品','["事務用品費"]','["購入内容が事業用か","機器は資産計上の検討が必要か"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["コピー用紙","ボールペン","文具","事務用品","プリンター用紙","インク","トナー","ノート","画材","絵具","キャンバス","封筒","朱肉","ゴム印","のり","軍手","梱包テープ","乾電池","ゴミ袋","クリアファイル","洗剤","植物","木ネジ","ペンキ","家具","椅子"],"unless_any":["ノートPC","返品","返金"]}');
INSERT INTO "account_master" VALUES('E102','通信費','通信費','expense','電話、インターネット、レンタルサーバー','["電話代","インターネット代"]','["対象期間","私用と共用なら事業割合の根拠"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["携帯電話","電話料金","インターネット","光回線","レンタルサーバー","ホスティング","通信料金","サーバー","ドメイン","クラウドサービス","SaaS","ライセンス月額","vCPU","コンピューティング","クラウド月額","クラウド利用","ロードバランサー"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E103','旅費交通費','旅費交通費','expense','業務の電車、バス、タクシー、出張宿泊','["交通費","旅費"]','["出張・移動の業務目的と行先"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["タクシー","乗車券","鉄道","電車","バス運賃","宿泊料金","ホテル宿泊","航空券","新幹線","旅客運賃","運賃","駐車","高速道路","宿泊","ホテル","旅館","ETC","有料道路","料金所"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E104','会議費','会議費','expense','業務の打合せ、会議室、会議に伴う飲食','[]','["打合せの目的","相手と人数","私的な飲食ではないか"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["打合せ","打ち合わせ","会議","商談"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E105','接待交際費','接待交際費','expense','取引先との会食、贈答、懇親','["交際費"]','["相手と事業上の関係","会食・贈答の目的"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["贈答","接待","懇親会","会食"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E106','広告宣伝費','広告宣伝費','expense','広告掲載、販促物、サービスの宣伝','["広告費","販売促進費"]','["広告した事業・案件と掲載期間"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["広告","販促","チラシ印刷","広告出稿","広告掲載","会社案内","パンフレット","SNS動画広告","検索連動型"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E107','新聞図書費','新聞図書費','expense','業務用の専門書、新聞、調査資料','["図書費","書籍代"]','["書籍と業務の関係"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["書籍","技術書","専門書","参考書","新聞","電子書籍","資料集","雑誌","書名","入門書"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E108','地代家賃','地代家賃','expense','事務所、作業場、コワーキングの賃料','["家賃"]','["賃借した期間と場所","共用なら事業割合の根拠"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["事務所賃料","事務所家賃","地代","作業場賃料","コワーキング"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E109','水道光熱費','水道光熱費','expense','業務で使った電気、ガス、水道','["光熱費","電気代"]','["利用期間","共用なら事業割合の根拠"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["電気料金","ガス料金","水道料金","電力料金"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E110','支払手数料','支払手数料','expense','振込・決済手数料、月額SaaS等のサービス料','["手数料"]','["サービスの内容と期間","買切り資産・前払ではないか"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["振込手数料","決済手数料","月額SaaS","SaaS利用料","制作サービス月額"],"unless_any":["年額","1年分","年払い"]}');
INSERT INTO "account_master" VALUES('E111','外注工賃','外注工賃','expense','制作、デザイン、プログラム等の業務委託','["外注費","業務委託費","委託料"]','["委託内容と完了日","源泉徴収等の別処理の有無"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["デザイン制作委託","イラスト制作委託","開発委託","業務委託","外注","制作委託","翻訳","ロゴ制作","デザイン制作","ミックス","版下制作","デザイン料","制作料","ウェブサイト制作"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E112','荷造運賃','荷造運賃','expense','発送、梱包、配送料','["荷造運賃費","発送費","送料"]','["発送した業務上の物品と相手"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["送料","配送料","宅配便","発送用梱包"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E113','修繕費','修繕費','expense','業務設備の修理・通常の保守','["修理費"]','["原状回復か、機能向上や資産取得か"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["修理","部品交換","修繕","保守点検","木ネジ","補修","洗浄"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E114','損害保険料','損害保険料','expense','業務用の損害保険・賠償責任保険','["保険料"]','["補償対象、契約期間、事業使用部分"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["損害保険","賠償責任保険","自動車保険","火災保険"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E115','租税公課','租税公課','expense','事業に関する税・公的負担・使用した収入印紙等','[]','["税の種類","所得税・住民税・罰金等ではないか","印紙等を業務で使用したか"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["収入印紙","事業税","固定資産税","登録免許税"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E116','研修費','研修費','expense','業務に関する講座、セミナー、研修','["教育研修費"]','["講座内容と業務の関係"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["セミナー","研修","技術講座","受講料","受講枠","eラーニング","研修プラン"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E117','諸会費','諸会費','expense','業務上の団体、職能団体等の会費','["会費"]','["団体と事業の関係","会費の内容と税の記載"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["会員会費","年会費","団体会費","諸会費"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E118','賃借料','賃借料','expense','機材・設備等の短期レンタル','["レンタル料","リース料"]','["契約期間","購入・リース資産の処理が必要か"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["レンタル","賃借","機材リース","コワーキング","フリー席","ワークラウンジ","リース料"],"unless_any":["レンタルサーバー"]}');
INSERT INTO "account_master" VALUES('E119','雑費','雑費','expense','他の科目に該当しない業務上の少額費用','[]','["他の具体的な科目がないか","継続する費用なら科目を分けるか"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":[],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E120','支払報酬料','支払報酬料','expense','税理士、弁護士など専門家への報酬','["支払報酬","報酬料金"]','["役務と業務の関係","源泉徴収等の別処理の有無"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["税理士報酬","弁護士報酬","専門家報酬"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E121','福利厚生費','福利厚生費','expense','従業員向けの福利厚生','[]','["本人や家族の私用ではないか","対象者と社内の取扱い"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["従業員福利厚生","従業員健康診断","従業員慶弔"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E122','給料賃金','給料賃金','expense','従業員の給与・賃金','["給与賃金","人件費"]','["給与台帳、源泉徴収、専従者等の処理が必要か"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["給与","給料","賃金"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E123','利子割引料','利子割引料','expense','事業の借入利息等。元本返済とは分ける','["支払利息"]','["事業借入か","元本返済との区分"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["借入利息","融資利息","支払利息"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E124','研究開発費','研究開発費','expense','事業に関する調査、研究、試作','[]','["事業との関係","固定資産や別の制作原価に該当しないか"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["研究試作","研究開発","試作材料"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E125','仕入高','仕入高','expense','販売・製造のための商品・材料の購入','["仕入"]','["棚卸や売上原価の計算が別途必要か"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["商品仕入","販売用仕入","製造用材料"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('A101','工具器具備品','工具器具備品','asset','PC、モニター、業務用の機器・備品の資産候補','["器具備品","備品","備品費"]','["取得価額、利用開始日、経理方式","耐用年数や少額資産の取扱い"]','["necessary_expenses","depreciation"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["パソコン","ノートPC","デスクトップ","業務用モニター","プリンター本体","カメラ本体","オシロスコープ","自作PC","会議用テーブル","中古プリンタ","業務用モニタ","ワークチェア","レーザープリンタ","オフィスチェア"],"unless_any":["修理","レンタル","返品","返金","部品交換"]}');
INSERT INTO "account_master" VALUES('A102','ソフトウェア','ソフトウェア','asset','買切りソフトウェア等の無形資産候補','[]','["買切りか継続サービスか","取得価額、利用期間、償却の扱い"]','["necessary_expenses","depreciation"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["買切りソフト","永久ライセンス","ソフトウェア買切"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('A103','車両運搬具','車両運搬具','asset','業務用の車両等の資産候補','[]','["事業割合","取得価額、利用開始日、償却の扱い"]','["necessary_expenses","depreciation"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["車両購入","営業用自動車"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('A104','前払費用','前払費用','asset','未経過期間のサービス料・保険料等','["前払い費用"]','["サービス提供期間","当期分と翌期分の区分や適用条件"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["年額","年払い","前払い","1年分","年間契約"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('B101','現金','現金','cash','事業用の現金','[]','[]','["necessary_expenses"]',0,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":[],"unless_any":[]}');
INSERT INTO "account_master" VALUES('B102','普通預金','普通預金','bank','事業用の普通預金','[]','[]','["necessary_expenses"]',0,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":[],"unless_any":[]}');
INSERT INTO "account_master" VALUES('L101','未払金','未払金','liability','事業用カード等の支払前の債務','[]','[]','["necessary_expenses"]',0,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":[],"unless_any":[]}');
INSERT INTO "account_master" VALUES('O101','事業主借','事業主借','equity','個人のお金で立て替えた事業分等','[]','[]','["necessary_expenses"]',0,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":[],"unless_any":[]}');
INSERT INTO "account_master" VALUES('O102','事業主貸','事業主貸','equity','事業のお金で支払った私用分等','[]','[]','["necessary_expenses"]',0,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":[],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E126','車両費','車両費','expense','事業用車両の燃料・洗車・整備等','["自動車費"]','["業務との関係","私用分・期間配分・資産処理の要否"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["ガソリン","ハイオク","レギュラー","給油","洗車","タイヤ","車検"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E127','印刷製本費','印刷製本費','expense','名刺・業務資料等の印刷製本','["印刷費"]','["業務との関係","私用分・期間配分・資産処理の要否"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["名刺印刷","製本","チラシ印刷","冊子印刷","オンデマンド印刷","名刺","会社案内","パンフレット","印刷","印刷工房","チラシ"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('E128','材料費','材料費','expense','製造・試作のための材料','["製造原価"]','["業務との関係","私用分・期間配分・資産処理の要否"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["試作部品","電子部品","抵抗器","コンデンサ","製造材料"],"unless_any":[]}');
INSERT INTO "account_master" VALUES('A105','長期前払費用','長期前払費用','asset','複数年にわたるサービス等の前払','[]','["業務との関係","私用分・期間配分・資産処理の要否"]','["necessary_expenses"]',1,1,1,'2026-10-22T05:10:03.649342+00:00','{"any":["複数年契約","長期前払"],"unless_any":[]}');
CREATE TABLE account_policies (
              code TEXT PRIMARY KEY,title TEXT NOT NULL,priority INTEGER NOT NULL,action TEXT NOT NULL,
              match_json TEXT NOT NULL,message TEXT NOT NULL,questions_json TEXT NOT NULL,sources_json TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1
            );
INSERT INTO "account_policies" VALUES('P_REFUND','返品・返金・取消',10,'block','{"document_flag":"refund"}','返品・返金・取消の記載があります。正の経費として登録せず、元取引と対応する返金仕訳を別途確認してください。','["元の取引と返金額","入金・カード返金の実際"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_PERSONAL_TAX','必要経費に含めない税・罰金等',20,'block','{"any":["所得税","住民税","罰金","反則金","過料"],"unless_any":["源泉"]}','所得税・住民税・罰金等の語句があります。事業の経費への計上は保留し、内容を確認してください。','["税や支出の正式名称"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_WITHHOLDING','源泉徴収を伴う支払',25,'hold','{"any":["源泉所得税","源泉徴収","源泉税"]}','源泉徴収の記載があります。報酬の総額、控除額、支払額、預り金等の仕訳を確認してください。','["税込報酬総額","源泉徴収額","実際の支払額"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_MIXED_ITEMS','複数用途の商品が混在',30,'hold','{"all_groups":[["コピー用紙","ボールペン","文具ノート","リングノート"],["食品","おにぎり","パン","弁当","コーヒー"]],"scope":"purchases","ignore_phrases":["食品保存容器","食品用容器","食品用ラップ"]}','事務用品と飲食品等が混在しています。単一科目にまとめられるか、私用分や別科目への分割が必要か確認してください。','["商品ごとの業務用途と金額","私用分の有無"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_FOOD_CONTEXT','飲食の業務目的が不明',40,'hold','{"any":["コーヒー","珈琲","喫茶","カフェ","レストラン","飲食","居酒屋","弁当","ランチ","ディナー","ビール","ワイン","食事","ベーカリー","コーヒ","食パン","あんぱん","青果","みかん","りんご","牛乳","たまご","食品","バナナ","ケータリング","ミニバー","朝食","野菜","ごぼう","みょうが","青梅"],"unless_any":["会議室","商談","会議","打合せ","打ち合わせ","取引先","接待"],"scope":"purchases","ignore_phrases":["食品保存容器","食品用容器","食品用ラップ"]}','飲食の記載があります。会議費・接待交際費・私用などの区分には、利用目的や相手の確認が必要です。','["利用目的","相手と人数","事業との関係"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_ASSET','機器・備品の資産検討',50,'asset_review','{"any":["パソコン","ノートPC","デスクトップ","カメラ","モニター","業務用機器","買切りソフト","家具","テーブル","チェア","オシロスコープ","プリンタ","PCパーツ","業務用モニタ"],"min_total":50000,"unless_any":["修理","レンタル","分解洗浄"]}','一定額以上の機器・備品等です。金額は確認を促すためのアプリの条件であり、税務の資産基準ではありません。経理方式、取得価額、利用開始日、資産・少額資産等の扱いを確認してください。','["取得価額と経理方式","利用開始日","資産・少額資産等の適用条件"]','["depreciation"]',1);
INSERT INTO "account_policies" VALUES('P_PREPAID','年払・複数期間の支払',60,'hold','{"any":["年額","年間契約","1年分","一年分","前払い","年払い"],"unless_any":["永久ライセンス","買切","買い切"]}','年払・複数期間の支払です。対象期間と当期・翌期の扱いを確認してください。このアプリは期間配分を自動計算しません。','["サービス開始日と終了日","当期分・翌期分の処理"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_SHARED','家事関連費の確認',70,'review','{"any":["携帯電話","インターネット","光回線","電気料金","ガス料金","水道料金","自宅兼"]}','私用と共用になることがある支出です。実際の利用に基づく事業割合と、その根拠を確認してください。','["事業で使った割合","その割合の根拠"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_MIXED_TAX','軽減税率と標準税率の混在',80,'review','{"all_groups":[["8%","8％"],["10%","10％"]]}','8%と10%の記載が混在しています。記載税率を混在として保存し、税率ごとの内訳は原本から確認してください。','["税率ごとの対象額と記載税額"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_UNPAID','未払い・見積・内部資料',15,'block','{"document_flag":"unpaid"}','未払い・見積・内部資料の記載があります。支払済みの領収書と照合してください。','["実際の支払日と支払額","支払済みの証憑"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_FOREIGN','外貨の円額が未確定',35,'hold','{"document_flag":"foreign"}','外貨の記載があります。実際に確定した円額をカード明細等から確認してください。','["円建て確定額と換算根拠"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_PRIVATE_GOODS','用途で取扱いが変わる購入',45,'hold','{"any":["ロゴ無","カジュアル衣料","寄附","寄付","家族","私用","元金","金券","商品券","住民票"]}','私用分や経費以外の支払を含む可能性があります。内訳と業務用途を確認してください。','["業務用途と私用の区分","経費以外の金額"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_EVENT_PURPOSE','入場・参加目的の確認',42,'hold','{"any":["入場料","イベント参加費","イベントチケット"],"unless_any":["研修","受講","商談","出展","展示会"]}','入場・参加料の用途が原本から確定できません。業務との関係と私用分を確認してください。','["参加目的と事業との関係"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_HONORARIUM','謝礼・謝金の支払確認',26,'hold','{"any":["出演謝礼","講演謝礼","謝礼","謝金"]}','謝礼・謝金の記載があります。支払先の属性、報酬総額、源泉徴収の要否や別仕訳を確認してください。','["支払先と役務内容","源泉徴収の要否と金額"]','["necessary_expenses"]',1);
INSERT INTO "account_policies" VALUES('P_RESIDENTIAL_RENT','居住用家賃の用途確認',65,'hold','{"scope":"purchases","all_groups":[["居住用","住居用","住宅家賃","自宅家賃"],["家賃","賃料"]]}','居住用の家賃の記載があります。業務で使う場所・事業割合と金額の根拠を確認してから、科目を選んでください。','["借りた場所の業務用途","事業で使う割合と根拠","今回の支払額と対象期間"]','["necessary_expenses"]',1);
CREATE TABLE account_sources (
              id TEXT PRIMARY KEY,title TEXT NOT NULL,url TEXT NOT NULL,checked_at TEXT NOT NULL,scope TEXT NOT NULL
            );
INSERT INTO "account_sources" VALUES('necessary_expenses','国税庁 No.2210 必要経費の知識','https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/2210.htm','2026-10-21','業務との関係、家事関連費、必要経費にならない支出、期間の考え方');
INSERT INTO "account_sources" VALUES('depreciation','国税庁 No.2100 減価償却のあらまし','https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/2100.htm','2026-10-21','資産、取得価額、経理方式、条件により異なる少額資産の取扱い。特例の適用は自動判定しない');
CREATE TABLE audit (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT,
                  receipt_id TEXT NOT NULL, at TEXT NOT NULL,
                  action TEXT NOT NULL, detail_json TEXT NOT NULL
                );
INSERT INTO "audit" VALUES(1,'8ea86261f61b4ce68c421d5e1c2a3e49','2026-10-22T05:10:03.658800+00:00','import','{"original_name":"V044.pdf","sha256":"297374a68acb60052ff363440574fd84e593b12402f68290d1b9f846a02fe124"}');
INSERT INTO "audit" VALUES(2,'8ea86261f61b4ce68c421d5e1c2a3e49','2026-10-22T05:10:03.661401+00:00','analysis','{"model":null,"error":"","ocr":{"text":"架空の機密OCRセンチネル","page_count":1},"suggestion":{"merchant":"架空の機密取引先","date":"2024-06-27","total":13479,"account":"通信費"}}');
INSERT INTO "audit" VALUES(3,'8ea86261f61b4ce68c421d5e1c2a3e49','2026-10-22T05:10:03.668680+00:00','confirm','{"before":{"date":"2024-06-27","merchant":"架空の機密取引先","total":13479,"description":"","account":"通信費","payment":"","business_percent":null,"purpose":"","tax_rate":"unknown","tax_amount":null,"invoice_number":"","export_tax":""},"after":{"date":"2024-06-27","merchant":"架空の機密取引先","total":13479,"description":"","account":"通信費","payment":"owner","business_percent":100,"purpose":"架空の業務連絡用の通信料金","tax_rate":"unknown","tax_amount":null,"invoice_number":"","export_tax":""},"previous_status":"review","remember_account":false}');
INSERT INTO "audit" VALUES(4,'e0f7de4d61d14751a080711b880b4c04','2026-10-22T05:10:03.673431+00:00','import','{"original_name":"V001.pdf","sha256":"581f7e2f848f7ec324f019b44a305cffcff3f63789b5f851b185be1437ac6d9a"}');
INSERT INTO "audit" VALUES(5,'e0f7de4d61d14751a080711b880b4c04','2026-10-22T05:10:03.676069+00:00','analysis','{"model":null,"error":"","ocr":{"text":"架空の機密OCRセンチネル","page_count":1},"suggestion":{"merchant":"架空の機密取引先","date":"2024-06-27","total":13479,"account":"通信費"}}');
CREATE TABLE catalog_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT INTO "catalog_meta" VALUES('seed_version','2026-10-22.2');
CREATE TABLE csv_exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,profile TEXT NOT NULL,
                kind TEXT NOT NULL,month TEXT NOT NULL,sha256 TEXT NOT NULL,row_count INTEGER NOT NULL,
                receipts_json TEXT NOT NULL
            );
CREATE TABLE journal_entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id TEXT NOT NULL REFERENCES receipts(id),
              receipt_version INTEGER NOT NULL,status TEXT NOT NULL CHECK(status IN ('posted','void')),
              created_at TEXT NOT NULL,voided_at TEXT,UNIQUE(receipt_id,receipt_version)
            );
INSERT INTO "journal_entries" VALUES(1,'8ea86261f61b4ce68c421d5e1c2a3e49',3,'posted','2026-10-22T05:10:03.669225+00:00',NULL);
CREATE TABLE journal_lines (
              entry_id INTEGER NOT NULL REFERENCES journal_entries(id),line_no INTEGER NOT NULL,
              account_code TEXT NOT NULL REFERENCES account_master(code),side TEXT NOT NULL CHECK(side IN ('debit','credit')),
              amount INTEGER NOT NULL CHECK(amount>0),PRIMARY KEY(entry_id,line_no)
            );
INSERT INTO "journal_lines" VALUES(1,1,'E102','debit',13479);
INSERT INTO "journal_lines" VALUES(1,2,'O101','credit',13479);
CREATE TABLE merchant_rules (
                  merchant TEXT PRIMARY KEY, account TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                , item_hint TEXT NOT NULL DEFAULT '', account_code TEXT REFERENCES account_master(code));
CREATE TABLE receipt_import_sources (
                receipt_id TEXT NOT NULL REFERENCES receipts(id),
                source_sha256 TEXT NOT NULL, source_name TEXT NOT NULL,
                page INTEGER NOT NULL, page_count INTEGER NOT NULL,
                PRIMARY KEY(source_sha256,page)
            );
CREATE TABLE receipts (
                  id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL,
                  original_name TEXT NOT NULL, extension TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  status TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '',
                  version INTEGER NOT NULL DEFAULT 1,
                  draft_json TEXT NOT NULL, ocr_json TEXT,
                  suggestion_json TEXT, error TEXT NOT NULL DEFAULT ''
                );
INSERT INTO "receipts" VALUES('8ea86261f61b4ce68c421d5e1c2a3e49','297374a68acb60052ff363440574fd84e593b12402f68290d1b9f846a02fe124','V044.pdf','.pdf','2026-10-22T05:10:03.658800+00:00','2026-10-22T05:10:03.668680+00:00','confirmed','',3,'{"date":"2024-06-27","merchant":"架空の機密取引先","total":13479,"description":"","account":"通信費","payment":"owner","business_percent":100,"purpose":"架空の業務連絡用の通信料金","tax_rate":"unknown","tax_amount":null,"invoice_number":"","export_tax":""}','{"text":"架空の機密OCRセンチネル","page_count":1}','{"merchant":"架空の機密取引先","date":"2024-06-27","total":13479,"account":"通信費"}','');
INSERT INTO "receipts" VALUES('e0f7de4d61d14751a080711b880b4c04','581f7e2f848f7ec324f019b44a305cffcff3f63789b5f851b185be1437ac6d9a','V001.pdf','.pdf','2026-10-22T05:10:03.673431+00:00','2026-10-22T05:10:03.676069+00:00','review','',2,'{"date":"2024-06-27","merchant":"架空の機密取引先","total":13479,"description":"","account":"通信費","payment":"","business_percent":null,"purpose":"","tax_rate":"unknown","tax_amount":null,"invoice_number":"","export_tax":""}','{"text":"架空の機密OCRセンチネル","page_count":1}','{"merchant":"架空の機密取引先","date":"2024-06-27","total":13479,"account":"通信費"}','');
CREATE TABLE settings (
                  key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
CREATE UNIQUE INDEX one_posted_entry_per_receipt ON journal_entries(receipt_id) WHERE status='posted';
CREATE UNIQUE INDEX one_receipt_per_source_page ON receipt_import_sources(source_sha256,page);
CREATE INDEX source_receipt_lookup ON receipt_import_sources(receipt_id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('audit',5);
INSERT INTO "sqlite_sequence" VALUES('journal_entries',1);
COMMIT;
PRAGMA user_version=2;
