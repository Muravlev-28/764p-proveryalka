"""Offline workflow, staging, reports and recovery. UI calls run()."""
from datetime import datetime
from pathlib import Path
from hashlib import sha256
from uuid import uuid4
import os
import shutil
import time
from .xml_input import schema, parse_bytes, validate, order_messages
from .adapter import events
from .state import State, apply, EVENT_VERSION
from .matching import RULES_VERSION, NORMALIZATION_VERSION
from . import __version__
from .master import MASTER_NAME, read_master, write_master, people
from .excel import read_clients
from .reports import entries,event_entries,check_clients,save_report01,save_report02
from .transaction import DirectoryLock, Transaction, digest, durable_json


def timestamp():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def empty_results():
    return dict(clients=[],active=[],changes=[],quality=[],matched=0,direct=0,participant_only=0)


def run(root, config, schemas, progress=lambda x:None):
    root=Path(root).resolve()
    run_id=datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_')+uuid4().hex[:8]
    log=[]; discovered=[]; actions=[]; result=empty_results()
    summary={'run_id':run_id,'Начало':timestamp(),'Покрытие':'Частичное','Проверка':'Не выполнена','Версия программы':__version__,'Версия нормализации':NORMALIZATION_VERSION,'Версия сопоставления':RULES_VERSION,'Версия событий':EVENT_VERSION}
    report_dir=root/'Результаты проверки'/run_id
    report1=report_dir/f'01_ПроверкаКлиентов_{run_id}.xlsx'
    report2=report_dir/f'02_ОбновлениеБазы_{run_id}.xlsx'
    committed=False
    started=time.perf_counter()
    def record(stage,code,message,level='INFO'):
        log.append([timestamp(),stage,level,code,message]); progress(message)
        summary[f'Время этапа {code}, с']=round(time.perf_counter()-started,3)
    try:
        with DirectoryLock(root):
            tx=Transaction(root)
            recovered=tx.recover()
            record('Подготовка','RECOVERY',f'Восстановление: {recovered}')
            report_dir.mkdir(parents=True)
            staging=root/'service'/run_id; staging.mkdir()
            master=root/MASTER_NAME
            before=digest(master) if master.exists() else None
            if master.exists():
                state,accepted,meta=read_master(master)
            elif config.get('initial_master'):
                imported=tx.path(config['initial_master'])
                state,accepted,meta=read_master(imported)
                meta['base_origin']=str(imported)
                meta['base_confirmation']=config.get('confirmation','')
                if config.get('confirmed_complete'):
                    if not meta['base_confirmation']: raise ValueError('Не указано происхождение подтверждённой базы')
                    meta['coverage']='Подтверждено ответственным пользователем'
            else:
                state,accepted,meta=State(),[],{'coverage':'Частичное','base_origin':'Накопление из XML','observation_start':timestamp()}
            summary.update({'Покрытие':meta.get('coverage','Частичное'),'Записей до':len(state.active),
                            'Участников до':sum(len(x['participants']) for x in state.active.values()),'Лиц до':len(people(state)),
                            'Ограничение покрытия':'Программа не может установить, остались ли нескaчанные сообщения или части дня в ЛК',
                            'SHA-256 базы до':before or 'Базы нет'})
            client_path=tx.path(config['clients'])
            inputs={config['clients']:digest(client_path)}
            if before: inputs[MASTER_NAME]=before
            if config.get('initial_master') and not master.exists(): inputs[config['initial_master']]=digest(root/config['initial_master'])
            accepted_hashes={f['sha256'] for f in accepted}
            for f in accepted:
                archive=tx.path(f['archive'])
                if not archive.is_file() or digest(archive)!=f['sha256']:
                    raise ValueError(f'Архив отсутствует или повреждён: {f["archive"]}')
            validator=schema(schemas)
            incoming=[]; event_map={}
            for path in sorted(root.iterdir()):
                if path.suffix.lower() not in ('.xml','.enc','.sig'): continue
                f={'filename':path.name,'status':'Не относится'};discovered.append(f)
                if path.suffix.lower()!='.xml':
                    f['reason']='Зашифрованный файл или подпись'; continue
                raw=path.read_bytes(); h=sha256(raw).hexdigest(); f['sha256']=h
                inputs[path.name]=h
                if h in accepted_hashes:
                    f['status']='Дубль';continue
                try:
                    parsed=parse_bytes(raw)
                except Exception:
                    f['status']='Ошибка';f['reason']='Некорректный XML';raise
                if parsed.tag!='СообщОтказ_115ФЗ':
                    if path.name.upper().startswith('CB_ES550P_'):
                        raise ValueError(f'Неизвестный профиль целевого XML: {path.name}')
                    f['reason']='XML другого назначения';continue
                try:
                    parsed,order,h=validate(raw,path.name,validator)
                    f.update(order=order,version=parsed.findtext('СлужЧасть/ВерсияФормата'),status='Проверен',archive=f'archive/xml/{h}.xml')
                    ev=events(parsed,f)
                except Exception as exc:
                    f.update(status='Ошибка',reason=str(exc));raise
                incoming.append(f); event_map[h]=(ev,raw)
            record('XML','PARSED','XML проверены; построение проекта базы')
            pending=order_messages(incoming,accepted)
            pending_hashes={f['sha256'] for f in pending}
            for f in incoming:
                if f['sha256'] not in pending_hashes: f['status']='Дубль'
            if not accepted and not pending and not master.exists():
                raise ValueError('Нет ни принятых XML, ни накопленной базы')
            last=max(accepted+pending,key=lambda f:tuple(f['order'])) if accepted or pending else None
            package=[e for f in pending for e in event_map[f['sha256']][0]]
            projected,actions=apply(state,package,run_id)
            searchable=list(package)
            if last and last['sha256'] not in pending_hashes:
                raw=(root/last['archive']).read_bytes()
                parsed,_,_=validate(raw,last['filename'],validator)
                searchable.extend(events(parsed,last))
                inputs[last['archive']]=last['sha256']
            clients,skipped=read_clients(client_path,config['sheet'],config['columns'],config.get('header_row',3),config.get('extra_columns'))
            record('Проверка','MATCH','Сопоставление клиентов с XML и действующим состоянием')
            result=check_clients(clients,entries(projected),event_entries(searchable,projected.active,last['sha256'] if last else ''),config.get('fuzzy',True),progress)
            for key,data in projected.active.items():
                for p in data['participants']:
                    result['quality'].extend(['|'.join(key),q] for q in p.get('quality',[]))
            if meta.get('coverage') != 'Частичное':
                for row in result['clients']:
                    if row[2]=='Не найдено':row[6]='Совпадение в доступных данных не найдено'
            summary.update({'Клиентов':len(clients),'Пустых строк':skipped,'Клиентов с совпадениями':result['matched'],
                            'Прямых упоминаний':result['direct'],'Только участников':result['participant_only'],
                            'Последнее сообщение':str(last['order']) if last else '',
                            'Записей после':len(projected.active),'Участников после':sum(len(x['participants']) for x in projected.active.values()),
                            'Лиц после':len(people(projected)),'Новых XML':len(pending),'Проверка':'Выполнена',
                            'Файлы и SHA-256':str(inputs),'Нечёткий поиск':'Включён' if config.get('fuzzy',True) else 'Отключён',
                            'Добавлено':sum(a['action']=='ADD' for a in actions),'Скорректировано':sum(a['action']=='UPDATE' for a in actions),
                            'Дубли записей':sum(a.get('warning')=='DUPLICATE_RECORD' for a in actions),
                            'Закрытие отсутствующего':sum(a.get('warning')=='CLOSE_NOT_FOUND' for a in actions),
                            'Проблемных строк':sum(bool(c['issues']) for c in clients),
                            'Окончание проверки клиентов':timestamp()})
            from collections import Counter
            for status,count in Counter(row[2] for row in result['clients']).items():summary[f'Клиенты: {status}']=count
            for code in range(4,9): summary[f'Закрыто код {code}']=sum(a['action']=='CLOSE' and a['code']==code for a in actions)
            stage1,stage2=staging/report1.name,staging/report2.name
            save_report01(stage1,summary,result,log)
            if pending or (config.get('initial_master') and not master.exists()):
                next_files=list(accepted)
                for f in pending:
                    ev,raw=event_map[f['sha256']]
                    dest=root/f['archive'];dest.parent.mkdir(parents=True,exist_ok=True)
                    if dest.exists():
                        if digest(dest)!=f['sha256']: raise ValueError('Конфликт архива XML')
                    else:
                        with dest.open('xb') as stream:
                            stream.write(raw);stream.flush();os.fsync(stream.fileno())
                    f.update(status='Применён',run_id=run_id,accepted_at=timestamp(),events=len(ev))
                    next_files.append(dict(f))
                meta.update(last_message=list(last['order']) if last else None,last_run=run_id,app_version=__version__,normalization_version=NORMALIZATION_VERSION,matching_version=RULES_VERSION,event_version=EVENT_VERSION)
                staged_master=staging/MASTER_NAME
                write_master(staged_master,projected,next_files,meta)
                verified,verified_files,_=read_master(staged_master)
                if len(verified.active)!=len(projected.active) or len(verified_files)!=len(next_files):
                    raise ValueError('Проверка новой книги не прошла')
                record('Книга','VERIFIED','Подготовленная книга записана и повторно проверена')
                summary['SHA-256 базы после']=digest(staged_master)
                summary['Резервная копия']=f'backups/{run_id}.xlsx' if before else 'Первоначальное создание'
                summary['Итог']='Обновлено'
                summary['Время подготовки итогового отчёта']=timestamp()
                summary['Время фактической фиксации']='См. журнал service/run_'+run_id+'.json (этап COMMITTED)'
                record('Фиксация','PREPARED','Книга и отчёты подготовлены; публикация будет выполнена после замены базы')
                save_report02(stage2,summary,actions,discovered,log)
                publications=[(str(stage1.relative_to(root)),str(report1.relative_to(root))),(str(stage2.relative_to(root)),str(report2.relative_to(root)))]
                tx.prepare(run_id,MASTER_NAME,str(staged_master.relative_to(root)),publications,inputs)
                tx.commit();committed=True
                record('Фиксация','COMMITTED','База обновлена; оба отчёта опубликованы')
            else:
                summary['Итог']='Новых файлов нет, база не изменялась'
                summary['SHA-256 базы после']=before or ''
                save_report02(stage2,summary,actions,discovered,log)
                for name,h in inputs.items():
                    if digest(root/name)!=h: raise ValueError('Входные файлы изменились во время проверки')
                publications=[(str(stage1.relative_to(root)),str(report1.relative_to(root))),(str(stage2.relative_to(root)),str(report2.relative_to(root)))]
                tx.prepare_reports(run_id,publications,inputs)
                tx.commit()
            summary['Окончание запуска']=timestamp()
            durable_json(root/'service'/f'run_{run_id}.json',dict(summary=summary,log=log))
            return dict(ok=True,run_id=run_id,summary=summary,reports=[str(report1),str(report2)])
    except Exception as exc:
        # If replacement already happened, retain prepared reports and journal for recovery.
        journal=root/'service'/'transaction.json'
        pending_commit=False
        if journal.exists():
            import json
            try:
                transaction=json.loads(journal.read_text(encoding='utf-8'))
                pending_commit=transaction.get('stage')=='REPORTS_READY' if transaction.get('mode')=='reports' else (root/MASTER_NAME).exists() and digest(root/MASTER_NAME)==transaction['after']
            except Exception: pending_commit=True
        message=str(exc)
        record('Ошибка','ERROR',message,'ERROR')
        summary.update({'Итог':'Фиксация выполнена; требуется восстановление публикации' if pending_commit else 'Обновление отменено',
                        'Причина':message,'Окончание':timestamp()})
        try:
            report_dir.mkdir(parents=True,exist_ok=True)
            if not pending_commit:
                save_report01(report1,summary,result,log)
                save_report02(report2,summary,[],discovered,log)
            durable_json(root/'service'/f'run_{run_id}.json',dict(summary=summary,log=log))
        except Exception as report_error:
            try:
                (root/f'АварийныйЖурнал_{run_id}.txt').write_text(str(summary)+'\n'+str(report_error),encoding='utf-8')
            except OSError: pass
            message+=f'\nНе удалось сохранить отчёт: {report_error}'
        return dict(ok=False,run_id=run_id,error=message,summary=summary,reports=[str(report1),str(report2)])
