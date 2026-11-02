import os
import tempfile
import unittest
from pathlib import Path
from check764p.transaction import Transaction, DirectoryLock, digest


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, text in [('master.xlsx','old'),('new.xlsx','new'),('report.xlsx','report'),('input.xml','input')]:
            (self.root/name).write_text(text)
        self.tx = Transaction(self.root)

    def prepare(self):
        return self.tx.prepare('run1','master.xlsx','new.xlsx',[('report.xlsx','results/report.xlsx')],{'input.xml':digest(self.root/'input.xml')})

    def test_commit(self):
        self.prepare()
        self.assertEqual(self.tx.commit(),'COMMITTED')
        self.assertEqual((self.root/'backups/run1.xlsx').read_text(),'old')
        self.assertEqual((self.root/'results/report.xlsx').read_text(),'report')

    def test_crash_after_replace(self):
        self.prepare()
        os.replace(self.root/'new.xlsx',self.root/'master.xlsx')
        self.assertEqual(self.tx.recover(),'COMMITTED')
        self.assertTrue((self.root/'results/report.xlsx').exists())
        self.assertEqual(self.tx.recover(),'NOTHING')

    def test_crash_before_replace(self):
        self.prepare()
        self.assertEqual(self.tx.recover(),'ABORTED')
        self.assertEqual((self.root/'master.xlsx').read_text(),'old')

    def test_input_change_blocks(self):
        self.prepare()
        (self.root/'input.xml').write_text('changed')
        with self.assertRaises(RuntimeError): self.tx.commit()
        self.assertEqual((self.root/'master.xlsx').read_text(),'old')

    def test_report_change_blocks(self):
        self.prepare()
        (self.root/'report.xlsx').write_text('changed')
        with self.assertRaises(RuntimeError): self.tx.commit()
        self.assertEqual((self.root/'master.xlsx').read_text(),'old')

    def test_unknown_master_blocks_recovery(self):
        self.prepare()
        (self.root/'master.xlsx').write_text('foreign')
        with self.assertRaises(RuntimeError): self.tx.recover()

    def test_path_escape(self):
        with self.assertRaises(ValueError): self.tx.path('../outside')

    def test_exclusive_lock(self):
        with DirectoryLock(self.root):
            with self.assertRaises(RuntimeError):
                with DirectoryLock(self.root): pass
        with DirectoryLock(self.root): pass

    def test_lost_report_rolls_back_master(self):
        self.prepare()
        os.replace(self.root/'new.xlsx',self.root/'master.xlsx')
        (self.root/'report.xlsx').unlink()
        self.assertEqual(self.tx.recover(),'ROLLED_BACK')
        self.assertEqual((self.root/'master.xlsx').read_text(),'old')
