"""Versioned candidate search. A match never establishes identity."""
import re
import unicodedata as ud
from collections import Counter
from dataclasses import dataclass

RULES_VERSION = '1.0'
NORMALIZATION_VERSION = '1.0'
OPF = ('ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ', 'ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО',
       'АКЦИОНЕРНОЕ ОБЩЕСТВО', 'ООО', 'ПАО', 'ОАО', 'ЗАО', 'АО', 'ИП')
TRANSLIT = dict(zip('АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ',
                   ['A','B','V','G','D','E','ZH','Z','I','Y','K','L','M','N','O','P','R','S','T','U','F','KH','TS','CH','SH','SHCH','','Y','','E','YU','YA']))
KEYS = str.maketrans('QWERTYUIOP[]ASDFGHJKL;\'ZXCVBNM,.', 'ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ')
LOOKALIKE = str.maketrans('ABCEHKMOPTXY', 'АВСЕНКМОРТХУ')


def normalize(value):
    if value is None:
        return ''
    text = ud.normalize('NFKC', str(value))
    text = ''.join(c for c in text if ud.category(c) != 'Cf' and (ud.category(c) != 'Cc' or c.isspace()))
    return ' '.join(text.upper().replace('Ё', 'Е').split())


def identifier(value):
    return ''.join(normalize(value).split())


def words(value):
    return re.findall(r'[^\W_]+', normalize(value), flags=re.UNICODE)


def company_words(value):
    text = ' '.join(words(value))
    for opf in OPF:
        text = re.sub(r'(?<!\w)' + re.escape(opf) + r'(?!\w)', ' ', text)
    return words(text) or words(value)


def representations(value):
    base = normalize(value)
    w = words(base)
    return {
        'BASE': base,
        'SEPARATORS': ' '.join(w),
        'JOINED': ''.join(w),
        'WORD_ORDER': ' '.join(sorted(w)),
        'NO_DIACRITICS': ''.join(c for c in ud.normalize('NFD', base) if not ud.combining(c)),
        'LOOKALIKE': base.translate(LOOKALIKE),
        'TRANSLITERATION': ''.join(TRANSLIT.get(c, c) for c in base),
        'KEYBOARD': base.translate(KEYS),
    }


def damerau(a, b):
    """Unrestricted Damerau–Levenshtein distance (not OSA)."""
    n, m = len(a), len(b)
    d = [[0] * (m + 2) for _ in range(n + 2)]
    d[0][0] = n + m
    for i in range(n + 1):
        d[i + 1][0], d[i + 1][1] = n + m, i
    for j in range(m + 1):
        d[0][j + 1], d[1][j + 1] = n + m, j
    seen = {}
    for i in range(1, n + 1):
        last = 0
        for j in range(1, m + 1):
            i1, j1 = seen.get(b[j - 1], 0), last
            cost = int(a[i - 1] != b[j - 1])
            if not cost:
                last = j
            d[i + 1][j + 1] = min(d[i][j] + cost, d[i + 1][j] + 1, d[i][j + 1] + 1,
                                     d[i1][j1] + i - i1 - 1 + 1 + j - j1 - 1)
        seen[a[i - 1]] = i
    return d[n + 1][m + 1]


@dataclass(frozen=True)
class Match:
    rule: str
    left: str
    right: str
    distance: int | None = None


def candidates(left, right, kind=None, fuzzy=True):
    if not normalize(left) or not normalize(right):
        return []
    result = []
    lrep, rrep = representations(left), representations(right)
    for rule, a in lrep.items():
        for other, b in rrep.items():
            if a and a == b:
                result.append(Match(f'{rule}/{other}', a, b))
    # Unknown type is searched as both a personal and a company name.
    for mode in (('person', 'company') if kind is None else (kind,)):
        a = company_words(left) if mode == 'company' else words(left)
        b = company_words(right) if mode == 'company' else words(right)
        if mode == 'company':
            for rule, x in representations(' '.join(a)).items():
                for other, y in representations(' '.join(b)).items():
                    if x and x == y:
                        result.append(Match(f'company:{rule}/{other}', x, y))
        # Ambiguous personal-name order: examine each possible surname component.
        for x in a:
            for y in b:
                if x == y:
                    result.append(Match(f'{mode}:TOKEN', x, y, 0))
                elif fuzzy:
                    distance = damerau(x, y)
                    threshold = 1 if min(len(x), len(y)) <= 3 else 2 if min(len(x), len(y)) <= 7 else 3
                    if distance <= threshold:
                        result.append(Match(f'{mode}:TYPO', x, y, distance))
        if mode == 'company' and Counter(a) == Counter(b):
            result.append(Match('company:WITHOUT_OPF', ' '.join(a), ' '.join(b)))
    # Additional independent token passes preserve surname recall under transliteration,
    # mixed scripts and a wrong keyboard layout even when other name parts differ.
    transforms=('NO_DIACRITICS','LOOKALIKE','TRANSLITERATION','KEYBOARD')
    for lr in ('BASE',)+transforms:
        for rr in ('BASE',)+transforms:
            if lr==rr=='BASE':continue
            for x in words(lrep[lr]):
                for y in words(rrep[rr]):
                    if x==y:
                        result.append(Match(f'ADDITIONAL_TOKEN:{lr}/{rr}',x,y,0))
    return list(dict.fromkeys(result))


def search_keys(value):
    """Exact inverted keys are a superset of every non-fuzzy rule above."""
    reps = set(representations(value).values()) | set(representations(' '.join(company_words(value))).values())
    return {('representation', x) for x in reps if x} | {('token', x) for text in reps for x in words(text)}


class CandidateIndex:
    """No top-k limit. Fuzzy token lookup uses a metric BK tree."""
    def __init__(self, entries, fuzzy=True):
        from collections import defaultdict
        self.entries = entries
        self.postings = defaultdict(set)
        self.fuzzy = fuzzy
        self.tree = None
        self.cache = {}
        self.names = {}
        for i, entry in enumerate(entries):
            for name in entry['names']:
                self.names.setdefault(name, set()).add(i)
        self.name_list = list(self.names)
        for n, name in enumerate(self.name_list):
            for key in search_keys(name):
                self.postings[key].add(n)
        if fuzzy:
            for typ, word in self.postings:
                if typ == 'token':
                    self._insert(word)

    def _insert(self, word):
        if self.tree is None:
            self.tree = (word, {})
            return
        node = self.tree
        while True:
            distance = damerau(word, node[0])
            if not distance:
                return
            if distance not in node[1]:
                node[1][distance] = (word, {})
                return
            node = node[1][distance]

    def _near(self, word):
        if word in self.cache:
            return self.cache[word]
        found, stack = [], [self.tree] if self.tree else []
        while stack:
            candidate, children = stack.pop()
            distance = damerau(word, candidate)
            shortest = min(len(word), len(candidate))
            threshold = 1 if shortest <= 3 else 2 if shortest <= 7 else 3
            if distance <= threshold:
                found.append(candidate)
            radius = 1 if len(word) <= 3 else 2 if len(word) <= 7 else 3
            stack.extend(node for length, node in children.items() if distance-radius <= length <= distance+radius)
        self.cache[word] = found
        return found

    def find(self, value):
        possible = set()
        for key in search_keys(value):
            possible.update(self.postings.get(key, ()))
            if self.fuzzy and key[0] == 'token':
                for near in self._near(key[1]):
                    possible.update(self.postings.get(('token', near), ()))
        for n in sorted(possible):
            name = self.name_list[n]
            rules = candidates(value, name, fuzzy=self.fuzzy)
            if rules:
                for i in sorted(self.names[name]):
                    yield self.entries[i], name, rules

try:
    from rapidfuzz.distance.DamerauLevenshtein import distance as damerau
except ImportError:
    pass  # Reference implementation above is also exercised by source-only users.
