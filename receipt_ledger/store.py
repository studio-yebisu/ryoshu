import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .protection import LedgerKeys, ProtectionError
from .database import CipherDatabase
from .schemas import Draft
from .catalog import account_categories, account_key, apply_account_policies, category_for, resolve_account, seed_catalog


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, root: Path):
        self.root = root
        if any((base / marker).is_file() for base in [root, root.parent] for marker in ['RESTORE_FAILED.txt', 'RESTORE_IN_PROGRESS.txt']):
            raise ValueError('復元が完了していない保存先です。元の帳簿か、正常に復元した帳簿を選んでください。')
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()
        self.keys = LedgerKeys(self.root)
        self.ready = False
        self.database = None
        self.path = self.root / "ledger.enc.sqlite3"
        self.initialization_error = ""
        self._initializing = False
        if self.keys.mode == "unlocked":
            try:
                self.initialize()
            except ProtectionError as exc:
                self.initialization_error = str(exc)

    def initialize(self):
        self.ready = False
        self.database = CipherDatabase(self.root, self.keys)
        self.database.protect_legacy_files()
        (self.root / "receipts").mkdir(exist_ok=True)
        self._initializing = True
        try:
            self._initialize_schema()
        finally:
            self._initializing = False
        self.ready = True
        self.initialization_error = ""

    def _initialize_schema(self):
        root = self.root
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 3:
                raise RuntimeError("このアプリより新しいデータベースです。更新版で開いてください")
            if version == 1:
                backup_dir = root / "backups"
                backup_dir.mkdir(exist_ok=True)
                self.database.snapshot(backup_dir / ("before-catalog-v2-" + uuid.uuid4().hex[:12] + ".sqlite3"), source=db)
            if version == 2:
                backup_dir = root / "backups"
                backup_dir.mkdir(exist_ok=True)
                self.database.snapshot(backup_dir / ("before-account-categories-v3-" + uuid.uuid4().hex[:12] + ".sqlite3"), source=db)
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS receipts (
                  id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL,
                  original_name TEXT NOT NULL, extension TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  status TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '',
                  version INTEGER NOT NULL DEFAULT 1,
                  draft_json TEXT NOT NULL, ocr_json TEXT,
                  suggestion_json TEXT, error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS audit (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT,
                  receipt_id TEXT NOT NULL, at TEXT NOT NULL,
                  action TEXT NOT NULL, detail_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS merchant_rules (
                  merchant TEXT PRIMARY KEY, account TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                  key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)
            self._migrate_catalog(db)

    @contextmanager
    def connect(self):
        if not (self.ready or self._initializing) or self.database is None:
            raise ProtectionError("先に帳簿の保護を設定するか、復元用パスワードで開いてください。")
        db = self.database.open()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _migrate_catalog(self, db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS account_sources (
              id TEXT PRIMARY KEY,title TEXT NOT NULL,url TEXT NOT NULL,checked_at TEXT NOT NULL,scope TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_master (
              code TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,name_key TEXT NOT NULL UNIQUE,
              kind TEXT NOT NULL CHECK(kind IN ('expense','asset','cash','bank','liability','equity')),
              description TEXT NOT NULL,aliases_json TEXT NOT NULL,questions_json TEXT NOT NULL,sources_json TEXT NOT NULL,
              selectable INTEGER NOT NULL CHECK(selectable IN (0,1)),active INTEGER NOT NULL CHECK(active IN (0,1)),
              seeded INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_policies (
              code TEXT PRIMARY KEY,title TEXT NOT NULL,priority INTEGER NOT NULL,action TEXT NOT NULL,
              match_json TEXT NOT NULL,message TEXT NOT NULL,questions_json TEXT NOT NULL,sources_json TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS catalog_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS journal_entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id TEXT NOT NULL REFERENCES receipts(id),
              receipt_version INTEGER NOT NULL,status TEXT NOT NULL CHECK(status IN ('posted','void')),
              created_at TEXT NOT NULL,voided_at TEXT,UNIQUE(receipt_id,receipt_version)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_posted_entry_per_receipt ON journal_entries(receipt_id) WHERE status='posted';
            CREATE TABLE IF NOT EXISTS journal_lines (
              entry_id INTEGER NOT NULL REFERENCES journal_entries(id),line_no INTEGER NOT NULL,
              account_code TEXT NOT NULL REFERENCES account_master(code),side TEXT NOT NULL CHECK(side IN ('debit','credit')),
              amount INTEGER NOT NULL CHECK(amount>0),PRIMARY KEY(entry_id,line_no)
            );
        """)
        initial = db.execute("SELECT value FROM catalog_meta WHERE key='seed_version'").fetchone() is None
        if initial:
            seed = seed_catalog()
            for source in seed["sources"]:
                db.execute("INSERT OR IGNORE INTO account_sources VALUES(?,?,?,?,?)", tuple(source[k] for k in ["id","title","url","checked_at","scope"]))
            for account in seed["accounts"]:
                db.execute("INSERT OR IGNORE INTO account_master(code,name,name_key,kind,description,aliases_json,questions_json,sources_json,selectable,active,seeded,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (account["code"],account["name"],account_key(account["name"]),account["kind"],account["description"],
                            dumps(account["aliases"]),dumps(account["review_questions"]),dumps(account["sources"]),
                            int(account["selectable"]),int(account["active"]),1,now()))
            for p in seed["policies"]:
                db.execute("INSERT OR IGNORE INTO account_policies VALUES(?,?,?,?,?,?,?,?,1)",
                           (p["code"],p["title"],p["priority"],p["action"],dumps(p["match"]),p["message"],dumps(p["questions"]),dumps(p["sources"])))
            db.execute("INSERT INTO catalog_meta VALUES('seed_version',?)", (seed["version"],))
        master_columns={r["name"] for r in db.execute("PRAGMA table_info(account_master)")}
        if "hints_json" not in master_columns:
            db.execute("ALTER TABLE account_master ADD COLUMN hints_json TEXT NOT NULL DEFAULT '{}' ")
            for account in seed_catalog()["accounts"]:
                db.execute("UPDATE account_master SET hints_json=? WHERE code=? AND seeded=1",(dumps(account.get("hints",{})),account["code"]))
        if "category_id" not in master_columns:
            db.execute("ALTER TABLE account_master ADD COLUMN category_id TEXT NOT NULL DEFAULT ''")
            seed_accounts = {a["code"]: a for a in seed_catalog()["accounts"]}
            for row in db.execute("SELECT code,kind,seeded FROM account_master").fetchall():
                account = seed_accounts.get(row["code"]) if row["seeded"] else None
                category_id = category_for(row["kind"], account.get("category_id") if account else None)
                db.execute("UPDATE account_master SET category_id=? WHERE code=?", (category_id, row["code"]))
        self._update_seed(db)
        columns = {r["name"] for r in db.execute("PRAGMA table_info(merchant_rules)")}
        if "item_hint" not in columns:
            db.execute("ALTER TABLE merchant_rules ADD COLUMN item_hint TEXT NOT NULL DEFAULT ''")
        if "account_code" not in columns:
            db.execute("ALTER TABLE merchant_rules ADD COLUMN account_code TEXT REFERENCES account_master(code)")
            db.execute("UPDATE merchant_rules SET account_code=(SELECT code FROM account_master WHERE name=merchant_rules.account)")
        for row in db.execute("SELECT id FROM receipts WHERE status='confirmed' AND id NOT IN (SELECT receipt_id FROM journal_entries WHERE status='posted')").fetchall():
            self._post(db, self.get(row["id"],db))
        db.execute("PRAGMA user_version=3")

    def _update_seed(self, db):
        """Update unchanged defaults only; user definitions and edited fields survive."""
        seed=seed_catalog()
        row=db.execute("SELECT value FROM catalog_meta WHERE key='seed_version'").fetchone()
        version=row['value'] if row else ''
        if version==seed['version']:return
        if version and (not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',version)):
            raise ValueError('科目データの識別情報が正しくありません。バックアップと保存先を確認してください。')
        old_path=Path(__file__).resolve().parents[1]/'data/catalog-history'/(version+'.json')
        if not old_path.is_file():
            # An unknown seed may contain local edits. Add new defaults, never replace it.
            previous={'accounts':[],'policies':[]}
        else:previous=json.loads(old_path.read_text(encoding='utf-8'))
        old_accounts={a['code']:a for a in previous['accounts']}
        old_sources = {source['id']: source for source in previous.get('sources', [])}
        for source in seed['sources']:
            stored = db.execute('SELECT * FROM account_sources WHERE id=?', (source['id'],)).fetchone()
            if stored is None:
                db.execute('INSERT INTO account_sources VALUES(?,?,?,?,?)', tuple(source[k] for k in ['id','title','url','checked_at','scope']))
            elif source['id'] in old_sources:
                for field in ['title','url','checked_at','scope']:
                    if stored[field] == old_sources[source['id']][field] and stored[field] != source[field]:
                        db.execute(f'UPDATE account_sources SET {field}=? WHERE id=?', (source[field], source['id']))
        columns={'description':'description','aliases':'aliases_json','review_questions':'questions_json','sources':'sources_json','hints':'hints_json','category_id':'category_id'}
        for a in seed['accounts']:
            stored=db.execute('SELECT * FROM account_master WHERE code=?',(a['code'],)).fetchone()
            if stored is None:
                db.execute('INSERT OR IGNORE INTO account_master(code,name,name_key,kind,description,aliases_json,questions_json,sources_json,selectable,active,seeded,updated_at,hints_json,category_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (a['code'],a['name'],account_key(a['name']),a['kind'],a['description'],dumps(a['aliases']),dumps(a['review_questions']),dumps(a['sources']),int(a['selectable']),int(a['active']),1,now(),dumps(a.get('hints',{})),category_for(a['kind'],a.get('category_id'))))
            elif stored['seeded'] and a['code'] in old_accounts:
                old=old_accounts[a['code']]
                for key,column in columns.items():
                    current=json.loads(stored[column]) if column.endswith('_json') else stored[column]
                    default = '' if key == 'category_id' else {}
                    if current==old.get(key,default) and current!=a.get(key,default):
                        value=dumps(a.get(key,default)) if column.endswith('_json') else a[key]
                        db.execute(f'UPDATE account_master SET {column}=?,updated_at=? WHERE code=?',(value,now(),a['code']))
        old_policies={p['code']:p for p in previous['policies']}
        for p in seed['policies']:
            stored=db.execute('SELECT * FROM account_policies WHERE code=?',(p['code'],)).fetchone()
            if stored is None:
                db.execute('INSERT INTO account_policies VALUES(?,?,?,?,?,?,?,?,1)',(p['code'],p['title'],p['priority'],p['action'],dumps(p['match']),p['message'],dumps(p['questions']),dumps(p['sources'])))
            elif p['code'] in old_policies:
                old=old_policies[p['code']]
                for key,column in {'title':'title','priority':'priority','action':'action','match':'match_json','message':'message','questions':'questions_json','sources':'sources_json'}.items():
                    current=json.loads(stored[column]) if column.endswith('_json') else stored[column]
                    if current==old[key] and current!=p[key]:
                        db.execute(f'UPDATE account_policies SET {column}=? WHERE code=?',(dumps(p[key]) if column.endswith('_json') else p[key],p['code']))
        db.execute("UPDATE catalog_meta SET value=? WHERE key='seed_version'",(seed['version'],))
        self.audit(db,'catalog','seed_update',{'from':version,'to':seed['version'],'preserved_manual_edits':True})
        self._refresh_pending_policies(db, seed['version'])

    def _refresh_pending_policies(self, db, seed_version):
        """Refresh AI guidance after a seed upgrade; keep owner input and posted journals."""
        from .evidence import evidence_text
        accounts, policies = self.accounts(True, db), self.policies(db)
        rows = db.execute("SELECT id,suggestion_json,ocr_json FROM receipts WHERE status='review' AND suggestion_json IS NOT NULL AND ocr_json IS NOT NULL")
        for row in rows:
            original = json.loads(row['suggestion_json'])
            checked = apply_account_policies(original, evidence_text(json.loads(row['ocr_json'])), accounts, policies)
            known = {p['code'] for p in original.get('policy_matches', [])}
            added = [p for p in checked['policy_matches'] if p['code'] not in known]
            if not added:
                continue
            if checked['blocking_reasons']:
                checked['decision'] = 'block'
            elif not checked['account']:
                checked['decision'] = 'hold'
            checked['policy_catalog_version'] = seed_version
            db.execute("UPDATE receipts SET suggestion_json=?,version=version+1,updated_at=? WHERE id=?",
                       (dumps(checked), now(), row['id']))
            self.audit(db, row['id'], 'policy_refresh', {'catalog_version': seed_version,
                       'added_conditions': [p['code'] for p in added], 'before': original,
                       'after': checked, 'owner_input_preserved': True})

    def accounts(self, selectable=True, db=None):
        if db is None:
            with self.connect() as conn:
                return self.accounts(selectable,conn)
        query="SELECT * FROM account_master WHERE active=1"+(" AND selectable=1 AND kind IN ('expense','asset')" if selectable else "")+" ORDER BY code"
        result=[]
        for row in db.execute(query):
            data=dict(row)
            for source,target in [("aliases_json","aliases"),("questions_json","review_questions"),("sources_json","sources")]:
                data[target]=json.loads(data.pop(source))
            data["example"]=data["description"]
            data["asset"]=data["kind"]=="asset"
            data["hints"]=json.loads(data.pop("hints_json"))
            # Unknown metadata from a direct DB edit must not hide a usable account.
            try:
                data['category_id'] = category_for(data['kind'], data['category_id'])
            except ValueError:
                data['category_id'] = category_for(data['kind'])
            result.append(data)
        return result

    def policies(self, db=None):
        if db is None:
            with self.connect() as conn:
                return self.policies(conn)
        result=[]
        for row in db.execute("SELECT * FROM account_policies WHERE active=1 ORDER BY priority,code"):
            data=dict(row)
            for source,target in [("match_json","match"),("questions_json","questions"),("sources_json","sources")]:
                data[target]=json.loads(data.pop(source))
            result.append(data)
        return result

    def favorite_accounts(self, db=None):
        """Return usable favorite codes; names and aliases never define identity."""
        if db is None:
            with self.connect() as conn:
                return self.favorite_accounts(conn)
        row = db.execute("SELECT value FROM settings WHERE key='favorite_accounts'").fetchone()
        try:
            saved = json.loads(row['value']) if row else []
        except (TypeError, ValueError):
            return []
        if not isinstance(saved, list):
            return []
        valid = {row['code'] for row in db.execute(
            "SELECT code FROM account_master WHERE active=1 AND selectable=1 AND kind IN ('expense','asset')")}
        return sorted({code for code in saved if isinstance(code, str) and code in valid})

    def set_account_favorite(self, code, favorite):
        """Change one favorite atomically without overwriting another window's list."""
        if not isinstance(code, str) or type(favorite) is not bool:
            raise ValueError('科目コードと、お気に入りの指定を確認してください。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            account = db.execute("SELECT code FROM account_master WHERE code=? AND active=1 AND selectable=1 AND kind IN ('expense','asset')", (code,)).fetchone()
            if account is None:
                raise ValueError('選択できる勘定科目を指定してください。')
            favorites = set(self.favorite_accounts(db))
            if favorite:
                favorites.add(code)
            else:
                favorites.discard(code)
            result = sorted(favorites)
            db.execute("INSERT INTO settings VALUES('favorite_accounts',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (dumps(result),))
            return result

    def catalog(self):
        with self.connect() as db:
            return {"schema_version":db.execute("PRAGMA user_version").fetchone()[0],
                    "seed_version":db.execute("SELECT value FROM catalog_meta WHERE key='seed_version'").fetchone()[0],
                    "accounts":self.accounts(False,db),"account_categories":account_categories(),"policies":self.policies(db),
                    "sources":[dict(r) for r in db.execute("SELECT * FROM account_sources ORDER BY id")]}

    def add_account(self, name, kind, description, aliases, questions, keywords=None, category_id=None):
        name=name.strip()
        if kind not in {"expense","asset"} or not name:
            raise ValueError("経費または資産の科目名を入力してください")
        category_id = category_for(kind, category_id)
        aliases=list(dict.fromkeys(a.strip() for a in aliases if a.strip()))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing=self.accounts(False,db)
            for value in [name,*aliases]:
                if resolve_account(value,existing):
                    raise ValueError("同じ名前・別名の科目が既にあります："+value)
            code="U"+uuid.uuid4().hex[:10].upper()
            db.execute("INSERT INTO account_master(code,name,name_key,kind,description,aliases_json,questions_json,sources_json,selectable,active,seeded,updated_at,hints_json,category_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (code,name,account_key(name),kind,description.strip(),dumps(aliases),dumps(questions),dumps([]),1,1,0,now(),dumps({"any":keywords or [],"unless_any":[]}),category_id))
            self.audit(db,"catalog","account_add",{"code":code,"name":name,"kind":kind,"category_id":category_id})
            return next(a for a in self.accounts(False,db) if a["code"]==code)

    def _void_posting(self, db, receipt_id):
        db.execute("UPDATE journal_entries SET status='void',voided_at=? WHERE receipt_id=? AND status='posted'",(now(),receipt_id))

    def _post(self, db, row):
        from .accounting import journal
        pairs=journal(row["draft"])
        lines={}
        for pair in pairs:
            for side,key in [("debit","debit"),("credit","credit")]:
                account=db.execute("SELECT code FROM account_master WHERE name=?",(pair[key],)).fetchone()
                if account is None:
                    raise ValueError("仕訳の科目がマスターにありません："+pair[key])
                marker=(side,account["code"])
                lines[marker]=lines.get(marker,0)+pair["amount"]
        debit=sum(v for (s,_),v in lines.items() if s=="debit")
        credit=sum(v for (s,_),v in lines.items() if s=="credit")
        if not lines or debit!=credit:
            raise ValueError("貸借が一致しないため登録できません")
        self._void_posting(db,row["id"])
        cursor=db.execute("INSERT INTO journal_entries(receipt_id,receipt_version,status,created_at) VALUES(?,?,'posted',?)",(row["id"],row["version"],now()))
        for number,((side,code),amount) in enumerate(lines.items(),1):
            db.execute("INSERT INTO journal_lines VALUES(?,?,?,?,?)",(cursor.lastrowid,number,code,side,amount))

    def decode(self, row, db=None):
        data = dict(row)
        for key in ["draft", "ocr", "suggestion"]:
            raw = data.pop(key + "_json")
            data[key] = json.loads(raw) if raw else None
        if db is not None:
            posting=db.execute("SELECT id,receipt_version FROM journal_entries WHERE receipt_id=? AND status='posted'",(data["id"],)).fetchone()
            data["posting"]=None
            if posting:
                data["posting"]={**dict(posting),"lines":[dict(r) for r in db.execute("SELECT l.account_code,a.name,l.side,l.amount FROM journal_lines l JOIN account_master a ON a.code=l.account_code WHERE entry_id=? ORDER BY line_no",(posting["id"],))]}
        return data

    def get(self, receipt_id, db=None):
        if db is None:
            with self.connect() as conn:
                return self.get(receipt_id, conn)
        row = db.execute("SELECT r.*,a.code AS account_code,a.kind AS account_kind FROM receipts r LEFT JOIN account_master a ON a.name=json_extract(r.draft_json,'$.account') WHERE r.id=?", (receipt_id,)).fetchone()
        if row is None:
            raise Missing(receipt_id)
        return self.decode(row,db)

    def list(self):
        with self.connect() as db:
            return [self.decode(r,db) for r in db.execute("SELECT r.*,a.code AS account_code,a.kind AS account_kind FROM receipts r LEFT JOIN account_master a ON a.name=json_extract(r.draft_json,'$.account') ORDER BY r.created_at DESC,r.id").fetchall()]

    def storage_issues(self):
        with self.connect() as db:
            missing=sum(not (self.root/"receipts"/r["id"]/("original"+r["extension"])).is_file() for r in db.execute("SELECT id,extension FROM receipts"))
            source_missing=0
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_import_sources'").fetchone():
                source_missing=sum(not (self.root/"imports"/r[0]/"source.pdf").is_file() for r in db.execute("SELECT DISTINCT source_sha256 FROM receipt_import_sources"))
        return {"missing_originals":missing,"missing_source_pdfs":source_missing}

    def folder(self, receipt_id):
        # IDs are always generated locally and looked up before file access.
        self.get(receipt_id)
        return self.root / "receipts" / receipt_id

    @staticmethod
    def audit(db, receipt_id, action, detail):
        db.execute("INSERT INTO audit(receipt_id,at,action,detail_json) VALUES(?,?,?,?)", (receipt_id, now(), action, dumps(detail)))

    def add(self, name: str, extension: str, content: bytes):
        if extension not in {".pdf", ".jpg", ".png", ".webp"}:
            raise ValueError("対応していない原本の形式です。")
        digest = hashlib.sha256(content).hexdigest()
        with self.connect() as db:
            existing = db.execute("SELECT id FROM receipts WHERE sha256=?", (digest,)).fetchone()
            if existing:
                current=self.get(existing["id"],db)
                original=self.root/"receipts"/current["id"]/("original"+current["extension"])
                try:
                    intact=hashlib.sha256(self.keys.read_file(original)).hexdigest()==digest
                except (ProtectionError,OSError):
                    intact=False
                if not intact:
                    self.keys.write_file(original,content)
                    self.audit(db,current["id"],"repair_original",{"sha256":digest,"same_bytes_reimported":True})
                return current, True
            receipt_id = uuid.uuid4().hex
            folder = self.root / "receipts" / receipt_id
            folder.mkdir()
            self.keys.write_file(folder / ("original" + extension), content)
            stamp = now()
            db.execute("""INSERT INTO receipts(id,sha256,original_name,extension,created_at,updated_at,status,draft_json)
                         VALUES(?,?,?,?,?,?,'queued',?)""", (receipt_id, digest, name, extension, stamp, stamp, dumps(Draft().model_dump())))
            self.audit(db, receipt_id, "import", {"original_name": name, "sha256": digest})
            return self.get(receipt_id, db), False

    def stage(self, receipt_id, stage):
        with self.connect() as db:
            db.execute("UPDATE receipts SET status='processing',stage=?,updated_at=? WHERE id=?", (stage, now(), receipt_id))

    def complete(self, receipt_id, ocr, suggestion, error=""):
        with self.connect() as db:
            current = self.get(receipt_id, db)
            self._void_posting(db,receipt_id)
            draft = current["draft"]
            if draft == Draft().model_dump() and suggestion:
                # Payment, business use, and purpose always need the owner's input.
                draft = Draft(**{k: suggestion[k] for k in Draft.model_fields if k in suggestion}).model_dump()
            db.execute("""UPDATE receipts SET status=?,stage='',draft_json=?,ocr_json=?,suggestion_json=?,error=?,
                          version=version+1,updated_at=? WHERE id=?""",
                       ("review" if ocr else "error", dumps(draft), dumps(ocr) if ocr else None,
                        dumps(suggestion) if suggestion else None, error, now(), receipt_id))
            self.audit(db, receipt_id, "analysis", {"model": suggestion.get("model") if suggestion else None, "error": error, "ocr":ocr, "suggestion":suggestion})

    def save(self, receipt_id, version, draft: Draft, confirm=False, remember=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.get(receipt_id, db)
            self.check(current, version)
            if current["status"] in ["queued", "processing"]:
                raise Conflict("読み取り中です。完了を待ってください")
            account=resolve_account(draft.account,self.accounts(True,db)) if draft.account else None
            if draft.account and account is None:
                raise ValueError("勘定科目マスターにある科目を選んでください")
            if account:
                draft=draft.model_copy(update={"account":account["name"]})
            if confirm:
                from .schemas import confirmation_errors
                errors=confirmation_errors(draft)
                from .evidence import evidence_text
                checked=apply_account_policies({**draft.model_dump(),"warnings":[]},evidence_text(current["ocr"]),self.accounts(True,db),self.policies(db))
                if errors or checked["blocking_reasons"]:
                    raise ValueError("\n".join(errors+checked["blocking_reasons"]))
            status = "confirmed" if confirm else "review"
            db.execute("""UPDATE receipts SET draft_json=?,status=?,version=version+1,updated_at=? WHERE id=?""",
                       (dumps(draft.model_dump()), status, now(), receipt_id))
            if remember and confirm:
                db.execute("INSERT INTO merchant_rules(merchant,account,updated_at,item_hint,account_code) VALUES(?,?,?,?,?) ON CONFLICT(merchant) DO UPDATE SET account=excluded.account,updated_at=excluded.updated_at,item_hint=excluded.item_hint,account_code=excluded.account_code", (draft.merchant.strip(),draft.account,now(),draft.description.strip(),account["code"]))
            self.audit(db, receipt_id, "confirm" if confirm else "save", {"before": current["draft"], "after": draft.model_dump(), "previous_status": current["status"], "remember_account": bool(remember and confirm)})
            if confirm:
                self._post(db,self.get(receipt_id,db))
            else:
                self._void_posting(db,receipt_id)
            return self.get(receipt_id, db)

    @staticmethod
    def check(current, version):
        if current["version"] != version:
            raise Conflict("別の操作で更新されました。画面を再読込してから入力し直してください")

    def set_status(self, receipt_id, version, action):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.get(receipt_id, db)
            self.check(current, version)
            if current["status"] in ["queued", "processing"]:
                raise Conflict("読み取り中です。完了を待ってください")
            if action == "restore" and current["status"] != "excluded":
                raise Conflict("対象外の明細だけ確認待ちへ戻せます。確認済みを修正する場合は、変更を保存してください")
            if action == "retry":
                if current["status"] == "confirmed":
                    raise Conflict("登録済みです。先に確認待ちへ戻してください")
                status = "queued"
            elif action == "exclude":
                status = "excluded"
            else:
                status = "review"
            db.execute("UPDATE receipts SET status=?,stage='',version=version+1,updated_at=? WHERE id=?", (status, now(), receipt_id))
            self._void_posting(db,receipt_id)
            self.audit(db, receipt_id, action, {"previous_status": current["status"]})
            return self.get(receipt_id, db)

    def recover(self):
        with self.connect() as db:
            db.execute("""UPDATE receipts SET status='error',stage='',error='前回の処理が中断されました。再読取できます。',
                          version=version+1 WHERE status IN ('queued','processing')""")

    def history(self, receipt_id):
        self.get(receipt_id)
        with self.connect() as db:
            return [dict(r, detail=json.loads(r["detail_json"])) for r in db.execute("SELECT seq,at,action,detail_json FROM audit WHERE receipt_id=? ORDER BY seq DESC", (receipt_id,))]

    def rules(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM merchant_rules ORDER BY merchant")]

    def remove_rule(self, merchant):
        with self.connect() as db:
            db.execute("DELETE FROM merchant_rules WHERE merchant=?", (merchant,))

    def business_context(self, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute("INSERT INTO settings VALUES('business_context',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
            row = db.execute("SELECT value FROM settings WHERE key='business_context'").fetchone()
            return row[0] if row else ""

    def export_map(self, profile, value=None):
        if profile not in {'freee','mf','yayoi'}:return {}
        with self.connect() as db:
            if value is not None:
                known={a['name'] for a in self.accounts(False,db)}
                if any(k not in known or not v.strip() or len(v)>100 or any(c in v for c in '\r\n\t') for k,v in value.items()):
                    raise ValueError('登録済み科目と、100文字以内の出力名を指定してください')
                db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('export_map_'+profile,dumps(value)))
            row=db.execute('SELECT value FROM settings WHERE key=?',('export_map_'+profile,)).fetchone()
            return json.loads(row[0]) if row else {}
