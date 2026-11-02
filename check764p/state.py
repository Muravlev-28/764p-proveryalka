"""Pure transactional projection: the supplied state is never modified."""
from copy import deepcopy
from dataclasses import dataclass, field

EVENT_VERSION = '1.0'


@dataclass
class Event:
    organization: str
    section: str
    number: str
    code: int
    data: dict
    source: dict = field(default_factory=dict)

    @property
    def key(self):
        return (self.organization, self.section, self.number.upper())


@dataclass
class State:
    active: dict = field(default_factory=dict)
    closed: set = field(default_factory=set)
    history: list = field(default_factory=list)


def significant(data):
    if data is None:
        return None
    result = deepcopy(data)
    result.pop('source', None)
    result.pop('code', None)
    # XPath indexes and event codes are provenance, not substantive record data.
    for field in result.get('fields', []):
        field.pop('path', None)
    result['fields'] = [f for f in result.get('fields', []) if f['name'] != 'ТипЗаписи']
    for participant in result.get('participants', []):
        for diagnostic in ('quality','type_text','role_text'):
            participant.pop(diagnostic,None)
        for field in participant.get('fields', []):
            field.pop('path', None)
    return result


def apply(state, events, run_id):
    projected = deepcopy(state)
    seen = {}
    actions = []
    for e in events:
        valid_section = e.section in ('Раздел1.1', 'Раздел1.2') if e.organization == 'КО' else e.organization == 'НФО' and e.section == 'Раздел2'
        if not valid_section or e.code not in range(1, 9 if e.section == 'Раздел1.1' else 8):
            raise ValueError('Недопустимое сочетание раздела, организации и ТипЗаписи')
        identity = (e.source.get('sha256'), e.key)
        if identity in seen and seen[identity] != (e.code, significant(e.data)):
            raise ValueError('Конфликт изменений одной записи внутри файла')
        seen[identity] = (e.code, significant(e.data))
        old = projected.active.get(e.key)
        warning = ''
        if e.code in (1, 2):
            if e.key in projected.closed:
                raise ValueError('Повторное открытие закрытого ключа')
            if old is not None:
                if significant(old) != significant(e.data):
                    raise ValueError('Первичная запись конфликтует с действующей')
                actions.append(dict(action='SKIP', key=e.key, code=e.code, source=e.source, warning='DUPLICATE_RECORD', before=old, after=old, event=e.data, run_id=run_id))
                continue
            if not e.data.get('participants'):
                raise ValueError('Нет участников первичной записи')
            action = 'ADD'
            projected.active[e.key] = deepcopy(e.data)
        elif e.code == 3:
            if not e.data.get('participants'):
                raise ValueError('Неполная корректировка')
            action = 'UPDATE' if old is not None else 'ADD'
            if old is None:
                warning = 'CORRECTION_WITHOUT_PREVIOUS'
            projected.active[e.key] = deepcopy(e.data)
            projected.closed.discard(e.key)
        else:
            action = 'CLOSE' if old is not None else 'SKIP'
            if old is None:
                warning = 'CLOSE_NOT_FOUND'
            projected.active.pop(e.key, None)
            projected.closed.add(e.key)
        entry = dict(key=e.key, code=e.code, action=action, warning=warning,
                     before=deepcopy(old), after=deepcopy(projected.active.get(e.key)),
                     run_id=run_id, source=deepcopy(e.source), event=deepcopy(e.data))
        projected.history.append(entry)
        actions.append(entry)
    adds = sum(a['action'] == 'ADD' for a in actions)
    closes = sum(a['action'] == 'CLOSE' for a in actions)
    assert len(projected.active) == len(state.active) + adds - closes
    return projected, actions
