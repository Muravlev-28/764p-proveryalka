"""Recoverable multi-file publication in a single working directory.

Call only while holding DirectoryLock. Stage every mandatory file first.
The master replacement is the commit point; report publication follows.
Recovery rolls forward if its digest is the new one; otherwise it aborts.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def durable_json(path, data):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class DirectoryLock:
    """OS lock is automatically released on process exit; lock file is retained."""
    def __init__(self, root):
        self.path = Path(root) / 'service' / 'run.lock'
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b'0')
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise RuntimeError('В этой папке уже выполняется проверка') from exc
        return self

    def __exit__(self, *args):
        self.stream.close()


class Transaction:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.journal = self.root / 'service' / 'transaction.json'
        self.journal.parent.mkdir(parents=True, exist_ok=True)

    def path(self, relative):
        p = (self.root / relative).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError('Путь выходит за пределы рабочей папки')
        return p

    def prepare(self, run_id, master, prepared_master, reports, inputs):
        if self.journal.exists():
            raise RuntimeError('Сначала восстановите незавершённую транзакцию')
        if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in run_id):
            raise ValueError('Небезопасный run_id')
        old = self.path(master)
        prepared = self.path(prepared_master)
        data = dict(run_id=run_id, master=master, prepared=prepared_master,
                    before=digest(old) if old.exists() else None, after=digest(prepared),
                    reports=[], inputs=inputs, stage='PREPARED')
        for source, target in reports:
            data['reports'].append(dict(source=source, target=target, sha256=digest(self.path(source))))
        for name, expected in inputs.items():
            if digest(self.path(name)) != expected:
                raise RuntimeError(f'Входной файл изменился: {name}')
        for report in data['reports']:
            if self.path(report['target']).exists():
                raise FileExistsError(report['target'])
        data['backup'] = f'backups/{run_id}.xlsx' if old.exists() else None
        if data['backup']:
            backup = self.path(data['backup'])
            backup.parent.mkdir(exist_ok=True)
            with backup.open('xb') as dest, old.open('rb') as src:
                shutil.copyfileobj(src, dest)
                dest.flush()
                os.fsync(dest.fileno())
            if digest(backup) != data['before']:
                raise RuntimeError('Контрольная сумма резервной копии не совпадает')
        durable_json(self.journal, data)
        return data

    def commit(self):
        data = json.loads(self.journal.read_text(encoding='utf-8'))
        master = self.path(data['master'])
        for name, expected in data['inputs'].items():
            if digest(self.path(name)) != expected:
                raise RuntimeError(f'Входной файл изменился: {name}')
        if (digest(master) if master.exists() else None) != data['before']:
            raise RuntimeError('Исходная база изменилась')
        if digest(self.path(data['prepared'])) != data['after']:
            raise RuntimeError('Проект базы изменился')
        for report in data['reports']:
            if digest(self.path(report['source'])) != report['sha256']:
                raise RuntimeError('Подготовленный отчёт изменился')
        os.replace(self.path(data['prepared']), master)
        return self.recover()

    def recover(self):
        if not self.journal.exists():
            return 'NOTHING'
        data = json.loads(self.journal.read_text(encoding='utf-8'))
        master = self.path(data['master'])
        actual = digest(master) if master.exists() else None
        if actual == data['after']:
            for report in data['reports']:
                source, target = self.path(report['source']), self.path(report['target'])
                if target.exists():
                    if digest(target) != report['sha256']:
                        raise RuntimeError('Конфликт опубликованного отчёта; требуется восстановление из копии')
                    continue
                if not source.exists() or digest(source) != report['sha256']:
                    backup = self.path(data['backup']) if data.get('backup') else None
                    if backup is not None:
                        if not backup.exists() or digest(backup) != data['before']:
                            raise RuntimeError('Утрачены отчёт и проверенная резервная копия')
                        recovery = self.path('service/recovery-master.xlsx')
                        shutil.copyfile(backup, recovery)
                        os.replace(recovery, master)
                    else:
                        # Preserve the replaced new master for investigation; restore absence.
                        os.replace(master, self.path('service/' + data['run_id'] + '-unpublished.xlsx'))
                    # Already published success reports must not remain presented as successful.
                    for previous in data['reports']:
                        previous_target=self.path(previous['target'])
                        if previous_target.exists() and digest(previous_target)==previous['sha256']:
                            os.replace(previous_target,self.path('service/' + data['run_id'] + '-' + previous_target.name))
                    data['stage']='ROLLED_BACK'
                    durable_json(self.journal,data)
                    history=self.root/'service'/'transactions';history.mkdir(exist_ok=True)
                    os.replace(self.journal,history/(data['run_id']+'.json'))
                    return 'ROLLED_BACK'

                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
            outcome = 'COMMITTED'
        elif actual == data['before']:
            outcome = 'ABORTED'
        else:
            raise RuntimeError('База изменена вне транзакции; автоматическое восстановление остановлено')
        data['stage'] = outcome
        durable_json(self.journal, data)
        history = self.root / 'service' / 'transactions'
        history.mkdir(exist_ok=True)
        os.replace(self.journal, history / (data['run_id'] + '.json'))
        return outcome
