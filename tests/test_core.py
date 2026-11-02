import tempfile
import unittest
from pathlib import Path
from copy import deepcopy
from openpyxl import load_workbook
from lxml import etree
from check764p.matching import normalize, identifier, candidates, damerau, representations
from check764p.state import Event, State, apply
from check764p.xml_input import parse_bytes, schema, validate, order_messages
from check764p.excel import template, read_clients, write_book


class MatchingTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize('  СЕМЁН\u00a0\u200b  ТЕСТОВ\t'), 'СЕМЕН ТЕСТОВ')
        self.assertEqual(identifier(' 00 123 '), '00123')

    def test_control_pairs(self):
        pairs = [
            ('Семён Тестов', 'СЕМЕН ТЕСТОВ'),
            ('ООО «Строй-Инвест»', 'Стройинвест'),
            ('Тестов Сергей Петрович', 'Петрович Тестов Сергей'),
            ('Тестов Сергей', 'Тестов Сергей Петрович'),
            ('Тестов С. П.', 'Тестов Сергей Петрович'),
            ('TESTOV', 'Тестов'),
            ('ТEСТОВ', 'ТЕСТОВ'),
            ('NTCNJD', 'ТЕСТОВ'),
            ('Тестов', 'Тесотв'),
            ('Общество с ограниченной ответственностью Пример', 'ООО Пример'),
            ('Пример 123', 'Пример 456'),
            ('Тестов', 'Тестов Андрей'),
        ]
        for a, b in pairs:
            with self.subTest(a=a, b=b):
                self.assertTrue(candidates(a, b))

    def test_empty_never_matches(self):
        for a in ('', ' ', None, '\u200b'):
            self.assertFalse(candidates(a, ''))

    def test_damerau(self):
        self.assertEqual(damerau('CA', 'ABC'), 2)
        self.assertEqual(damerau('ТЕСТОВ', 'ТЕСОТВ'), 1)
        self.assertEqual(damerau('', 'А'), 1)

    def test_repeated_words_retained(self):
        self.assertNotEqual(representations('А А Б')['WORD_ORDER'], representations('А Б')['WORD_ORDER'])

    def test_no_top_k_limit(self):
        results = [x for x in range(100) if candidates('Пример', f'ООО Пример {x}', fuzzy=False)]
        self.assertEqual(len(results), 100)


class StateTests(unittest.TestCase):
    def event(self, code=1, number='abc', source='a', name='Тестов'):
        return Event('НФО', 'Раздел2', number, code, {'participants': [{'name': name}]}, {'sha256': source})

    def test_add_and_close_preserves_other_record(self):
        a, b = self.event(), self.event(number='def')
        state, _ = apply(State(), [a, b], 'run1')
        result, _ = apply(state, [self.event(4, source='b')], 'run2')
        self.assertEqual(len(state.active), 2)
        self.assertEqual(list(result.active), [b.key])
        self.assertEqual(len(result.history), 3)

    def test_correction_replaces_participants(self):
        s, _ = apply(State(), [self.event()], 'a')
        e = self.event(3, source='b', name='Другой пример')
        s, actions = apply(s, [e], 'b')
        self.assertEqual(s.active[e.key], e.data)
        self.assertEqual(actions[0]['action'], 'UPDATE')
        self.assertEqual(actions[0]['before']['participants'][0]['name'], 'Тестов')

    def test_duplicate_no_history(self):
        s, _ = apply(State(), [self.event()], 'a')
        t, actions = apply(s, [self.event(source='b')], 'b')
        self.assertEqual(s, t)
        self.assertEqual(actions[0]['action'], 'SKIP')

    def test_atomic_conflict(self):
        s = State()
        old = deepcopy(s)
        with self.assertRaises(ValueError):
            apply(s, [self.event(), self.event(name='Другой')], 'r')
        self.assertEqual(s, old)

    def test_no_silent_reopening(self):
        s, _ = apply(State(), [self.event(4)], 'a')
        with self.assertRaises(ValueError):
            apply(s, [self.event(source='b')], 'b')

    def test_missing_correction(self):
        s, actions = apply(State(), [self.event(3)], 'r')
        self.assertEqual(actions[0]['warning'], 'CORRECTION_WITHOUT_PREVIOUS')
        self.assertEqual(len(s.active), 1)

    def test_missing_close(self):
        s, actions = apply(State(), [self.event(7)], 'r')
        self.assertFalse(s.active)
        self.assertEqual(actions[0]['warning'], 'CLOSE_NOT_FOUND')

    def test_invalid_code_and_org(self):
        for e in [self.event(8), Event('НФО','Раздел1.1','a',1,{})]:
            with self.assertRaises(ValueError):
                apply(State(), [e], 'r')

    def test_all_close_codes(self):
        for code in range(4, 9):
            a = Event('КО', 'Раздел1.1', 'abc', 1, {'participants': [{'name': 'Пример'}]})
            s, _ = apply(State(), [a], 'a')
            b = Event('КО', 'Раздел1.1', 'ABC', code, {}, {'sha256': 'b'})
            s, _ = apply(s, [b], 'b')
            self.assertFalse(s.active)


class XMLTests(unittest.TestCase):
    def test_dtd_forbidden(self):
        with self.assertRaises(ValueError):
            parse_bytes(b'<!DOCTYPE a [<!ENTITY secret SYSTEM "file:///etc/passwd">]><a>&secret;</a>')

    def test_malformed(self):
        with self.assertRaises(etree.XMLSyntaxError):
            parse_bytes(b'<a>')

    def test_utf8_bom(self):
        self.assertEqual(parse_bytes(b'\xef\xbb\xbf<a/>').tag, 'a')

    def test_wrong_encoding(self):
        with self.assertRaises(ValueError):
            parse_bytes(b'<?xml version="1.0" encoding="windows-1251"?><a/>')

    def test_missing_schema_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'FSFM550P'):
                schema(d)

    def test_unknown_namespace(self):
        with self.assertRaisesRegex(ValueError, 'профиль'):
            validate('<СообщОтказ_115ФЗ xmlns="urn:unknown"/>'.encode(), 'CB_ES550P_20261019_001.XML', None)

    def test_sequence(self):
        def msg(n, sha=None): return {'order': ('2026-10-19', n), 'sha256': sha or str(n)}
        self.assertEqual(order_messages([msg(2), msg(1)], []), [msg(1), msg(2)])
        self.assertEqual(order_messages([msg(1)], [msg(1)]), [])
        for incoming, accepted in [([msg(3)],[msg(1)]), ([msg(1,'other')],[msg(1)]), ([msg(1)], [msg(2)])]:
            with self.assertRaises(ValueError):
                order_messages(incoming, accepted)


class ExcelTests(unittest.TestCase):
    def test_template_and_input(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'клиенты.xlsx'
            template(p)
            rows, _ = read_clients(p, 'Клиенты', ['Наименование полное'])
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['row'], 4)

    def test_hidden_rows_and_formulas(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'клиенты.xlsx'
            template(p)
            b = load_workbook(p)
            b.active.row_dimensions[4].hidden = True
            b.active['B5'] = '=1+1'
            b.save(p)
            b.close()
            rows, _ = read_clients(p, 'Клиенты', ['Наименование полное'])
            self.assertEqual(len(rows), 2)
            self.assertTrue(rows[1]['issues'])

    def test_report_text_and_splitting(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'отчёт.xlsx'
            write_book(p, {'Результат': (['Значение'], [['=1+1'], ['+123'], ['00123'], ['@example']])}, max_rows=3)
            b = load_workbook(p)
            self.assertIn('Результат_2', b.sheetnames)
            self.assertEqual(b['Результат']['A2'].data_type, 's')
            self.assertEqual(b['Результат_2']['A2'].value, '00123')
            b.close()

    def test_no_silent_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                write_book(Path(d)/'a.xlsx', {'Лист': (['a'], [['x'*32768]])})


if __name__ == '__main__':
    unittest.main()
