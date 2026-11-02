"""Windows desktop entry point. Working directory is always the EXE directory."""
import argparse
import json
import os
from pathlib import Path
import queue
import sys
import threading

# PyInstaller script entry point needs absolute imports.
from check764p.engine import run
from check764p.master import MASTER_NAME,read_master
from check764p.transaction import durable_json,digest


def locations():
    if getattr(sys,'frozen',False):
        return Path(sys.executable).resolve().parent,Path(sys._MEIPASS)/'schemas'
    return Path(__file__).resolve().parents[1],Path(__file__).resolve().parents[1]/'schemas'


def gui(rootdir,schemas):
    import tkinter as tk
    from tkinter import ttk,filedialog,messagebox
    from openpyxl import load_workbook
    window=tk.Tk();window.title('Проверка 764-П');window.geometry('760x410');window.minsize(650,360)
    status=tk.StringVar(value='Подготовка проверки…')
    ttk.Label(window,text='Проверка клиентов по сведениям 764-П',font=('Segoe UI',16)).pack(anchor='w',padx=20,pady=16)
    ttk.Label(window,textvariable=status,wraplength=710).pack(anchor='w',padx=20,pady=8)
    bar=ttk.Progressbar(window,mode='indeterminate');bar.pack(fill='x',padx=20,pady=8)
    description=tk.Text(window,height=8,wrap='word');description.pack(fill='both',expand=True,padx=20,pady=8)
    description.insert('end','Совпадение текста требует ручной проверки и не устанавливает тождество лиц.\nВсе данные остаются в рабочей папке.\n')
    buttons=ttk.Frame(window);buttons.pack(fill='x',padx=20,pady=12)
    report_button=ttk.Button(buttons,text='Открыть папку отчётов',state='disabled');report_button.pack(side='left')
    config_button=ttk.Button(buttons,text='Настройки и повторная проверка');config_button.pack(side='left',padx=8)
    ttk.Button(buttons,text='Закрыть',command=lambda:close()).pack(side='right')
    settings_path=rootdir/'service'/'settings.json';messages=queue.Queue();busy=False

    def choose_settings(existing):
        dialog=tk.Toplevel(window);dialog.title('Настройка проверки');dialog.geometry('730x590');dialog.transient(window);dialog.grab_set()
        filename=tk.StringVar(value=existing.get('clients',''))
        sheet=tk.StringVar(value=existing.get('sheet',''))
        header=tk.IntVar(value=existing.get('header_row',3));fuzzy=tk.BooleanVar(value=existing.get('fuzzy',True))
        result={};book_sheets=[];headers=[]
        ttk.Label(dialog,text='Книга клиентов рядом с программой').pack(anchor='w',padx=15,pady=6)
        line=ttk.Frame(dialog);line.pack(fill='x',padx=15)
        ttk.Entry(line,textvariable=filename).pack(side='left',fill='x',expand=True)
        sheet_box=ttk.Combobox(dialog,textvariable=sheet,state='readonly');sheet_box.pack(fill='x',padx=15,pady=10)
        rowline=ttk.Frame(dialog);rowline.pack(fill='x',padx=15)
        ttk.Label(rowline,text='Строка заголовков:').pack(side='left')
        ttk.Spinbox(rowline,from_=1,to=10000,textvariable=header,width=8).pack(side='left',padx=8)
        ttk.Label(dialog,text='Выберите один или несколько столбцов ФИО / наименований (Ctrl / Shift):').pack(anchor='w',padx=15,pady=6)
        listbox=tk.Listbox(dialog,selectmode='extended',exportselection=False,height=9);listbox.pack(fill='both',expand=True,padx=15)
        def refresh_headers(*args):
            nonlocal headers
            try:
                book=load_workbook(rootdir/filename.get(),read_only=True,data_only=False)
                try:
                    if not sheet.get():return
                    headers=[c.value for c in next(book[sheet.get()].iter_rows(min_row=header.get(),max_row=header.get()))]
                finally:book.close()
                listbox.delete(0,'end')
                for h in headers:listbox.insert('end',str(h or '(пустой заголовок)'))
                for i,h in enumerate(headers):
                    if h in existing.get('columns',['Наименование полное']):listbox.selection_set(i)
            except Exception as exc:messagebox.showerror('Чтение заголовков',str(exc),parent=dialog)
        def load_book():
            try:
                book=load_workbook(rootdir/filename.get(),read_only=True,data_only=False)
                try:book_sheets=book.sheetnames
                finally:book.close()
                sheet_box['values']=book_sheets
                if sheet.get() not in book_sheets:sheet.set(book_sheets[0])
                refresh_headers()
            except Exception as exc:messagebox.showerror('Книга клиентов',str(exc),parent=dialog)
        def browse():
            selected=filedialog.askopenfilename(parent=dialog,initialdir=rootdir,filetypes=[('Excel','*.xlsx')])
            if not selected:return
            path=Path(selected).resolve()
            if path.parent!=rootdir:
                messagebox.showerror('Рабочая папка','Поместите книгу клиентов рядом с программой и выберите её снова.',parent=dialog);return
            filename.set(path.name);load_book()
        ttk.Button(line,text='Выбрать…',command=browse).pack(side='right')
        ttk.Button(rowline,text='Прочитать заголовки',command=refresh_headers).pack(side='left')
        sheet_box.bind('<<ComboboxSelected>>',refresh_headers)
        ttk.Checkbutton(dialog,text='Нечёткий поиск опечаток (для больших перечней может быть медленным)',variable=fuzzy).pack(anchor='w',padx=15,pady=8)
        ttk.Label(dialog,text='Тип лица, ИНН и ID клиента не обязательны. Поиск выполняется по выбранным текстовым колонкам.',wraplength=690).pack(anchor='w',padx=15)
        def save():
            selected=[headers[i] for i in listbox.curselection()]
            if not selected or any(not h or headers.count(h)!=1 for h in selected):
                messagebox.showerror('Колонки','Выберите непустые уникальные заголовки.',parent=dialog);return
            result.update(existing,clients=filename.get(),sheet=sheet.get(),columns=selected,header_row=header.get(),fuzzy=fuzzy.get())
            dialog.destroy()
        ttk.Button(dialog,text='Сохранить и проверить',command=save).pack(pady=15)
        if filename.get():load_book()
        window.wait_window(dialog)
        return result or None

    def initial_settings(config):
        if (rootdir/MASTER_NAME).exists() or config.get('initial_master'):return config
        choice=messagebox.askyesnocancel('Первоначальная база','Импортировать существующую накопленную книгу Check764P?\n\nДа — выбрать базу.\nНет — начать частичное накопление из XML в рабочей папке.\nОтмена — остановить проверку.',parent=window)
        if choice is None:return None
        if not choice:return config
        filename=filedialog.askopenfilename(initialdir=rootdir,title='Накопленная база Check764P',filetypes=[('Excel','*.xlsx')])
        if not filename:return None
        selected=Path(filename).resolve()
        if selected.parent!=rootdir:
            messagebox.showerror('Импорт','Скопируйте базу под другим именем и её archive/xml в рабочую папку. Затем выберите книгу.',parent=window);return None
        try:
            state,files,meta=read_master(selected)
            for f in files:
                if digest(rootdir/f['archive'])!=f['sha256']:raise ValueError('Архив не соответствует базе')
        except Exception as exc:
            messagebox.showerror('Импорт','Эта книга не подходит для безопасного накопления.\n'+str(exc),parent=window);return None
        config['initial_master']=selected.name
        if messagebox.askyesno('Покрытие','Ответственный пользователь подтверждает полноту импортируемой базовой версии?',parent=window):
            from tkinter.simpledialog import askstring
            reason=askstring('Происхождение','Укажите источник базовой версии и основание подтверждения полноты:',parent=window)
            if not reason:return None
            config.update(confirmed_complete=True,confirmation=reason)
        return config

    def start(force=False):
        nonlocal busy
        if busy:return
        try:
            config=json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
        except Exception:config={}
        if not config:
            options=sorted(rootdir.glob('клиенты*.xlsx')) or sorted(rootdir.glob('clients*.xlsx'))
            if len(options)==1:config['clients']=options[0].name
        if force or not all(k in config for k in ('clients','sheet','columns')) or not (rootdir/config['clients']).is_file():
            config=choose_settings(config)
        if not config:status.set('Проверка не запущена. Откройте настройки, чтобы продолжить.');return
        config=initial_settings(config)
        if not config:status.set('Первоначальная загрузка отменена.');return
        try:
            settings_path.parent.mkdir(parents=True,exist_ok=True);durable_json(settings_path,config)
        except OSError as exc:
            messagebox.showerror('Сохранение настроек',str(exc),parent=window);return
        busy=True;bar.start();config_button.config(state='disabled')
        def worker():
            try:outcome=run(rootdir,config,schemas,lambda message:messages.put(('progress',message)))
            except Exception as exc:outcome={'ok':False,'error':str(exc),'summary':{},'reports':[]}
            messages.put(('done',outcome))
        threading.Thread(target=worker,daemon=False).start()

    def poll():
        nonlocal busy
        try:
            while True:
                kind,data=messages.get_nowait()
                if kind=='progress':status.set(data)
                else:
                    busy=False;bar.stop();config_button.config(state='normal')
                    status.set('Проверка завершена' if data['ok'] else 'Проверка завершилась с ошибкой')
                    description.delete('1.0','end')
                    keys=['run_id','Итог','Новых XML','Добавлено','Скорректировано','Клиентов с совпадениями','Проблемных строк']+[f'Закрыто код {i}' for i in range(4,9)]
                    description.insert('end','\n'.join(f'{k}: {data["summary"][k]}' for k in keys if k in data['summary']))
                    if not data['ok']:description.insert('end','\n'+data['error'])
                    description.insert('end','\n\n'+'\n'.join(data['reports']))
                    folder=Path(data['reports'][0]).parent if data['reports'] else rootdir
                    def open_folder(folder=folder):
                        if os.name=='nt':os.startfile(folder)
                        else:
                            import subprocess
                            subprocess.Popen(['xdg-open',str(folder)])
                    report_button.config(state='normal',command=open_folder)
        except queue.Empty:pass
        window.after(100,poll)
    config_button.config(command=lambda:start(True))
    def close():
        if busy:
            messagebox.showinfo('Проверка выполняется','Дождитесь завершения записи файлов.',parent=window)
        else:window.destroy()
    window.protocol('WM_DELETE_WINDOW',close)
    window.after(150,start);window.after(100,poll);window.mainloop()


def main():
    parser=argparse.ArgumentParser(description='Локальная проверка 764-П')
    parser.add_argument('--self-test',action='store_true');parser.add_argument('--gui-smoke',action='store_true')
    args=parser.parse_args();root,schemas=locations()
    if args.self_test:
        from check764p.selftest import self_test
        outcome=self_test(root,schemas,gui=args.gui_smoke)
        return 0 if outcome['ok'] else 1
    gui(root,schemas)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
