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
