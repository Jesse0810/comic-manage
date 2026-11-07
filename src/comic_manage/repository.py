"""SQLite catalog. Each operation owns its connection, safe for worker threads."""

from __future__ import annotations

from contextlib import contextmanager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .metadata import Metadata, title_key
from .sources import Snapshot, source_id


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Comic:
    id: str
    source: str
    kind: str
    fingerprint: str
    page_count: int
    metadata: Metadata
    status: str = "draft"
    output: str = ""
    warning: str = ""
    updated_at: str = ""


class Repository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise ValueError("数据库版本比本程序新，请使用较新版本打开。")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS comics (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    page_count INTEGER NOT NULL, metadata TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft', output TEXT NOT NULL DEFAULT '',
                    warning TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, comic_id TEXT NOT NULL,
                    action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS removed_comics (
                    comic_id TEXT PRIMARY KEY, removed_at TEXT NOT NULL
                );
                PRAGMA user_version = 1;
            """)
        self._migrate_metadata()

    def _migrate_metadata(self) -> None:
        # Back up before rewriting any older manual metadata. New records always contain title.
        with self.connect() as db:
            legacy = [(row["id"], json.loads(row["metadata"]), row["warning"])
                      for row in db.execute("SELECT id, metadata, warning FROM comics")
                      if "title" not in json.loads(row["metadata"])]
        if not legacy:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = self.path.with_name(f"{self.path.stem}.before-metadata-v1_1-{stamp}.sqlite3")
        self.backup(backup)
        with self.connect() as db:
            for comic_id, previous, warning in legacy:
                metadata = Metadata.from_dict(previous)
                note = "元数据规范已升级，原资料已自动备份，请核对作品与系列、年龄分级、译者后重新审核。"
                if previous.get("age_rating", "Unknown") not in ("Unknown", "Everyone", "MA15+", "R18+", "Adults Only 18+", "全年龄", "R15", "R18"):
                    note += f" 原分级 {previous['age_rating']} 无法准确对应，已留待确认。"
                db.execute("UPDATE comics SET metadata=?, status='draft', warning=?, updated_at=? WHERE id=?", (
                    json.dumps(metadata.to_dict(), ensure_ascii=False), "；".join(filter(None, [warning, note])), now(), comic_id))
                self._event(db, comic_id, "metadata_rules_migration", json.dumps({"backup": str(backup), "previous": previous}, ensure_ascii=False))
            db.execute("INSERT INTO settings VALUES ('metadata_rules', '1.1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _decode(row) -> Comic:
        value = dict(row)
        value["metadata"] = Metadata.from_dict(json.loads(value["metadata"]))
        return Comic(**value)

    def all(self, *, include_removed: bool = False) -> list[Comic]:
        with self.connect() as db:
            condition = "" if include_removed else " WHERE id NOT IN (SELECT comic_id FROM removed_comics)"
            return [self._decode(row) for row in db.execute(
                "SELECT * FROM comics" + condition + " ORDER BY updated_at DESC, id")]

    def get(self, comic_id: str, *, include_removed: bool = False) -> Comic:
        with self.connect() as db:
            condition = "" if include_removed else " AND id NOT IN (SELECT comic_id FROM removed_comics)"
            row = db.execute("SELECT * FROM comics WHERE id=?" + condition, (comic_id,)).fetchone()
        if not row:
            raise ValueError("作品记录不存在。")
        return self._decode(row)

    def is_removed(self, comic_id: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM removed_comics WHERE comic_id=?", (comic_id,)).fetchone() is not None

    def upsert_scan(self, snapshot: Snapshot, *, restore: bool = False,
                    defaults: dict | None = None, overwrite_existing: bool = False) -> str | None:
        comic_id = source_id(snapshot.path)
        metadata, warning = snapshot.initial_metadata()
        defaults = defaults or {}
        with self.connect() as db:
            removed = db.execute("SELECT 1 FROM removed_comics WHERE comic_id=?", (comic_id,)).fetchone()
            if removed and not restore:
                return None
            previous = db.execute("SELECT * FROM comics WHERE id=?", (comic_id,)).fetchone()
            if previous:
                if previous["fingerprint"] != snapshot.fingerprint:
                    db.execute("""UPDATE comics SET fingerprint=?, page_count=?, status='draft',
                        warning=?, updated_at=? WHERE id=?""", (
                        snapshot.fingerprint, len(snapshot.pages),
                        "源文件已变化，请重新预览并审核。" + warning, now(), comic_id))
                    self._event(db, comic_id, "source_changed", snapshot.fingerprint)
                if removed:
                    db.execute("DELETE FROM removed_comics WHERE comic_id=?", (comic_id,))
                    db.execute("UPDATE comics SET status='draft', updated_at=? WHERE id=?", (now(), comic_id))
                    self._event(db, comic_id, "restore", str(snapshot.path))
                if defaults and overwrite_existing:
                    metadata = Metadata.from_dict(json.loads(previous["metadata"]))
                    for key, value in defaults.items():
                        setattr(metadata, key, value)
                    db.execute("UPDATE comics SET metadata=?, status='draft', updated_at=? WHERE id=?", (
                        json.dumps(metadata.to_dict(), ensure_ascii=False), now(), comic_id))
                    self._event(db, comic_id, "batch_edit", "导入时批量更新作者或类型")
                return comic_id
            for key, value in defaults.items():
                setattr(metadata, key, value)
            db.execute("""INSERT INTO comics
                (id, source, kind, fingerprint, page_count, metadata, warning, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
                comic_id, str(snapshot.path), snapshot.kind, snapshot.fingerprint,
                len(snapshot.pages), json.dumps(metadata.to_dict(), ensure_ascii=False), warning, now()))
            self._event(db, comic_id, "import", str(snapshot.path))
        return comic_id

    def remove(self, comic_ids: list[str]) -> None:
        """Hide catalog entries only. Source images and exported files are untouched."""
        with self.connect() as db:
            self._require_visible(db, comic_ids)
            for comic_id in comic_ids:
                db.execute("INSERT INTO removed_comics (comic_id, removed_at) VALUES (?, ?)", (comic_id, now()))
                self._event(db, comic_id, "remove", "从漫画库移除，保留源文件、成品和人工资料")

    @staticmethod
    def _require_visible(db, comic_ids: list[str]):
        for comic_id in comic_ids:
            row = db.execute("SELECT 1 FROM comics WHERE id=? AND id NOT IN "
                             "(SELECT comic_id FROM removed_comics)", (comic_id,)).fetchone()
            if row is None:
                raise ValueError("作品记录不存在或已移除。")

    def batch_edit(self, comic_ids: list[str], changes: dict) -> int:
        count = 0
        fields = set(changes)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_visible(db, comic_ids)
            for comic_id in comic_ids:
                row = db.execute("SELECT metadata FROM comics WHERE id=?", (comic_id,)).fetchone()
                stored = json.loads(row["metadata"])
                before = Metadata.from_dict(stored).to_dict()
                metadata = Metadata.from_dict({**before, **changes})
                actual = {key: value for key, value in metadata.to_dict().items() if before[key] != value}
                try:
                    # Canonicalization is allowed for checked fields only.
                    unselected = actual.keys() - fields
                    if "pencillers" in unselected:
                        raise ValueError("作者和已有绘画者重名，请同时勾选并调整独立绘画者。")
                    if "localized_series" in unselected:
                        raise ValueError("新系列名和已有本地化系列名相同，请同时勾选并调整本地化系列名。")
                    if unselected:
                        raise ValueError("本次填写会影响未勾选的字段，请检查批量编辑内容。")
                    if changes.get("uncensored") and metadata.age_rating != "R18+":
                        raise ValueError("只有 R18 作品可以设置为无修正。")
                    metadata.validate_fields(fields)
                except ValueError as error:
                    raise ValueError(f"《{before['title']}》{error}") from error
                if not actual:
                    continue
                db.execute("UPDATE comics SET metadata=?, status='draft', updated_at=? WHERE id=?", (
                    json.dumps({**stored, **actual}, ensure_ascii=False), now(), comic_id))
                self._event(db, comic_id, "batch_edit", f"批量修改 {', '.join(actual)}。需要重新审核。")
                count += 1
        return count

    def save(self, comic_id: str, metadata: Metadata) -> None:
        # Drafts may be incomplete. Every edit invalidates previous approval/export status.
        with self.connect() as db:
            db.execute("UPDATE comics SET metadata=?, status='draft', updated_at=? WHERE id=?", (
                json.dumps(metadata.to_dict(), ensure_ascii=False), now(), comic_id))
            self._event(db, comic_id, "edit", "人工修改元数据")

    def conflicts(self, comic_id: str, metadata: Metadata) -> list[Comic]:
        if metadata.series.strip():
            # An explicit series is a deliberate grouping choice. Writers may change between volumes.
            return []
        titles = {title_key(t) for t in (metadata.series or metadata.display_title, metadata.localized_series) if t.strip()}
        authors = {title_key(a) for a in metadata.authors}
        return [c for c in self.all() if c.id != comic_id and c.status in ("approved", "packaged")
                and authors != {title_key(a) for a in c.metadata.authors}
                and titles.intersection(title_key(t) for t in (c.metadata.series or c.metadata.display_title, c.metadata.localized_series) if t.strip())]

    def approve(self, comic_id: str, snapshot: Snapshot) -> None:
        comic = self.get(comic_id)
        if str(snapshot.path) != comic.source or snapshot.fingerprint != comic.fingerprint:
            raise ValueError("源文件已变化，请重新扫描、检查并审核。")
        comic.metadata.validate(len(snapshot.pages))
        conflicts = self.conflicts(comic_id, comic.metadata)
        if conflicts:
            raise ValueError("发现其他作者的同名作品，Kavita 可能错误合并。请调整作品标题或分开管理书库：\n"
                             + "\n".join(c.source for c in conflicts))
        with self.connect() as db:
            db.execute("UPDATE comics SET status='approved', warning='', updated_at=? WHERE id=?", (now(), comic_id))
            self._event(db, comic_id, "approve", snapshot.fingerprint)

    def mark_packaged(self, comic_id: str, output: Path, report: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE comics SET status='packaged', output=?, updated_at=? WHERE id=?", (str(output), now(), comic_id))
            self._event(db, comic_id, "package_verified", json.dumps(report, ensure_ascii=False))

    @staticmethod
    def _event(db, comic_id: str, action: str, detail: str):
        db.execute("INSERT INTO events (comic_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
                   (comic_id, action, detail, now()))

    def setting(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def backup(self, destination: Path):
        # SQLite backup API gives a consistent catalog even while other connections exist.
        if destination.exists():
            raise FileExistsError("备份文件已存在，请选择新文件名。")
        with destination.open("xb"):
            pass
        try:
            with self.connect() as db, closing(sqlite3.connect(destination)) as target:
                db.backup(target)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
