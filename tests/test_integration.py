import tempfile
import unittest
from pathlib import Path
from check764p.synthetic import message
from check764p.xml_input import schema,validate
from check764p.adapter import events
from check764p.engine import run
from check764p.excel import template
from check764p.master import read_master,MASTER_NAME
from check764p.transaction import digest
from check764p.matching import CandidateIndex,candidates

SCHEMAS=Path(__file__).resolve().parents[1]/'schemas'


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);template(self.root/'клиенты.xlsx')
        self.config={'clients':'клиенты.xlsx','sheet':'Клиенты','columns':['Наименование полное'],'fuzzy':False}

    def xml(self,number=1,records=None,version='1.2'):
        p=self.root/f'CB_ES550P_20261019_{number:03d}.XML';p.write_bytes(message(records,version));return p

    def run_app(self):
        return run(self.root,self.config,SCHEMAS)

    def test_all_sections_versions(self):
        validator=schema(SCHEMAS)
        for version in ['1.1','1.2']:
            for section in ['Раздел1.1','Раздел1.2','Раздел2']:
                for code in range(1,9 if section=='Раздел1.1' else 8):
                    with self.subTest(version=version,section=section,code=code):
                        raw=message([dict(section=section,code=code)],version)
                        root,order,h=validate(raw,'CB_ES550P_20261019_001.XML',validator)
                        ev=events(root,{'sha256':h,'filename':'synthetic.xml'})
                        self.assertEqual(ev[0].code,code)

    def test_end_to_end_repeat_and_close(self):
        self.xml(records=[dict(number=1),dict(number=2,names=['Примеров Тест Тестович','Другой Синтетический'])])
        first=self.run_app();self.assertTrue(first['ok'],first)
        state,files,meta=read_master(self.root/MASTER_NAME)
        self.assertEqual(len(state.active),2);self.assertEqual(len(files),1)
        old=digest(self.root/MASTER_NAME)
        second=self.run_app();self.assertTrue(second['ok'],second)
        self.assertEqual(old,digest(self.root/MASTER_NAME))
        self.xml(2,[dict(number=1,code=4)])
        third=self.run_app();self.assertTrue(third['ok'],third)
        state,files,_=read_master(self.root/MASTER_NAME)
        self.assertEqual(len(state.active),1);self.assertEqual(len(state.history),3)
        self.assertEqual(len(list((self.root/'backups').glob('*.xlsx'))),1)

    def test_bad_file_rolls_back_entire_package(self):
        self.xml();self.assertTrue(self.run_app()['ok'])
        old=digest(self.root/MASTER_NAME)
        self.xml(2,[dict(number=2)])
        (self.root/'CB_ES550P_20261019_003.XML').write_text('<broken>')
        result=self.run_app();self.assertFalse(result['ok'])
        self.assertEqual(old,digest(self.root/MASTER_NAME))
        self.assertTrue(all(Path(p).exists() for p in result['reports']))

    def test_duplicate_renamed(self):
        p=self.xml();self.assertTrue(self.run_app()['ok'])
        p.rename(self.root/'renamed.xml')
        self.assertTrue(self.run_app()['ok'])

    def test_empty_message(self):
        self.xml(records=[]);result=self.run_app();self.assertTrue(result['ok'],result)
        self.assertEqual(result['summary']['Записей после'],0)

    def test_no_data_reports_error(self):
        result=self.run_app();self.assertFalse(result['ok'])
        self.assertTrue(all(Path(p).exists() for p in result['reports']))

    def test_index_recall(self):
        names=['Тестов Сергей','TESTOV','ООО Пример','Примеров Тест Тестович','А','ТЕСТОВ','Тесотв','Стройинвест']
        entries=[{'names':[name],'id':i} for i,name in enumerate(names)]
        for fuzzy in [False,True]:
            index=CandidateIndex(entries,fuzzy)
            for query in names+['ООО «Строй-Инвест»','ТEСТОВ','NTCNJD']:
                actual={p['id'] for p,_,_ in index.find(query)}
                expected={i for i,n in enumerate(names) if candidates(query,n,fuzzy=fuzzy)}
                self.assertEqual(actual,expected,(query,fuzzy))

    def test_display_sheet_tamper_detected(self):
        from openpyxl import load_workbook
        self.xml();self.assertTrue(self.run_app()['ok'])
        p=self.root/MASTER_NAME;b=load_workbook(p)
        b['АктивныеЗаписи']['D2']='edited';b.save(p);b.close()
        with self.assertRaisesRegex(ValueError,'Изменены данные'):
            read_master(p)

    def test_changed_content_same_message_conflicts(self):
        self.xml();self.assertTrue(self.run_app()['ok'])
        before=digest(self.root/MASTER_NAME)
        self.xml(records=[dict(number=2)])
        result=self.run_app();self.assertFalse(result['ok'])
        self.assertEqual(before,digest(self.root/MASTER_NAME))

    def test_multiple_participants_all_matched(self):
        from openpyxl import load_workbook
        self.xml(records=[dict(names=['Примеров Тест Тестович','Примеров Тест Тестович'])])
        result=self.run_app();self.assertTrue(result['ok'],result)
        b=load_workbook(result['reports'][0],read_only=True)
        try:
            records=list(b['ДействующиеСовпадения'].iter_rows(min_row=2,values_only=True))
            self.assertEqual({r[3] for r in records},{'1','2'})
        finally:b.close()

    def test_different_ids_do_not_hide_name_match(self):
        from check764p.reports import check_clients
        from check764p.adapter import events
        parsed,_,h=validate(message(),'CB_ES550P_20261019_001.XML',schema(SCHEMAS))
        e=events(parsed,{'filename':'test.xml','sha256':h})[0]
        p=dict(e.data['participants'][0],key=e.key,record=e.data,inn='123456789012')
        results=check_clients([dict(row=4,values=['Примеров Тест Тестович'],issues=[],inn='987654321098')],[p],[],False)
        self.assertTrue(results['active'])
        self.assertEqual(results['clients'][0][2],'Конфликт реквизитов')

    def test_add_then_close_in_package_not_active(self):
        self.xml(1,[dict(number=1)])
        self.xml(2,[dict(number=1,code=5)])
        outcome=self.run_app();self.assertTrue(outcome['ok'],outcome)
        state,_,_=read_master(self.root/MASTER_NAME)
        self.assertFalse(state.active);self.assertEqual(len(state.history),2)
        from openpyxl import load_workbook
        b=load_workbook(outcome['reports'][0],read_only=True)
        try:self.assertEqual(len(list(b['ИзмененияПоКлиентам'].iter_rows(min_row=2))),2)
        finally:b.close()

    def test_calendar_date_rejected(self):
        raw=message(day='31/02/2026')
        with self.assertRaises(ValueError):
            validate(raw,'CB_ES550P_20260231_001.XML',schema(SCHEMAS))

    def test_schema_integrity(self):
        import shutil
        target=self.root/'schemas';shutil.copytree(SCHEMAS,target)
        with (target/'data_types.xsd').open('ab') as f:f.write(b' ')
        with self.assertRaisesRegex(ValueError,'Контрольная сумма'):
            schema(target)

    def test_streamed_report_overflow_keeps_full_text(self):
        from check764p.workbooks import write,rows
        from openpyxl import load_workbook
        text='Длинный синтетический текст '*2000
        path=self.root/'result.xlsx'
        write(path,{'Результаты':(['Текст'],[[text],['=2+2'],['00123']])},max_rows=3)
        b=load_workbook(path,read_only=True)
        try:
            chunks=list(rows(b,'ДлинныеЗначения'))
            self.assertEqual(''.join(row[2] for row in chunks),text)
            self.assertEqual(b['Результаты']['A3'].data_type,'s')
            self.assertEqual(b['Результаты_2']['A2'].value,'00123')
        finally:b.close()

    def test_mapped_extra_columns_and_hidden_rows(self):
        from openpyxl import load_workbook
        from check764p.excel import read_clients
        p=self.root/'клиенты.xlsx';b=load_workbook(p)
        b.active['C3']='Внутренний номер';b.active['C4']='000001';b.active.row_dimensions[4].hidden=True
        b.save(p);b.close()
        clients,_=read_clients(p,'Клиенты',['Наименование полное'],extra_columns={'id':'Внутренний номер'})
        self.assertEqual(clients[0]['id'],'000001')

    def test_dates_normalized_without_false_conflict(self):
        from check764p.reports import comparable_date
        self.assertEqual(comparable_date('02.01.2000'),comparable_date('02/01/2000'))
        self.assertEqual(comparable_date('2000-01-02 00:00:00'),comparable_date('02/01/2000'))

    def test_diagnostic_change_is_not_record_conflict(self):
        from copy import deepcopy
        from check764p.state import State,apply,Event
        a=Event('НФО','Раздел2','a',1,{'participants':[{'names':['Пример'],'quality':[]}]},{'sha256':'1'})
        state,_=apply(State(),[a],'r1')
        data=deepcopy(a.data);data['participants'][0]['quality']=['Новое диагностическое правило']
        b=Event('НФО','Раздел2','a',1,data,{'sha256':'2'})
        projected,actions=apply(state,[b],'r2')
        self.assertEqual(actions[0]['action'],'SKIP')
        self.assertEqual(len(projected.history),1)

    def test_record_number_case_does_not_create_conflict(self):
        from check764p.state import State,apply,Event
        a=Event('НФО','Раздел2','abc',1,{'number':'abc','participants':[{'names':['Пример']}],'fields':[{'name':'НомерЗаписи','value':'abc'}]},{'sha256':'1'})
        state,_=apply(State(),[a],'r1')
        b=Event('НФО','Раздел2','ABC',1,{'number':'ABC','participants':[{'names':['Пример']}],'fields':[{'name':'НомерЗаписи','value':'ABC'}]},{'sha256':'2'})
        projected,actions=apply(state,[b],'r2')
        self.assertEqual(actions[0]['action'],'SKIP')
        self.assertEqual(len(projected.history),1)

    def test_unknown_namespace_with_lost_name_is_not_silently_skipped(self):
        self.xml();self.assertTrue(self.run_app()['ok'])
        before=digest(self.root/MASTER_NAME)
        (self.root/'переименованный.xml').write_text('<СообщОтказ_115ФЗ xmlns="urn:unknown"/>',encoding='utf-8')
        result=self.run_app();self.assertFalse(result['ok'])
        self.assertEqual(before,digest(self.root/MASTER_NAME))
