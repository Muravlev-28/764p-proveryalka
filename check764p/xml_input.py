"""Strict offline XML boundary. Missing original schemas are a fatal error."""
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import re
from lxml import etree

HASHES = {
    'FSFM550P.xsd': 'd279885398e698b49348524e5d11307e8d39cef7ce5b82514e955dc8fc9b01ae',
    'data_types.xsd': 'fd9ccae67892a0935548195aa6af11534d4db8a288e98854608f6632a3e300f7',
}


def parse_bytes(raw):
    text = raw.decode('utf-8-sig')
    if '\x00' in text:
        raise ValueError('Требуется UTF-8; нулевые байты запрещены')
    if re.search(r'<!\s*(DOCTYPE|ENTITY)\b', text, re.I):
        raise ValueError('DTD и сущности запрещены')
    declaration = re.match(r'\s*<\?xml[^?]*encoding=[\"\']([^\"\']+)', text)
    if declaration and declaration[1].lower().replace('_', '-') != 'utf-8':
        raise ValueError('Требуется UTF-8')
    return etree.fromstring(raw, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))


class LocalResolver(etree.Resolver):
    def __init__(self, files):
        self.files = files

    def resolve(self, url, public_id, context):
        name = Path(url).name
        if name not in self.files or '://' in url:
            raise ValueError('Внешняя схема запрещена')
        return self.resolve_string(self.files[name], context)


def schema(directory):
    files = {}
    for name, digest in HASHES.items():
        path = Path(directory) / name
        if not path.is_file():
            raise ValueError(f'Отсутствует оригинальная схема {name}')
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != digest:
            raise ValueError(f'Контрольная сумма схемы не совпадает: {name}')
        files[name] = raw
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    parser.resolvers.add(LocalResolver(files))
    return etree.XMLSchema(etree.fromstring(files['FSFM550P.xsd'], parser))


def validate(raw, filename, validator):
    root = parse_bytes(raw)
    if root.tag != 'СообщОтказ_115ФЗ':
        raise ValueError('Неизвестный профиль XML')
    version = root.findtext('СлужЧасть/ВерсияФормата')
    if version not in ('1.1', '1.2'):
        raise ValueError('Неподдерживаемая версия')
    match = re.fullmatch(r'CB_ES550P_(\d{8})_(\d{3})\.[xX][mM][lL]', filename)
    if not match or int(match[2]) < 1:
        raise ValueError('Требуется исходное имя сообщения')
    day = datetime.strptime(root.findtext('СлужЧасть/ДатаСообщения'), '%d/%m/%Y').date()
    if day.strftime('%Y%m%d') != match[1]:
        raise ValueError('Дата имени и сообщения различается')
    validator.assertValid(root)
    return root, (day.isoformat(), int(match[2])), sha256(raw).hexdigest()


def order_messages(incoming, accepted):
    """Items have order=(ISO date, number), sha256; returns new items only."""
    hashes = {x['sha256'] for x in accepted}
    slots = {tuple(x['order']): x['sha256'] for x in accepted}
    latest = max(slots, default=None)
    pending = []
    for item in sorted(incoming, key=lambda x: x['order']):
        slot, digest = tuple(item['order']), item['sha256']
        if slot in slots and slots[slot] != digest:
            raise ValueError('Конфликт версий сообщения')
        if digest in hashes:
            continue
        if latest and slot <= latest:
            raise ValueError('Нарушена очередность сообщений')
        hashes.add(digest)
        slots[slot] = digest
        pending.append(item)
    days = {}
    for day, number in slots:
        days.setdefault(day, set()).add(number)
    for numbers in days.values():
        if numbers != set(range(1, max(numbers) + 1)):
            raise ValueError('Пропуск номера файла внутри дня')
    return pending
