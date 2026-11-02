"""The XLSX workbook contains all canonical state; no external database is used."""
import hashlib
import json
from collections import Counter
from openpyxl import load_workbook
from .state import State
from .adapter import PROFILE, person_key
from .workbooks import write, rows, json_text

STRUCTURE_VERSION = '1'
MASTER_NAME = 'Перечень 764-П.xlsx'


def participant_rows(state):
    for key, data in state.active.items():
        for p in data['participants']:
            yield ['|'.join(key),str(p['index']),p.get('type',''),p.get('type_text',''),
                   ' / '.join(p.get('names',[])),p.get('inn',''),p.get('birth',''),p.get('document',''),
                   p.get('role',''),p.get('role_text',''),str(p.get('direct',False))]


def people(state):
    result = {}
    for key,data in state.active.items():
        for p in data['participants']:
            pid = person_key(key,p)
            entry = result.setdefault(pid,dict(names=set(),records=set(),roles=set(),types=set()))
            entry['names'].update(p.get('names',[])); entry['records'].add(key)
            entry['roles'].add(p.get('role_text','')); entry['types'].add(p.get('type_text',''))
    return result


def canonical_items(state, files):
    for key,data in state.active.items():
        yield 'active',list(key),data
    for key in sorted(state.closed):
        yield 'closed',list(key),None
    for i,entry in enumerate(state.history):
        yield 'history',i,entry
    for i,entry in enumerate(files):
        yield 'file',i,entry


def write_master(path,state,files,metadata):
    checksum = hashlib.sha256()
    view_hashes = {}
    def protect(name, source):
        h = hashlib.sha256()
        for row in source:
            row = [str(v) if v is not None else '' for v in row]
            row = [(f'Полное значение в Данные; SHA-256 {hashlib.sha256(v.encode()).hexdigest()}' if len(v.encode('utf-16-le'))>60000 else v) for v in row]
            h.update((json_text(row)+'\n').encode())
            yield row
        view_hashes[name] = h.hexdigest()
    def chunks():
        for n,item in enumerate(canonical_items(state,files)):
            text = json_text(item)
            checksum.update((text+'\n').encode())
            for i in range(0,len(text),10000):
                yield [str(n),str(i//10000),text[i:i+10000]]
    def active():
        for key,d in state.active.items():
            yield ['|'.join(key),key[0],key[1],d.get('number',key[2]),d.get('format',''),str(d.get('code','')),
                   d.get('refusal_date',''),' / '.join(d.get('refusal_codes',[])),' / '.join(d.get('reasons',[])),
                   d.get('source',{}).get('filename',''),d.get('source',{}).get('sha256',''),d.get('source',{}).get('node','')]
    def history():
        for e in state.history:
            yield ['|'.join(e['key']),str(e['code']),e['action'],e.get('warning',''),e['run_id'],
                   e.get('source',{}).get('filename',''),e.get('source',{}).get('sha256',''),e.get('source',{}).get('node',''),
                   'Данные: history; полный снимок до/после и событие']
    def meta():
        all_meta = dict(metadata,view_hashes=dict(view_hashes),structure=STRUCTURE_VERSION,profile=PROFILE,canonical_sha256=checksum.hexdigest(),
                        active_count=str(len(state.active)),history_count=str(len(state.history)),file_count=str(len(files)),
                        participant_count=str(sum(len(d['participants']) for d in state.active.values())))
        for key,value in all_meta.items():
            yield [key,json_text(value)]
    tables={
        'Данные': (['Объект','Часть','JSON'],chunks()),
        'АктивныеЗаписи': (['Ключ','ТипОрганизация','Раздел','НомерЗаписи','ФорматЗаписи','ТипЗаписи','ДатаОтказа','КодОтказа','Причины','XML','SHA-256','Узел'],active()),
        'Участники': (['Ключ','Индекс','Тип','Тип лица','Имя','ИНН','ДатаРождения','Документ','Роль','Расшифровка','Прямое упоминание'],participant_rows(state)),
        'ДействующиеЛица': (['Ключ лица','Имена','Действующих записей','Роли','Статусы'],([pid,' / '.join(sorted(p['names'])),str(len(p['records'])),' / '.join(sorted(p['roles'])),' / '.join(sorted(p['types']))] for pid,p in people(state).items())),
        'История': (['Ключ','Код','Действие','Предупреждение','run_id','XML','SHA-256','Узел','Снимки'],history()),
        'ОбработанныеФайлы': (['Дата','Номер','Версия','SHA-256','XML','Архив','run_id','Время','Событий'],([f['order'][0],str(f['order'][1]),f['version'],f['sha256'],f['filename'],f['archive'],f['run_id'],f['accepted_at'],str(f['events'])] for f in files)),
        'Метаданные': (['Ключ','Значение'],meta()),
    }
    for name,(headers,source) in list(tables.items()):
        if name not in ('Метаданные','Данные'):
            tables[name] = (headers,protect(name,source))
    write(path,tables)


def read_master(path):
    book = load_workbook(path,read_only=True,data_only=False,keep_links=False)
    try:
        metadata = {k:json.loads(v) for k,v in rows(book,'Метаданные')}
        if metadata.get('structure') != STRUCTURE_VERSION or metadata.get('profile') != PROFILE:
            raise ValueError('Неподдерживаемая структура или профиль накопленной базы')
        state, files = State(), []
        checksum = hashlib.sha256()
        current, chunks, part = None, [], 0
        def consume():
            if current is None:
                return
            text = ''.join(chunks); checksum.update((text+'\n').encode())
            kind,key,value = json.loads(text)
            if kind == 'active':
                if tuple(key) in state.active: raise ValueError('Повтор ключа в базе')
                state.active[tuple(key)] = value
            elif kind == 'closed': state.closed.add(tuple(key))
            elif kind == 'history':
                value['key'] = tuple(value['key']); state.history.append(value)
            elif kind == 'file': files.append(value)
            else: raise ValueError('Неизвестный объект базы')
        for object_id, chunk_id, value in rows(book,'Данные'):
            if object_id != current:
                consume(); current,chunks,part = object_id,[],0
            if int(chunk_id) != part: raise ValueError('Повреждена последовательность частей базы')
            chunks.append(value or ''); part += 1
        consume()
        if checksum.hexdigest() != metadata.get('canonical_sha256'):
            raise ValueError('Контрольная сумма данных базы не совпадает')
        checks={'АктивныеЗаписи':len(state.active),'Участники':sum(len(x['participants']) for x in state.active.values()),
                'История':len(state.history),'ОбработанныеФайлы':len(files),'ДействующиеЛица':len(people(state))}
        for name,count in checks.items():
            h=hashlib.sha256();actual_count=0
            for row in rows(book,name):
                h.update((json_text([str(v) if v is not None else '' for v in row])+'\n').encode());actual_count+=1
            if actual_count != count: raise ValueError(f'Нарушена структура базы: {name}')
            if h.hexdigest()!=metadata.get('view_hashes',{}).get(name):raise ValueError(f'Изменены данные листа {name}')
        if len(state.active)!=int(metadata['active_count']) or len(state.history)!=int(metadata['history_count']):
            raise ValueError('Нарушены контрольные счётчики базы')
        if set(state.active)&state.closed: raise ValueError('Запись одновременно активна и закрыта')
        return state,files,metadata
    except Exception as exc:
        raise ValueError(f'База повреждена или несовместима: {exc}') from exc
    finally:
        book.close()
