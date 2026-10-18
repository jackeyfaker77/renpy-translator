"""应用级 SQLite（data/app.db）：settings / jobs / job_events / scheduled_tasks

与项目库（projects/<name>/project.db）分离——任务可不属于任何项目（如建项目）。
任务与事件持久化：服务重启后 running/waiting_input 标为 interrupted
（终态，仅作历史记录；无断点续跑实现）。
"""
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    label         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'running',
    progress      REAL NOT NULL DEFAULT 0,
    stage         TEXT NOT NULL DEFAULT '',
    payload_json  TEXT NOT NULL DEFAULT '{}',
    question_json TEXT,
    result_json   TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_events (
    job_id    TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    kind      TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (job_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL,
    project        TEXT NOT NULL,
    params_json    TEXT NOT NULL DEFAULT '{}',
    schedule_type  TEXT NOT NULL,
    schedule_json  TEXT NOT NULL,
    repeat_policy  TEXT NOT NULL DEFAULT 'once',
    enabled        INTEGER NOT NULL DEFAULT 1,
    armed_at       TEXT,
    last_job_id    TEXT,
    last_status    TEXT,
    last_fired_at  TEXT,
    created_at     TEXT NOT NULL
);
"""

# 每任务保留的最近事件条数
EVENT_KEEP = 2000


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


class AppDatabase:
    """线程安全（内锁）的应用级数据库；所有方法都是阻塞式，调用方自行放 executor"""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self):
        with self._lock:
            self._conn.close()

    # ---- settings ----

    def get_setting(self, key: str, default: str = '') -> str:
        with self._lock:
            row = self._conn.execute(
                'SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default

    def set_setting(self, key: str, value: str):
        with self._lock:
            self._conn.execute(
                'INSERT INTO settings(key,value) VALUES(?,?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, value))
            self._conn.commit()

    # ---- jobs ----

    def create_job(self, kind: str, label: str, payload: dict) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                'INSERT INTO jobs(id,kind,label,status,payload_json,created_at,updated_at) '
                'VALUES(?,?,?,?,?,?,?)',
                (job_id, kind, label, 'running', json.dumps(payload, ensure_ascii=False),
                 _now(), _now()))
            self._conn.commit()
        return job_id

    def update_job(self, job_id: str, **fields):
        """可更新字段: status/progress/stage/payload/question/result/error"""
        col_map = {
            'status': 'status', 'progress': 'progress', 'stage': 'stage',
            'payload': 'payload_json', 'question': 'question_json',
            'result': 'result_json', 'error': 'error',
        }
        sets, vals = [], []
        for k, v in fields.items():
            col = col_map[k]
            sets.append(f'{col}=?')
            if col.endswith('_json') and v is not None and not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            vals.append(v)
        sets.append('updated_at=?')
        vals.append(_now())
        vals.append(job_id)
        with self._lock:
            self._conn.execute(
                f'UPDATE jobs SET {", ".join(sets)} WHERE id=?', vals)
            self._conn.commit()

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                'SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        return self._job_row(row) if row else None

    def list_jobs(self, active_only: bool = False, limit: int = 50) -> list:
        sql = 'SELECT * FROM jobs'
        if active_only:
            # interrupted 是终态（历史记录），不算活跃——
            # 前端恢复对话框只接管真正还在跑/等输入的任务
            sql += " WHERE status IN ('running','waiting_input')"
        sql += ' ORDER BY created_at DESC LIMIT ?'
        with self._lock:
            rows = self._conn.execute(sql, (limit,)).fetchall()
        return [self._job_row(r) for r in rows]

    def mark_interrupted(self) -> int:
        """启动时调用：所有 running/waiting_input → interrupted"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status='interrupted', updated_at=? "
                "WHERE status IN ('running','waiting_input')", (_now(),))
            self._conn.commit()
            return cur.rowcount

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        for col in ('payload_json', 'question_json', 'result_json'):
            raw = d.pop(col)
            key = col[:-5]  # 去掉 _json
            d[key] = json.loads(raw) if raw else None
        return d

    # ---- job_events ----

    def add_event(self, job_id: str, kind: str, data: dict) -> int:
        """追加事件，返回 seq；超出 EVENT_KEEP 裁掉最旧的"""
        with self._lock:
            row = self._conn.execute(
                'SELECT COALESCE(MAX(seq),0)+1 AS seq FROM job_events WHERE job_id=?',
                (job_id,)).fetchone()
            seq = row['seq']
            self._conn.execute(
                'INSERT INTO job_events(job_id,seq,kind,data_json) VALUES(?,?,?,?)',
                (job_id, seq, kind, json.dumps(data, ensure_ascii=False)))
            self._conn.execute(
                'DELETE FROM job_events WHERE job_id=? AND seq <= ?',
                (job_id, seq - EVENT_KEEP))
            self._conn.commit()
        return seq

    def get_events(self, job_id: str, after_seq: int = 0) -> list:
        with self._lock:
            rows = self._conn.execute(
                'SELECT seq,kind,data_json FROM job_events '
                'WHERE job_id=? AND seq>? ORDER BY seq',
                (job_id, after_seq)).fetchall()
        return [{'seq': r['seq'], 'kind': r['kind'],
                 'data': json.loads(r['data_json'])} for r in rows]

    # ---- scheduled_tasks（定时任务） ----

    def list_scheduled_tasks(self, enabled_only: bool = False) -> list:
        sql = 'SELECT * FROM scheduled_tasks'
        if enabled_only:
            sql += ' WHERE enabled=1'
        sql += ' ORDER BY created_at'
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [self._sched_row(r) for r in rows]

    def get_scheduled_task(self, task_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                'SELECT * FROM scheduled_tasks WHERE id=?', (task_id,)).fetchone()
        return self._sched_row(row) if row else None

    def add_scheduled_task(self, task: dict) -> str:
        """task: name/kind/project/params/schedule_type/schedule/repeat_policy/enabled"""
        tid = task.get('id') or uuid.uuid4().hex[:12]
        now = _now()
        with self._lock:
            self._conn.execute(
                'INSERT INTO scheduled_tasks(id,name,kind,project,params_json,'
                'schedule_type,schedule_json,repeat_policy,enabled,armed_at,created_at) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (tid, task['name'], task['kind'], task['project'],
                 json.dumps(task.get('params') or {}, ensure_ascii=False),
                 task['schedule_type'],
                 json.dumps(task['schedule'], ensure_ascii=False),
                 task.get('repeat_policy', 'once'),
                 1 if task.get('enabled', True) else 0,
                 task.get('armed_at') or now, now))
            self._conn.commit()
        return tid

    def update_scheduled_task(self, task_id: str, **fields) -> bool:
        """可更新 name/kind/project/params/schedule_type/schedule/
        repeat_policy/enabled/armed_at/last_* 状态字段"""
        sets, params = [], []
        for key in ('name', 'kind', 'project', 'schedule_type', 'repeat_policy',
                    'armed_at', 'last_job_id', 'last_status', 'last_fired_at'):
            if key in fields:
                sets.append(f'{key}=?')
                params.append(fields[key])
        if 'enabled' in fields:
            sets.append('enabled=?')
            params.append(1 if fields['enabled'] else 0)
        if 'params' in fields:
            sets.append('params_json=?')
            params.append(json.dumps(fields['params'], ensure_ascii=False))
        if 'schedule' in fields:
            sets.append('schedule_json=?')
            params.append(json.dumps(fields['schedule'], ensure_ascii=False))
        if not sets:
            return False
        params.append(task_id)
        with self._lock:
            cur = self._conn.execute(
                f'UPDATE scheduled_tasks SET {", ".join(sets)} WHERE id=?', params)
            self._conn.commit()
        return cur.rowcount > 0

    def delete_scheduled_task(self, task_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                'DELETE FROM scheduled_tasks WHERE id=?', (task_id,))
            self._conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _sched_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d['params'] = json.loads(d.pop('params_json') or '{}')
        d['schedule'] = json.loads(d.pop('schedule_json') or '{}')
        d['enabled'] = bool(d['enabled'])
        return d
