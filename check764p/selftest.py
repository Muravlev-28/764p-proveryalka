"""Packaged offline smoke test, using no source files or external test runner."""
import json
import platform
import tempfile
from pathlib import Path
from .synthetic import message
from .excel import template
from .engine import run
from .master import MASTER_NAME,read_master
from .transaction import digest


def self_test(workdir,schemas,gui=False):
    report={'ok':False,'platform':platform.platform(),'python':platform.python_version(),'checks':[]}
    try:
        from .matching import candidates
        if not candidates('Примеров','Примеорв',fuzzy=True):raise RuntimeError('Fuzzy search failed')
        report['checks'].append('Fuzzy Damerau-Levenshtein candidate search')
        with tempfile.TemporaryDirectory(prefix='Самопроверка ',dir=workdir) as temp:
            folder=Path(temp)
            template(folder/'клиенты.xlsx')
            (folder/'CB_ES550P_20261019_001.XML').write_bytes(message([dict(number=1),dict(number=2)]))
            config=dict(clients='клиенты.xlsx',sheet='Клиенты',columns=['Наименование полное'],fuzzy=False)
            first=run(folder,config,schemas)
            if not first['ok']:raise RuntimeError(first['error'])
            if first['summary']['Клиентов с совпадениями']!=1:raise RuntimeError('Синтетическое совпадение не найдено')
            state,files,_=read_master(folder/MASTER_NAME)
            if len(state.active)!=2 or len(files)!=1:raise RuntimeError('Неверный результат первоначальной загрузки')
            report['checks'].append('Schema validation, matching, master, two reports')
            before=digest(folder/MASTER_NAME)
            repeated=run(folder,config,schemas)
            if not repeated['ok'] or digest(folder/MASTER_NAME)!=before:raise RuntimeError('Повторный запуск изменил базу')
            report['checks'].append('Repeat is idempotent')
            (folder/'CB_ES550P_20261019_002.XML').write_bytes(message([dict(number=1,code=4)]))
            closed=run(folder,config,schemas)
            if not closed['ok']:raise RuntimeError(closed['error'])
            state,_,_=read_master(folder/MASTER_NAME)
            if len(state.active)!=1:raise RuntimeError('Закрытие затронуло другую запись')
            report['checks'].append('Record-specific close and backup')
            before=digest(folder/MASTER_NAME)
            (folder/'CB_ES550P_20261019_003.XML').write_bytes(b'<broken>')
            failed=run(folder,config,schemas)
            if failed['ok'] or digest(folder/MASTER_NAME)!=before:raise RuntimeError('Повреждённый XML изменил базу')
            report['checks'].append('Invalid XML aborts without changing master')
            if gui:
                from .app import gui as application_window
                from .transaction import durable_json
                (folder/'CB_ES550P_20261019_003.XML').unlink()
                durable_json(folder/'service'/'settings.json',config)
                ui_result=application_window(folder,schemas,smoke=True)
                if not ui_result.get('ok'):raise RuntimeError('GUI workflow failed: '+str(ui_result.get('error')))
                report['checks'].append('Actual Tk application window, auto-start, worker, reports and close')
        report['ok']=True
    except Exception as exc:
        report['error']=str(exc)
    (Path(workdir)/'self-test-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
