"""Schedule management bound to the desktop's existing queue and worker."""
import json
import re
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class DesktopSchedules:
    def __init__(self, service, scheduler=None):
        from .scheduler import get_scheduler
        self.service = service
        self.scheduler = scheduler or get_scheduler()
        self._lock = threading.RLock()
        self.scheduler.set_task_callback(self._enqueue)
        self.scheduler.set_readiness_check(self._ready)

    def _metadata(self, schedule):
        value = schedule.get('metadata') or {}
        try: value = json.loads(value) if isinstance(value,str) else value
        except ValueError: return {}
        return dict(value) if isinstance(value,dict) else {}

    def _ready(self, info):
        from .mobile_bridge.service import ACTIVE_STATUSES
        profile = info.get('agent_id')
        available = self.service.available_profiles() if callable(self.service.available_profiles) else [self.service.profile]
        if profile not in available:
            return False
        meta = self._metadata(info)
        task_id = meta.get('last_task_id')
        if task_id:
            try:
                if self.service.get_task(task_id).get('status') in ACTIVE_STATUSES:
                    return False
                if meta.get('deliver_session') and meta.get('delivery_task_id') != task_id:
                    return False
            except Exception as error:
                from .mobile_bridge.service import MobileNotFound
                if not isinstance(error,MobileNotFound):return False
        return True

    def _enqueue(self, info):
        from .mobile_bridge.service import MobileInvalid
        with self._lock:
            self.deliver_pending()
            current = self.scheduler.get_schedule(info['schedule_id'])
            if current is None or ('due_at' in info and (not current.enabled or current.next_run_at != info['due_at'])):
                raise MobileInvalid('Расписание изменилось; этот запуск отменён')
            info = dict(info, metadata=self._metadata(current.to_dict()), agent_id=current.agent_id,
                        payload=current.payload, name=current.name)
            if not self._ready(info):
                raise MobileInvalid('Запусти исполнитель выбранного профиля; предыдущий запуск должен завершиться')
            meta = self._metadata(info)
            key = 'schedule:' + info['schedule_id'] + ':' + str(info.get('run_id') or '')
            session = self.service.create_session(profile=info['agent_id'], cwd=meta.get('cwd') or self.service.workdir, idempotency_key=key)
            session_id = session['session_id']
            self.service.update_session(session_id, title=info['name'])
            source = meta.get('deliver_session')
            options = {}
            payload = info['payload']
            if source:
                origin = self.service.get_session(source)
                if origin['meta'].get('profile') != info['agent_id']:
                    raise MobileInvalid('Профиль исходного чата изменился')
                from .prompt_assembly import build_scheduled_run_prompt
                payload = build_scheduled_run_prompt(payload, origin.get('messages', []))
                options['schedule_run'] = {'schedule_id': info['schedule_id'], 'source_session': source,
                                           'quiet': bool(meta.get('quiet'))}
            task = self.service.send_message(session_id, payload, idempotency_key=key, **options)
            meta.update(last_session_id=session_id, last_task_id=task['task_id'],
                        delivery_status='', delivery_attempts=0, delivery_retry_at=0)
            self.scheduler.update_schedule(info['schedule_id'], metadata=meta)

    def deliver_pending(self):
        from .scheduled_delivery import deliver_result
        from .mobile_bridge.service import FINAL_STATUSES
        with self._lock:
            for schedule in self.scheduler.list_schedules():
                meta = self._metadata(schedule.to_dict())
                task_id = meta.get('last_task_id')
                if not task_id or not meta.get('deliver_session') or meta.get('delivery_task_id') == task_id:
                    continue
                if meta.get('delivery_retry_at', 0) > time.time():
                    continue
                try:
                    task = self.service.get_task(task_id)
                    if task.get('status') not in FINAL_STATUSES or task.get('approval_blocked_tools'):
                        continue
                    status = deliver_result(self.service, task, meta['deliver_session'], quiet=bool(meta.get('quiet')))
                except Exception:
                    attempts = int(meta.get('delivery_attempts', 0)) + 1
                    meta.update(delivery_status='delivery_failed', delivery_attempts=attempts,
                                delivery_retry_at=int(time.time()) + 30)
                    if attempts >= 3:
                        meta['delivery_task_id'] = task_id
                    self.scheduler.update_schedule(schedule.schedule_id, metadata=meta)
                    continue
                meta.update(delivery_task_id=task_id, delivery_status=status)
                self.scheduler.update_schedule(schedule.schedule_id, metadata=meta)

    def list(self):
        rows = []
        for schedule in self.scheduler.list_schedules():
            data = schedule.to_dict(); meta = self._metadata(data)
            task_status = ''
            if meta.get('last_task_id'):
                try: task_status = self.service.get_task(meta['last_task_id']).get('status', '')
                except Exception: pass
            rows.append({key:data.get(key) for key in ('schedule_id','name','payload','schedule_type','time_value','cron_expression','timezone','enabled','next_run_at','last_run_at','run_count')} | {
                'profile': data.get('agent_id'), 'cwd': meta.get('cwd') or self.service.workdir,
                'interval_hours': meta.get('interval_hours', 1),
                'last_session_id': meta.get('last_session_id',''), 'last_task_id': meta.get('last_task_id',''),
                'last_status': task_status, 'delivery_status': meta.get('delivery_status', ''), 'ready': self._ready(data)})
        return {'schedules':rows, 'running':self.scheduler.running, 'available':True}

    def save(self, fields, schedule_id=None):
        from .mobile_bridge.service import MobileInvalid, MobileNotFound
        from .scheduler import Schedule
        required = {'name','payload','profile','cwd','schedule_type','time_value','timezone'}
        if not isinstance(fields, dict) or not required <= set(fields) or set(fields) - required - {'interval_hours'}:
            raise MobileInvalid('Некорректные поля расписания')
        maximum = {'name':160,'payload':16000,'profile':64,'cwd':4096,'schedule_type':16,'time_value':5,'timezone':80}
        if any(not isinstance(fields[key],str) or not fields[key].strip() or len(fields[key])>maximum[key] for key in required):
            raise MobileInvalid('Проверь название, задание, профиль и папку')
        fields = {key:value.strip() if key in required else value for key,value in fields.items()}
        if fields['schedule_type'] not in {'daily','once','hourly'} or re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',fields['time_value']) is None:
            raise MobileInvalid('Выбери повтор и время в формате ЧЧ:ММ')
        if 'interval_hours' in fields and (fields['schedule_type'] != 'hourly' or type(fields['interval_hours']) is not int or not 1 <= fields['interval_hours'] <= 8760):
            raise MobileInvalid('Интервал должен быть целым числом часов от 1 до 8760')
        try: ZoneInfo(fields['timezone'])
        except (ZoneInfoNotFoundError,ValueError): raise MobileInvalid('Неизвестный часовой пояс') from None
        directory = Path(fields['cwd']).expanduser()
        if not directory.is_absolute() or not directory.is_dir():
            raise MobileInvalid('Выбери существующую рабочую папку')
        self.service._require_available_profile(fields['profile'])
        with self._lock:
            previous = self.scheduler.get_schedule(schedule_id) if schedule_id else None
            if schedule_id and previous is None:
                raise MobileNotFound('Расписание не найдено')
            meta = self._metadata(previous.to_dict()) if previous else {'created_via':'desktop'}
            meta['cwd'] = str(directory.resolve())
            hours = fields.get('interval_hours', meta.get('interval_hours', 1) if previous and previous.schedule_type == 'hourly' else 1)
            if fields['schedule_type'] == 'hourly' and hours > 1:
                if (previous is None or previous.schedule_type != 'hourly' or meta.get('interval_hours', 1) != hours
                        or previous.time_value != fields['time_value'] or previous.tz != fields['timezone']):
                    meta.pop('interval_anchor_at', None)
                meta['interval_hours'] = hours
            else:
                meta.pop('interval_hours', None)
                meta.pop('interval_anchor_at', None)
            preview = Schedule({'schedule_type':fields['schedule_type'],'time_value':fields['time_value'],
                                'timezone':fields['timezone'],'enabled':True,'metadata':meta})
            next_run = preview.calculate_next_run()
            if next_run is None:
                raise MobileInvalid('Для разового задания выбери время позже текущего сегодня')
            if fields['schedule_type'] == 'hourly' and hours > 1:
                meta.setdefault('interval_anchor_at', next_run)
            values = dict(name=fields['name'],agent_id=fields['profile'],payload=fields['payload'],schedule_type=fields['schedule_type'],time_value=fields['time_value'],tz=fields['timezone'],metadata=meta)
            if previous:
                values['timezone'] = values.pop('tz')
                self.scheduler.update_schedule(schedule_id, **values)
            else:
                schedule_id = self.scheduler.create_schedule(**values)
        return {'schedule_id':schedule_id, **self.list()}

    def action(self, schedule_id, action, fields=None):
        from .mobile_bridge.service import MobileInvalid, MobileNotFound
        with self._lock:
            if self.scheduler.get_schedule(schedule_id) is None:
                raise MobileNotFound('Расписание не найдено')
            if action == 'enabled':
                if not isinstance(fields,dict) or set(fields) != {'enabled'} or not isinstance(fields['enabled'],bool):
                    raise MobileInvalid('enabled должен быть boolean')
                if fields['enabled']:
                    from .scheduler import Schedule
                    current=self.scheduler.get_schedule(schedule_id).to_dict()
                    if Schedule(current | {'enabled':True}).calculate_next_run() is None:
                        raise MobileInvalid('Задай новое время перед возобновлением расписания')
                self.scheduler.update_schedule(schedule_id,enabled=fields['enabled'])
            elif action == 'run':
                if fields not in (None,{}): raise MobileInvalid('Некорректные поля запуска')
                if not self.scheduler.run_schedule_now(schedule_id):
                    raise MobileInvalid('Запусти исполнитель выбранного профиля; предыдущий запуск должен завершиться')
            elif action == 'delete':
                self.scheduler.delete_schedule(schedule_id)
            else:
                raise MobileInvalid('Неизвестное действие расписания')
            return self.list()

    def start(self):
        self.scheduler.start()

    def stop(self):
        self.scheduler.stop()
