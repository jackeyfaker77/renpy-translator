"""定时任务：调度判定纯函数 + app.db 存储 CRUD"""
from datetime import datetime

import pytest

from server.appdb import AppDatabase
from server.scheduler import due_action, next_run_at, window_active


def _task(stype, sch, repeat='once', enabled=True, armed='2026-10-17T08:00:00',
          last_fired=None):
    return {'id': 't1', 'name': 'x', 'kind': 'texts.translate-all.dialogue',
            'project': 'p', 'schedule_type': stype, 'schedule': sch,
            'repeat_policy': repeat, 'enabled': enabled,
            'armed_at': armed, 'last_fired_at': last_fired}


# ---- window_active ----

def test_window_basic_and_cross_midnight():
    assert window_active({'start': '00:00', 'end': '08:00'},
                         datetime(2026, 10, 17, 3, 0))
    assert not window_active({'start': '00:00', 'end': '08:00'},
                             datetime(2026, 10, 17, 9, 0))
    # 跨午夜 22:00–02:00
    assert window_active({'start': '22:00', 'end': '02:00'},
                         datetime(2026, 10, 17, 23, 30))
    assert window_active({'start': '22:00', 'end': '02:00'},
                         datetime(2026, 10, 18, 1, 0))
    assert not window_active({'start': '22:00', 'end': '02:00'},
                             datetime(2026, 10, 18, 12, 0))


# ---- due_action ----

def test_delay_fires_after_countdown_only_once():
    t = _task('delay', {'minutes': 60}, armed='2026-10-17T08:00:00')
    assert due_action(t, datetime(2026, 10, 17, 8, 30), False) is None
    assert due_action(t, datetime(2026, 10, 17, 9, 1), False) == 'fire'
    # 触发后 20 分钟内防抖（last_fired_at 记录）
    t2 = dict(t, last_fired_at='2026-10-17T09:01:00')
    assert due_action(t2, datetime(2026, 10, 17, 9, 5), False) is None


def test_at_one_shot_fires_in_lockout_window():
    t = _task('at', {'at': '2026-10-17T09:00:00'})
    assert due_action(t, datetime(2026, 10, 17, 8, 59), False) is None
    assert due_action(t, datetime(2026, 10, 17, 9, 10), False) == 'fire'
    # 错过（睡过头超过20分钟）不补跑
    assert due_action(t, datetime(2026, 10, 17, 11, 0), False) is None


def test_at_daily_refires_next_day():
    t = _task('at', {'at': '2026-10-17T09:00:00'}, repeat='daily',
              last_fired='2026-10-17T09:01:00')
    # 今天已触发 → 不重复
    assert due_action(t, datetime(2026, 10, 17, 9, 5), False) is None
    # 次日窗口 → 触发
    assert due_action(t, datetime(2026, 10, 18, 9, 5), False) == 'fire'


def test_window_stop_and_refire():
    t = _task('window', {'start': '02:00', 'end': '03:00'}, repeat='daily',
              armed='2026-10-17T00:00:00')
    # 窗口内且上个任务还在跑 → 不重复触发
    assert due_action(t, datetime(2026, 10, 17, 2, 30), True) is None
    # 窗口结束且上个任务还在跑 → 强制取消
    assert due_action(t, datetime(2026, 10, 17, 3, 5), True) == 'stop'
    # 窗口内无任务 → 触发
    assert due_action(t, datetime(2026, 10, 17, 2, 10), False) == 'fire'
    # 窗口外不触发
    assert due_action(t, datetime(2026, 10, 17, 12, 0), False) is None


def test_window_once_only_today():
    t = _task('window', {'start': '02:00', 'end': '03:00'}, repeat='once',
              armed='2026-10-17T00:00:00')
    assert due_action(t, datetime(2026, 10, 17, 2, 10), False) == 'fire'
    # 次日不再触发
    assert due_action(t, datetime(2026, 10, 18, 2, 10), False) is None


def test_disabled_and_active_block():
    t = _task('delay', {'minutes': 1}, enabled=False)
    assert due_action(t, datetime(2026, 10, 18, 9, 0), False) is None
    t2 = _task('delay', {'minutes': 1}, armed='2026-10-17T08:00:00')
    # 上个 job 还在跑：非窗口任务不重复触发
    assert due_action(t2, datetime(2026, 10, 17, 9, 0), True) is None


# ---- next_run_at ----

def test_next_run_at_shapes():
    t = _task('delay', {'minutes': 30}, armed='2026-10-17T08:00:00')
    assert next_run_at(t, datetime(2026, 10, 17, 7, 0)) == datetime(2026, 10, 17, 8, 30)
    t2 = _task('at', {'at': '2026-10-17T09:00:00'})
    assert next_run_at(t2, datetime(2026, 10, 17, 8, 0)) == datetime(2026, 10, 17, 9, 0)
    # 一次性已过期 → None
    assert next_run_at(t2, datetime(2026, 10, 17, 10, 0)) is None
    # daily 跨日取次日
    t3 = _task('at', {'at': '2026-10-17T09:00:00'}, repeat='daily')
    assert next_run_at(t3, datetime(2026, 10, 17, 10, 0)) == datetime(2026, 10, 18, 9, 0)


# ---- app.db 存储 ----

@pytest.fixture
def appdb(tmp_path):
    db = AppDatabase(tmp_path / 'app.db')
    yield db
    db.close()


def test_scheduled_task_crud(appdb):
    tid = appdb.add_scheduled_task({
        'name': '半夜翻译', 'kind': 'texts.translate-all.dialogue',
        'project': 'p1', 'params': {'character': 'asia'},
        'schedule_type': 'window', 'schedule': {'start': '01:00', 'end': '05:00'},
        'repeat_policy': 'daily', 'enabled': True,
    })
    task = appdb.get_scheduled_task(tid)
    assert task['params'] == {'character': 'asia'}
    assert task['schedule'] == {'start': '01:00', 'end': '05:00'}
    assert task['enabled'] is True

    assert appdb.update_scheduled_task(tid, enabled=False, last_job_id='j1',
                                       last_status='已触发')
    assert appdb.get_scheduled_task(tid)['enabled'] is False
    assert appdb.list_scheduled_tasks() and not appdb.list_scheduled_tasks(enabled_only=True)

    assert appdb.delete_scheduled_task(tid)
    assert appdb.get_scheduled_task(tid) is None
