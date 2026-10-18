"""定时任务：调度判定 + 指定项目独立执行

三种调度方式（schedule_type，与「循环」正交）：
- delay   倒计时：armed_at + {minutes} 后触发一次（恒 once）
- at      时间点：{at: ISO} 触发一次；repeat=daily 则每天该时刻
- window  时间段：{start,end} "HH:MM"（支持跨午夜）窗口内触发；
          repeat=once 仅 armed_at 当天；窗口结束时取消进行中的任务
          （协作式取消——当前批次会收尾，与手动取消一致）

repeat_policy: once | daily。错过时间点/窗口不补跑（电脑未开即跳过，
任务中心可见 last_status）。

执行：ProjectRuntime 为目标项目独立构建 db/翻译器/服务，不依赖当前
打开的项目；jobs/logger/项目管理器复用全局 AppState。
"""
import asyncio
from datetime import datetime, timedelta


class ProjectRuntime:
    """指定项目的执行上下文（鸭子类型兼容 AppState 的任务工厂用法）。

    db/translator/translation_service/name_service 指向目标项目；
    其余属性（jobs/logger/run_sync/db_lock/project_manager/sdk_manager…）
    经 __getattr__ 委托全局 AppState。
    """

    __slots__ = ('_base', 'current_project', 'db', 'translator',
                 'translation_service', '_name_service')

    def __init__(self, base, current_project, db, translator,
                 translation_service):
        self._base = base
        self.current_project = current_project
        self.db = db
        self.translator = translator
        self.translation_service = translation_service
        self._name_service = None

    def __getattr__(self, name):
        return getattr(self._base, name)

    async def db_call(self, fn, *args, **kwargs):
        """与 AppState.db_call 同语义（共享 db 写锁，避免并发写）"""
        async with self._base.db_lock:
            return await self._base.run_sync(fn, *args, **kwargs)

    def get_name_service(self, max_context_k: int = 8):
        from .errors import ApiError
        if self._name_service is None:
            if self.translation_service is None or self.translator is None:
                raise ApiError(409, 'NO_TRANSLATOR', '请先配置模型')
            from services.name_translation import NameTranslationService
            self._name_service = NameTranslationService(
                db=self.db, translator=self.translator,
                translation_service=self.translation_service,
                logger=self._base.logger, max_context_k=max_context_k)
        return self._name_service

    def close(self):
        for svc in (self._name_service, self.translation_service):
            if svc is not None:
                try:
                    svc.close()
                except Exception:
                    pass
        if self.db is not None:
            try:
                self.db.close()
            except Exception:
                pass


# ---- 调度判定（纯函数，可单测） ----

_FIRE_LOCKOUT = timedelta(minutes=20)   # 循环任务距上次触发的最小间隔（tick 30s 的防抖）


def _minutes(now: datetime) -> int:
    return now.hour * 60 + now.minute


def _parse_hhmm(s: str) -> int:
    h, m = (int(x) for x in s.split(':')[:2])
    return h * 60 + m


def window_active(schedule: dict, now: datetime) -> bool:
    """窗口是否生效（支持跨午夜）"""
    start = _parse_hhmm(schedule['start'])
    end = _parse_hhmm(schedule['end'])
    m = _minutes(now)
    if start <= end:
        return start <= m < end
    return m >= start or m < end


def next_run_at(task: dict, now: datetime):
    """下次触发时间（无计划/已过期 → None），前端展示倒计时用"""
    if not task.get('enabled'):
        return None
    st = task['schedule_type']
    sch = task['schedule']
    try:
        if st == 'delay':
            armed = datetime.fromisoformat(task.get('armed_at') or now.isoformat())
            return armed + timedelta(minutes=float(sch['minutes']))
        if st == 'at':
            at = datetime.fromisoformat(sch['at'])
            if task.get('repeat_policy') == 'daily':
                cand = now.replace(hour=at.hour, minute=at.minute,
                                   second=0, microsecond=0)
                return cand if cand > now else cand + timedelta(days=1)
            return at if at > now else None
        if st == 'window':
            if task.get('repeat_policy') == 'once':
                armed = datetime.fromisoformat(
                    task.get('armed_at') or now.isoformat())
                if now.date() != armed.date() and now > armed:
                    return None
            start = _parse_hhmm(sch['start'])
            if window_active(sch, now):
                return now
            cand = now.replace(hour=start // 60, minute=start % 60,
                               second=0, microsecond=0)
            return cand if cand > now else cand + timedelta(days=1)
    except (KeyError, ValueError, TypeError):
        return None
    return None


def due_action(task: dict, now: datetime, last_active: bool) -> str | None:
    """当前时刻该任务的动作：'fire' | 'stop' | None

    last_active：该任务最近一次触发的 job 仍在运行/等确认。
    """
    st = task['schedule_type']
    sch = task['schedule']
    if not task.get('enabled'):
        return None
    last_fired = task.get('last_fired_at')
    fired_dt = None
    if last_fired:
        try:
            fired_dt = datetime.fromisoformat(last_fired)
        except ValueError:
            fired_dt = None

    if last_active:
        # 窗口结束：取消进行中的任务（无论进行到何种情况）
        if st == 'window' and not window_active(sch, now):
            return 'stop'
        return None
    if fired_dt and now - fired_dt < _FIRE_LOCKOUT:
        return None

    try:
        if st == 'delay':
            armed = datetime.fromisoformat(task.get('armed_at') or now.isoformat())
            if now >= armed + timedelta(minutes=float(sch['minutes'])):
                return 'fire'
        elif st == 'at':
            at = datetime.fromisoformat(sch['at'])
            if task.get('repeat_policy') == 'daily':
                cand = now.replace(hour=at.hour, minute=at.minute,
                                   second=0, microsecond=0)
                if fired_dt and fired_dt.date() == now.date() \
                        and fired_dt.hour == at.hour:
                    return None  # 今天已触发过
                if cand <= now <= cand + _FIRE_LOCKOUT:
                    return 'fire'
            elif at <= now <= at + _FIRE_LOCKOUT:
                return 'fire'
        elif st == 'window':
            if task.get('repeat_policy') == 'once':
                armed = datetime.fromisoformat(
                    task.get('armed_at') or now.isoformat())
                if now.date() != armed.date() and now > armed:
                    return None
            if window_active(sch, now):
                return 'fire'
    except (KeyError, ValueError, TypeError):
        return None
    return None


# ---- 触发执行 ----

async def fire(state, task: dict) -> dict:
    """立即执行定时任务：为任务目标项目构建运行时并启动对应任务体。

    返回 {job_id?, error?}；错误写入 task.last_status（不抛——调度循环
    不应因单个任务失败而中断）。
    """
    from .errors import ApiError

    rt = state.project_runtime(task['project'])
    if rt is None:
        state.app_db.update_scheduled_task(
            task['id'], last_status=f'项目不存在: {task["project"]}',
            last_fired_at=datetime.now().isoformat(timespec='seconds'))
        return {'error': 'project-missing'}

    kind = task['kind']
    params = task.get('params') or {}
    result: dict
    try:
        if kind.startswith('texts.translate-all.'):
            from .api.texts import TranslateAllIn, start_translate_all
            ct = kind.rsplit('.', 1)[-1]
            scope = TranslateAllIn(
                filter_mode=params.get('filter_mode', 'all'),
                search=params.get('search', ''),
                character=params.get('character', ''))
            result = await start_translate_all(rt, ct, scope)
        elif kind.startswith('names.translate-all.'):
            from .api.names import start_names_all
            result = await start_names_all(rt, kind.rsplit('.', 1)[-1])
        elif kind.startswith('texts.failed-retry.'):
            from .api.texts import start_failed_retry
            ct = kind.rsplit('.', 1)[-1]
            result = await start_failed_retry(rt, ct, params.get('chunk_size', 10))
        elif kind == 'ui.hints-rebuild':
            from .api.texts import start_hints_rebuild
            result = await start_hints_rebuild(rt)
        elif kind == 'export.game':
            from .api.export import start_export_game
            result = await start_export_game(rt, params.get('shell', True))
        elif kind == 'graph.story-build':
            from .api.graph import start_story_build
            result = await start_story_build(
                rt, incremental=params.get('incremental', False),
                engine_render=False)
        elif kind == 'graph.relations-build':
            from .api.graph import start_relations_build
            result = await start_relations_build(rt, engine_render=False)
        elif kind == 'embedded.scan':
            from .api.embedded import start_embedded_scan
            result = await start_embedded_scan(rt)
        elif kind == 'project.export-zip':
            from .api.projects import start_export_zip
            result = await start_export_zip(rt, task['project'])
        else:
            result = {'job_id': None, 'message': f'未知任务类型: {kind}'}
    except ApiError as e:
        state.app_db.update_scheduled_task(
            task['id'], last_status=f'启动失败({e.code}): {e}',
            last_fired_at=datetime.now().isoformat(timespec='seconds'))
        return {'error': str(e)}
    except Exception as e:  # noqa: BLE001 — 调度循环不因单任务中断
        state.app_db.update_scheduled_task(
            task['id'], last_status=f'启动失败: {e}',
            last_fired_at=datetime.now().isoformat(timespec='seconds'))
        return {'error': str(e)}
    # 注意：任务创建成功时不能在此关闭 rt——任务体在后台继续使用
    # db/翻译服务线程池（提前 close 会报 cannot schedule new futures
    # after shutdown）。终结后由 schedule_tick 统一释放。
    job_id = (result or {}).get('job_id')
    if job_id:
        state._scheduled_runtimes[job_id] = rt
    else:
        rt.close()
    state.app_db.update_scheduled_task(
        task['id'], last_job_id=job_id or '', last_status='已触发',
        last_fired_at=datetime.now().isoformat(timespec='seconds'))
    return {'job_id': job_id}


async def schedule_tick(state) -> None:
    """调度主循环单次扫描（默认 30s 一次；不并发、不补跑）"""
    # 释放已终结任务的项目运行时（db/翻译服务线程池随任务生命周期）
    terminal = ('succeeded', 'failed', 'cancelled', 'interrupted')
    for jid, rt in list(state._scheduled_runtimes.items()):
        rec = state.app_db.get_job(jid)
        if rec is None or rec['status'] in terminal:
            try:
                rt.close()
            finally:
                state._scheduled_runtimes.pop(jid, None)
    now = datetime.now()
    tasks = state.app_db.list_scheduled_tasks(enabled_only=True)
    for t in tasks:
        last_active = False
        if t.get('last_job_id'):
            rec = state.app_db.get_job(t['last_job_id'])
            last_active = bool(rec and rec['status'] in
                               ('running', 'waiting_input'))
        action = due_action(t, now, last_active)
        if action == 'stop' and t.get('last_job_id'):
            try:
                await state.jobs.cancel(t['last_job_id'])
            except Exception:
                pass
        elif action == 'fire':
            await fire(state, t)
            # 一次性任务（delay/at once/window once）触发后停用
            if t.get('repeat_policy') != 'daily':
                state.app_db.update_scheduled_task(t['id'], enabled=0)
