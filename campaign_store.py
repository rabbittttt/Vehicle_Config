"""SQLite-backed campaign drafts and immutable published editions."""
from contextlib import closing, contextmanager
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

from catalog import Conflict, now, read_history_page
from stage_workbook import (
    SECONDARY, SUMMARY_FIELDS, apply_edits, backfill_totals, empty_workbook, export_workbook, import_workbook, validate_publication,
)


def encode(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def decode_workbook(payload):
    data = json.loads(payload)
    header = data['sheets'][0]['rows'][0]
    if header[5] == '小订发布时间':
        header[5] = SUMMARY_FIELDS[5]
    return data


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
    tables = [{'name': name, 'before': sum(1 for _ in _data_rows(before[name])) if name in before else 0,
               'after': sum(1 for _ in _data_rows(after[name])) if name in after else 0,
               'changed': before.get(name) != after.get(name)} for name in dict.fromkeys([*after,*before])]
    previous = _stages(before['车型汇总'])
    imported = _stages(after['车型汇总'])
    return {'tables': tables, 'addedStages': _stage_list(imported - previous),
            'removedStages': _stage_list(previous - imported)}


def _publication_rows(sheet):
    """Match spreadsheet rows by block and stage, including duplicate historical entries."""
    indexed = {}
    if sheet is None:
        return indexed
    marks = defaultdict(dict)
    for tag in ('reportedTotals', 'literalCells', 'derivedCells'):
        for address in sheet.get(tag, []):
            row, col = map(int, address.split(':'))
            marks[row].setdefault(col-1, []).append(tag)
    starts = sheet['headerRows']
    for block, start in enumerate(starts):
        head = sheet['rows'][start-1]
        secondary = head.index(SECONDARY)
        end = starts[block+1]-1 if block+1 < len(starts) else len(sheet['rows'])
        occurrences = Counter()
        for n in range(start, end):
            row = sheet['rows'][n]
            if not any(value is not None and value != '' for value in row):
                continue
            stage = (row[0] or '', (row[secondary] or '') if secondary < len(row) else '')
            indexed[block, stage, occurrences[stage]] = (head, row, marks.get(n+1, {}))
            occurrences[stage] += 1
    return indexed


def _publication_fields(record):
    if record is None:
        return {}
    headers, row, marks = record
    values = dict(zip(headers, row))
    for index, tags in marks.items():
        field = headers[index]
        values[field] = (values.get(field), tags)
    return values


def publication_changes(published, candidate):
    previous = {s['name']:s for s in published['sheets']}
    current = {s['name']:s for s in candidate['sheets']}
    changes = []
    for name in dict.fromkeys([*current, *previous]):
        old, new = previous.get(name), current.get(name)
        if old == new:
            continue
        before, after = _publication_rows(old), _publication_rows(new)
        stages, fields = {}, {}
        added = removed = updated = 0
        for key in dict.fromkeys([*after, *before]):
            left, right = before.get(key), after.get(key)
            if left == right:
                continue
            a, b = _publication_fields(left), _publication_fields(right)
            changed = [field for field in dict.fromkeys([*b, *a]) if field and a.get(field) != b.get(field)]
            if changed:
                if left is None:
                    added += 1
                elif right is None:
                    removed += 1
                else:
                    updated += 1
                stage = key[1]
                if stage[0] and stage[0] != '合计':
                    stages[stage] = None
                fields.update(dict.fromkeys(changed))
        changes.append({'name':name, 'added':added, 'removed':removed, 'updated':updated,
                        'stageCount':len(stages), 'stages':list(stages)[:4],
                        'fieldCount':len(fields), 'fields':list(fields)[:6]})
    return changes


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
                CREATE VIEW IF NOT EXISTS events AS
                    SELECT revision AS id, '发布' AS kind, actor, created_at AS time
                    FROM publications WHERE revision > 0;
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
        if data is None:
            data = backfill_totals(decode_workbook(row['data']))
        return {
            **data,
            'available': True,
            'revision': row['revision'],
            'publishedRevision': row['published_revision'],
            'hasUnpublishedChanges': data != decode_workbook(row['published_data']),
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

    def history_page(self, page=1, query=''):
        with closing(self.connect()) as db:
            return read_history_page(db, page, query)

    def published(self, revision=None):
        with closing(self.connect()) as db:
            if revision is None:
                row = self._current(db)
                return self._edition(row['published_revision'], json.loads(row['published_data']))
            row = db.execute('SELECT data FROM publications WHERE revision = ?', (revision,)).fetchone()
            if row is None:
                raise ValueError('找不到指定的车型日程与填报发布版本')
            return self._edition(revision, json.loads(row['data']))

    @staticmethod
    def _check(row, revision):
        if type(revision) is not int or revision < 0:
            raise ValueError('缺少有效的车型日程与填报草稿版本')
        if row['revision'] != revision:
            raise Conflict('车型日程与填报草稿已被其他人修改，请重新载入后核对')

    def preview(self, revision, edits):
        with closing(self.connect()) as db:
            row = self._current(db)
        self._check(row, revision)
        data = apply_edits(decode_workbook(row['data']), edits)
        return self._response(row, data)

    def publish_preview(self, revision, edits):
        with closing(self.connect()) as db:
            row = self._current(db)
        self._check(row, revision)
        data = apply_edits(decode_workbook(row['data']), edits)
        changes = publication_changes(decode_workbook(row['published_data']), data)
        error = ''
        if changes:
            try:
                validate_publication(data)
            except ValueError as exc:
                error = str(exc)
        return {'revision':revision, 'publishedRevision':row['published_revision'], 'changes':changes, 'error':error}

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
            draft = decode_workbook(row['data'])
            data = apply_edits(draft, edits)
            payload = encode(data)
            if publish:
                validate_publication(data)
                if data == decode_workbook(row['published_data']):
                    raise ValueError('没有需要发布的车型日程与填报变更')
            if publish or data != draft:
                self._update(db, row, payload, '发布' if publish else '草稿', actor)
        return self.get()

    def preview_import(self, revision, path):
        with closing(self.connect()) as db:
            row = self._current(db)
        self._check(row, revision)
        current = backfill_totals(decode_workbook(row['data']))
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
            current = backfill_totals(decode_workbook(row['data']))
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
            raise ValueError('车型日程与填报数据尚未发布')
        if source not in ('draft', 'published'):
            raise ValueError('未知的导出来源')
        data = backfill_totals(decode_workbook(row['data'])) if source == 'draft' else decode_workbook(row['published_data'])
        return export_workbook(data, selection)
