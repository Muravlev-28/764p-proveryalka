from dataclasses import asdict
from .matching import CandidateIndex, normalize, RULES_VERSION
from .workbooks import write, json_text
from .adapter import CODES, valid_inn
from .matching import identifier
from . import __version__

NO_MATCH = 'Совпадение в доступных данных не найдено; полнота исходного перечня не подтверждена'
DETAIL_HEADERS = ['Строка Excel','Исходный текст клиента','Ключ записи','Индекс участника','Имя в перечне','Тип лица','Роль код','Роль','Характер связи','Правила','Дата отказа','Основание отказа','XML','SHA-256','Узел','ТипЗаписи','Значение кода','Состояние после пакета','Источник поиска','Нормализованный клиент','Нормализованный перечень','Ограничения','ID клиента','Расхождения реквизитов']


def entries(state):
    return [dict(p, key=key,record=d) for key,d in state.active.items() for p in d['participants']]


def event_entries(events, active, latest):
    return [dict(p,key=e.key,record=e.data,latest=e.source['sha256']==latest,active=e.key in active)
            for e in events for p in e.data['participants']]


def check_clients(clients, active_entries, changed_entries, fuzzy=True, progress=lambda x:None):
    current = CandidateIndex(active_entries,fuzzy)
    changed = CandidateIndex(changed_entries,fuzzy)
    active_results, changes, summary, quality = [], [], [], []
    matched_clients, direct, participant_only = set(),set(),set()
    for n,client in enumerate(clients):
        counts=[0,0]; has_direct=False; has_indirect=False; conflict=False; id_match=False
        seen=[set(),set()]
        for source_no,index in enumerate((current,changed)):
            for name in client['values']:
                for p,matched,rules in index.find(name):
                    record=p['record']; source=record['source']
                    identity=(tuple(p['key']),p['index'],source['sha256'],name,matched)
                    if identity in seen[source_no]: continue
                    seen[source_no].add(identity)
                    role = p['role_text']; is_direct=p['direct']
                    differences=[]
                    for field in ('inn','birth'):
                        left,right=client.get(field,''),p.get(field,'')
                        if left and right and identifier(left)!=identifier(right):
                            differences.append(f'{field}: клиент {left}; перечень {right}')
                    conflict |= bool(differences)
                    id_match |= bool(valid_inn(client.get('inn','')) and identifier(client.get('inn'))==identifier(p.get('inn')))
                    row=[str(client['row']),name,'|'.join(p['key']),str(p['index']),matched,p['type_text'],p['role'],role,
                         'Прямое упоминание клиента' if is_direct else f'Упоминание в операции с отказом, роль {role}',
                         json_text([asdict(rule) for rule in rules]),record['refusal_date'],' / '.join(record['refusal_codes']),
                         source['filename'],source['sha256'],source['node'],str(record['code']),CODES[record['code']],
                         'Действует' if source_no==0 or p.get('active') else 'Не действует',
                         'Действующее состояние' if source_no==0 else 'Последний XML' if p.get('latest') else 'Другой XML пакета',
                         normalize(name),normalize(matched),'Совпадение текста не подтверждает тождество лиц',client.get('id',''),'; '.join(differences)]
                    (active_results if source_no==0 else changes).append(row)
                    counts[source_no]+=1
                    matched_clients.add(client['row'])
                    has_direct |= is_direct; has_indirect |= not is_direct
        if has_direct: direct.add(client['row'])
        elif has_indirect: participant_only.add(client['row'])
        issues=list(client['issues'])
        if any(len(normalize(v).split()) < 2 for v in client['values']):
            issues.append('Короткое или неполное имя: выполнен поиск по доступным данным')
        if any(counts): status='Кандидат по имени'
        else: status='Не найдено'
        if id_match: status='Совпадение по идентификатору'
        if conflict: status='Конфликт реквизитов'
        if client['issues']: status='Ошибка или недостаточно данных'
        summary.append([str(client['row']),' / '.join(client['values']),status,str(counts[0]),str(counts[1]),'; '.join(issues),NO_MATCH if not any(counts) else 'Требуется ручная проверка',client.get('id',''),json_text(client.get('raw',{}))])
        quality.extend([str(client['row']),issue] for issue in issues)
        if n%100==0: progress(f'Проверено клиентов: {n+1} из {len(clients)}')
    return dict(clients=summary,active=active_results,changes=changes,quality=quality,
                matched=len(matched_clients),direct=len(direct),participant_only=len(participant_only))


def save_report01(path,summary,result,log):
    write(path,{
        'Сводка': (['Показатель','Значение'],list(summary.items())+[['Версия программы',__version__],['Версия правил',RULES_VERSION],['Состояние','Расчёт до фиксации. Фактический итог — в отчёте 02']]),
        'Клиенты': (['Строка Excel','Исходный текст','Статус','Действующих связей','Связей с изменениями','Качество','Ограничения','ID клиента','Все исходные реквизиты'],result['clients']),
        'ДействующиеСовпадения': (DETAIL_HEADERS,result['active']),
        'ИзмененияПоКлиентам': (DETAIL_HEADERS,result['changes']),
        'КачествоДанных': (['Строка или ключ','Описание'],result['quality']),
        'Журнал': (['Время','Этап','Уровень','Код','Описание'],log),
    })


def save_report02(path,summary,actions,files,log):
    write(path,{
        'Сводка': (['Показатель','Значение'],summary.items()),
        'Изменения': (['Ключ','ТипЗаписи','Значение кода','Действие','Причина','До','После','XML','SHA-256','Узел'],
                      (['|'.join(a['key']),str(a['code']),CODES[a['code']],a['action'],a.get('warning',''),json_text(a.get('before')),json_text(a.get('after')),a['source'].get('filename',''),a['source'].get('sha256',''),a['source'].get('node','')] for a in actions)),
        'Файлы': (['Имя','Дата','Номер','Версия','SHA-256','Статус','Причина'],([f.get('filename',''),f.get('order',['',''])[0],str(f.get('order',['',''])[1]),f.get('version',''),f.get('sha256',''),f.get('status',''),f.get('reason','')] for f in files)),
        'Аномалии': (['Ключ','Код','Описание'],(['|'.join(a['key']),str(a['code']),a['warning']] for a in actions if a.get('warning'))),
        'Журнал': (['Время','Этап','Уровень','Код','Описание'],log),
    })
