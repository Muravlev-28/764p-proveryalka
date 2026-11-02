"""Adapter for the original no-namespace 550P schema used by the 764-П workflow."""
from datetime import datetime
from .state import Event
from .matching import identifier

ROLES = {'0': 'Прямое упоминание клиента', '1': 'Плательщик, лицо, совершающее операцию',
         '2': 'Получатель', '3': 'Представитель плательщика', '4': 'Представитель получателя',
         '5': 'Лицо, действующее от имени и по поручению плательщика', '6': 'Выгодоприобретатель',
         '7': 'Лицо, действующее от имени и по поручению получателя'}
TYPES = {'1': 'ЮЛ или филиал', '2': 'ФЛ', '3': 'ИП', '4': 'Частная практика', '5': 'Иностранная структура без ЮЛ'}
CODES = {1: 'Первичное направление', 2: 'Первичное направление', 3: 'Корректировка',
         4: 'Удаление ошибочных сведений', 5: 'Устранение основания',
         6: 'Решение межведомственной комиссии', 7: 'Отмена решения судом',
         8: 'Отсутствие основания расторжения после судебной отмены'}
PROFILE = 'FSFM550P/no-namespace/1.1-1.2'


def leaf_fields(node, base_path=None):
    # Construct valid absolute XPath in linear time: getpath() repeatedly scans
    # all preceding siblings in large messages.
    base_path = base_path or node.getroottree().getpath(node)
    result=[]
    def walk(current,path):
        if len(current)==0:
            result.append(dict(path=path,name=current.tag,value=current.text or ''))
            return
        counters={}
        for child in current:
            if not isinstance(child.tag,str):continue
            counters[child.tag]=counters.get(child.tag,0)+1
            walk(child,f'{path}/{child.tag}[{counters[child.tag]}]')
    walk(node,base_path)
    return result


def participant(node, index, direct=False, path=None):
    kind = node.findtext('ТипКлиента') or node.findtext('ТипУчастника')
    expected = 'СведЮЛ' if kind == '1' else 'СведФЛИП' if kind in ('2', '3', '4') else 'СведИНБОЮЛ'
    blocks = [node.find(t) for t in ('СведЮЛ', 'СведФЛИП', 'СведИНБОЮЛ') if node.find(t) is not None]
    if len(blocks) != 1 or blocks[0].tag != expected:
        raise ValueError(f'Тип лица {kind} не согласован с блоком реквизитов')
    block = blocks[0]
    names = []
    for tag in ('НаимЮЛ', 'ФИОСтрока', 'ПолноеНаимИНБОЮЛ', 'НаимИНБОЮЛ'):
        text = block.findtext(tag)
        if text:
            names.append(text)
    fio = block.find('ФИОФЛИП')
    if fio is not None:
        text = ' '.join(fio.findtext(t) for t in ('Фам', 'Имя', 'Отч') if fio.findtext(t))
        if text:
            names.append(text)
    role = '0' if direct else node.findtext('СтатусУчастника')
    fields = leaf_fields(block, (path+'/'+expected+'[1]') if path else None)
    def first(*tags):
        return next((x['value'] for tag in tags for x in fields if x['name'] == tag and x['value']), '')
    return dict(index=index, type=kind, type_text=TYPES[kind], names=list(dict.fromkeys(names)),
                role=role, role_text=ROLES[role], direct=direct, fields=fields,
                inn=first('ИННЮЛ', 'ИННФЛИП'), ogrn=first('ОГРНЮЛ', 'ОГРНИП'),
                birth=first('ДатаРождения'), document='|'.join(first(t) for t in ('ВидДокКод','СерияДок','НомДок')),
                quality=([] if names else ['Имя отсутствует; поиск по имени недоступен']) + (['Некорректный ИНН; лицо не объединяется по этому идентификатору'] if first('ИННЮЛ','ИННФЛИП') and not valid_inn(first('ИННЮЛ','ИННФЛИП')) else []))


def events(root, source):
    organizations = set()
    result = []
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if element.text and len(element) == 0 and (element.tag.startswith('Дата') or element.tag == 'ДатВыдачиДок'):
            try:
                datetime.strptime(element.text, '%d/%m/%Y')
            except ValueError as exc:
                raise ValueError(f'Некорректная календарная дата: {element.tag}') from exc
    for block_index,block in enumerate(root.findall('ИнформЧасть'),1):
        org = block.findtext('ТипОрганизация')
        if org in organizations:
            raise ValueError('Повтор информационного блока одного типа организации')
        organizations.add(org)
        section_counts={}
        for node in block:
            if node.tag == 'ТипОрганизация':
                continue
            section = node.tag
            section_counts[section]=section_counts.get(section,0)+1
            node_path=f'/СообщОтказ_115ФЗ/ИнформЧасть[{block_index}]/{section}[{section_counts[section]}]'
            if (org == 'КО' and section not in ('Раздел1.1', 'Раздел1.2')) or (org == 'НФО' and section != 'Раздел2'):
                raise ValueError('Раздел не соответствует ТипОрганизация')
            code = int(node.findtext('ТипЗаписи'))
            if code not in range(1, 9 if section == 'Раздел1.1' else 8):
                raise ValueError('Недопустимый ТипЗаписи')
            participants = [participant(p, i, path=f'{node_path}/Участник[{i}]') for i, p in enumerate(node.findall('Участник'), 1)] if section == 'Раздел2' else [participant(node, 1, True, node_path)]
            provenance = dict(source, node=node_path)
            data = dict(number=node.findtext('НомерЗаписи'), format=node.findtext('ФорматЗаписи'),
                        code=code, participants=participants,
                        refusal_date=node.findtext('ДатаОтказа') or node.findtext('СведОперация/ДатаОтказа'),
                        refusal_codes=node.xpath('.//КодОтказа/text()'), reasons=node.xpath('.//КодПричинаОтказ/text()'),
                        removal_reason=node.findtext('ПричинаУдаления') or '', fields=leaf_fields(node,node_path), source=provenance)
            result.append(Event(org, section, data['number'], code, data, provenance))
    return result


def valid_inn(value):
    value = identifier(value)
    if not value.isascii() or not value.isdigit() or len(set(value)) == 1:
        return False
    def check(weights):
        return sum(int(x)*w for x,w in zip(value,weights)) % 11 % 10
    if len(value) == 10:
        return check([2,4,10,3,5,9,4,6,8]) == int(value[9])
    if len(value) == 12:
        return check([7,2,4,10,3,5,9,4,6,8]) == int(value[10]) and check([3,7,2,4,10,3,5,9,4,6,8]) == int(value[11])
    return False


def person_key(record_key, p):
    inn = identifier(p.get('inn'))
    if valid_inn(inn):
        return ('ЮЛ' if p['type'] == '1' else 'ФЛ') + ':' + inn
    # Absence of a reliable ID must never merge namesakes.
    return '|'.join(record_key) + ':' + str(p['index'])
