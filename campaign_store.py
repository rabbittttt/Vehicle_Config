"""SQLite-backed campaign drafts and immutable published editions."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

from catalog import Conflict, now
from stage_workbook import (
    apply_edits, empty_workbook, export_workbook, import_workbook, validate_publication,
)


def encode(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _data_rows(sheet):
    headers = set(sheet['headerRows'])
    return (row for i, row in enumerate(sheet['rows'], 1)
            if i not in headers and any(v is not None and v != '' for v in row))


def _stages(summary):
    return {(str(row[0]), str(row[1] or '') if len(row) > 1 else '')
            for row in _data_rows(summary) if row and row[0]}


def _stage_list(keys):
    return [{'generation': name, 'subgeneration': subgroup} for name, subgroup in sorted(keys)]


def import_comparison(current, incoming):
    before = {s['name']: s for s in current['sheets']}
    after = {s['name']: s for s in incoming['sheets']}
    tables = [{'name': name, 'before': sum(1 for _ in _data_rows(before[name])),
               'after': sum(1 for _ in _data_rows(sheet)), 'changed': before[name] != sheet}
              for name, sheet in after.items()]
    previous = _stages(before['车型汇总'])
    imported = _stages(after['车型汇总'])
    return {'tables': tables, 'addedStages': _stage_list(imported - previous),
            'removedStages': _stage_list(previous - imported)}


class CampaignStore:
    def __init__(self, root):
        self.root = Path(root) / 'campaigns'
        self.root.mkdir(exist_ok=True, parents=True)
        self.path = self.root / 'campaigns.sqlite3'
        self.snapshot = self.root / 'published.json'
        with closing(self.connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS publications (
                    revision INTEGER PRIMARY KEY, data TEXT NOT NULL,
                    actor TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS draft (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    revision INTEGER NOT NULL, published_revision INTEGER NOT NULL,
                    projected_revision INTEGER NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity (
                    revision INTEGER PRIMARY KEY, action TEXT NOT NULL,
                    actor TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS import_backup (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    import_revision INTEGER NOT NULL, previous_data TEXT NOT NULL
                );
            """)
            with db:
                initial = encode(empty_workbook())
                db.execute("INSERT OR IGNORE INTO publications VALUES (0, ?, '', '')", (initial,))
                db.execute("INSERT OR IGNORE INTO draft VALUES (1, 0, 0, 0, ?)", (initial,))
        self._project(check_contents=True)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _write(self):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    @staticmethod
    def _current(db):
        return db.execute("""
            SELECT d.*, p.data AS published_data, b.import_revision AS undo_revision FROM draft d
            JOIN publications p ON p.revision = d.published_revision
            LEFT JOIN import_backup b ON b.id = 1 AND b.import_revision = d.revision
            WHERE d.id = 1
        """).fetchone()

    @staticmethod
    def _edition(revision, data):
        return {**data, 'schemaVersion': 1, 'revision': revision}

    def _project(self, check_contents=False):
        """Serialize replacements with publication writes, always projecting the newest edition."""
        with self._write() as db:
            row = self._current(db)
            revision = row['published_revision']
            if not revision:
                return ''
            try:
                if not check_contents and row['projected_revision'] == revision and self.snapshot.is_file():
                    return ''
                data = self._edition(revision, json.loads(row['published_data']))
                payload = encode(data).encode('utf-8')
                if not (check_contents and self.snapshot.is_file()
                        and self.snapshot.read_bytes() == payload):
                    self._replace_snapshot(payload)
                db.execute('UPDATE draft SET projected_revision = ? WHERE id = 1', (revision,))
            except OSError:
                db.execute('UPDATE draft SET projected_revision = -1 WHERE id = 1')
                return '发布版本已保存，报表数据文件暂未更新。请检查目录写入权限或文件占用后重新载入。'
        return ''

    def _replace_snapshot(self, data):
        temporary = None
        try:
            with NamedTemporaryFile(dir=self.root, prefix='.publish-', suffix='.tmp', delete=False) as f:
                temporary = Path(f.name)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.snapshot)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _response(row, data=None, error=''):
        return {
            **(data if data is not None else json.loads(row['data'])),
            'available': True,
            'revision': row['revision'],
            'publishedRevision': row['published_revision'],
            'hasUnpublishedChanges': row['data'] != row['published_data'],
            'canImportLegacy': True,
            'canUndoImport': row['undo_revision'] is not None,
            'projectionPending': bool(error),
            'projectionError': error,
            'edits': [],
        }

    def get(self):
        with closing(self.connect()) as db:
            row = self._current(db)
        if row['published_revision'] and (
                row['projected_revision'] != row['published_revision'] or not self.snapshot.is_file()):
            error = self._project()
            with closing(self.connect()) as db:
                row = self._current(db)
            return self._response(row, error=error)
        return self._response(row)

    def published(self, revision=None):
        with closing(self.connect()) as db:
            if revision is None:
                row = self._current(db)
                return self._edition(row['published_revision'], json.loads(row['published_data']))
            row = db.execute('SELECT data FROM publications WHERE revision = ?', (revision,)).fetchone()
            if row is None:
                raise ValueError('找不到指定的小订与首销发布版本')
            return self._edition(revision, json.loads(row['data']))

    @staticmethod
    def _check(row, revision):
        if type(revision) is not int or revision < 0:
            raise ValueError('缺少有效的小订与首销草稿版本')
        if row['revision'] != revision:
            raise Conflict('小订与首销草稿已被其他人修改，请重新载入后核对')

    def preview(self, revision, edits):
        with closing(self.connect()) as db:
            row = self._current(db)
        self._check(row, revision)
        data = apply_edits(json.loads(row['data']), edits)
        result = self._response(row, data)
        result['hasUnpublishedChanges'] = encode(data) != row['published_data']
        return result

    @staticmethod
    def _update(db, row, data, action, actor):
        revision = row['revision'] + 1
        edition = row['published_revision']
        timestamp = now()
        db.execute('DELETE FROM import_backup')
        if action == '发布':
            edition += 1
            db.execute('INSERT INTO publications VALUES (?, ?, ?, ?)', (edition, data, actor, timestamp))
        db.execute('UPDATE draft SET revision=?, published_revision=?, data=? WHERE id=1',
                   (revision, edition, data))
        db.execute('INSERT INTO activity VALUES (?, ?, ?, ?)', (revision, action, actor, timestamp))

    def save(self, revision, edits, actor, publish=False):
        with self._write() as db:
            row = self._current(db)
            self._check(row, revision)
            data = apply_edits(json.loads(row['data']), edits)
            payload = encode(data)
            if publish:
                validate_publication(data)
                if payload == row['published_data']:
                    raise ValueError('没有需要发布的小订与首销变更')
            if publish or payload != row['data']:
                self._update(db, row, payload, '发布' if publish else '草稿', actor)
        return self.get()

    def preview_import(self, revision, path):
        with closing(self.connect()) as db:
            row = self._current(db)
        self._check(row, revision)
        current = json.loads(row['data'])
        incoming = import_workbook(path)
        changes = import_comparison(current, incoming)
        identical = current == incoming
        return {'revision': revision, 'identical': identical,
                'requiresConfirmation': not identical and current != empty_workbook(),
                'changes': changes}

    def import_legacy(self, revision, path, actor, confirm_replace=False):
        with closing(self.connect()) as db:
            self._check(self._current(db), revision)
        incoming = import_workbook(path)
        with self._write() as db:
            row = self._current(db)
            self._check(row, revision)
            current = json.loads(row['data'])
            if current != incoming:
                if current != empty_workbook() and confirm_replace is not True:
                    raise ValueError('请预览差异并确认替换现有草稿')
                self._update(db, row, encode(incoming), '迁入草稿', actor)
                db.execute('INSERT INTO import_backup VALUES (1, ?, ?)', (revision + 1, row['data']))
        return self.get()

    def undo_import(self, revision, actor):
        with self._write() as db:
            row = self._current(db)
            self._check(row, revision)
            backup = db.execute('SELECT previous_data FROM import_backup WHERE import_revision=?',
                                (revision,)).fetchone()
            if backup is None:
                raise ValueError('当前草稿没有可撤回的导入')
            self._update(db, row, backup['previous_data'], '撤回导入', actor)
        return self.get()

    def export(self, selection, source='published'):
        with closing(self.connect()) as db:
            row = self._current(db)
        if source == 'published' and not row['published_revision']:
            raise ValueError('小订与首销尚未发布')
        if source not in ('draft', 'published'):
            raise ValueError('未知的导出来源')
        data = row['data'] if source == 'draft' else row['published_data']
        return export_workbook(json.loads(data), selection)
