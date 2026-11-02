"""Full synthetic workflow benchmark. No real personal data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
from openpyxl import Workbook
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from check764p.synthetic import message
from check764p.engine import run


def name(i):
    # Fixed synthetic unique words avoid a combinatorial common-surname output.
    return 'SYN'+hashlib.sha256(str(i).encode()).hexdigest()[:20].upper()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--records',type=int,default=100000)
    parser.add_argument('--clients',type=int,default=20000);parser.add_argument('--output',default='docs/validation/benchmark.json')
    args=parser.parse_args()
    if hasattr(os,'sched_getaffinity'):
        available=sorted(os.sched_getaffinity(0));os.sched_setaffinity(0,available[:4])
    result={'records':args.records,'participants':args.records*3,'clients':args.clients,'fuzzy':False,
            'platform':platform.platform(),'python':platform.python_version(),'data':'synthetic unique names; three participants per record; one exact candidate per selected client'}
    with tempfile.TemporaryDirectory(prefix='764p-benchmark-',dir='/workspace' if os.name!='nt' else None) as temp:
        folder=Path(temp)
        book=Workbook(write_only=True);sheet=book.create_sheet('Клиенты')
        sheet.append(['Синтетический нагрузочный тест']);sheet.append([]);sheet.append(['Наименование полное'])
        for i in range(args.clients):sheet.append([name(i)])
        book.save(folder/'клиенты.xlsx')
        for start in range(0,args.records,10000):
            records=[dict(number=i+1,names=[name(i*3+j) for j in range(3)]) for i in range(start,min(args.records,start+10000))]
            (folder/f'CB_ES550P_20261019_{start//10000+1:03d}.XML').write_bytes(message(records))
        config=dict(clients='клиенты.xlsx',sheet='Клиенты',columns=['Наименование полное'],fuzzy=False)
        started=time.perf_counter()
        outcome=run(folder,config,Path(__file__).resolve().parents[1]/'schemas',lambda m:print(m,flush=True))
        result.update(seconds=round(time.perf_counter()-started,3),ok=outcome['ok'],summary=outcome['summary'],error=outcome.get('error'))
        if os.name!='nt':
            import resource
            result['peak_rss_mib']=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1)
        result['within_180_seconds']=result['seconds']<=180 and result['ok']
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='summary'},ensure_ascii=False),flush=True)
    return 0 if result['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
