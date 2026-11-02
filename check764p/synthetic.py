"""Entirely generated fictitious messages for tests and packaged self-check."""
from lxml import etree as E


def message(records=None, version='1.2', day='19/10/2026'):
    root=E.Element('СообщОтказ_115ФЗ')
    def add(parent,tag,text=''):
        node=E.SubElement(parent,tag);node.text=text;return node
    service=add(root,'СлужЧасть')
    for tag,text in [('ВерсияФормата',version),('ДатаСообщения',day),('ТелОператор','0000000000'),('ЭлектроннаяПочта','synthetic@example.invalid')]: add(service,tag,text)
    records = records if records is not None else [dict(number=1)]
    groups={}
    for item in records:
        section=item.get('section','Раздел2');org='НФО' if section=='Раздел2' else 'КО'
        groups.setdefault(org,[]).append(item)
    if not groups: groups['НФО']=[]
    for org,items in groups.items():
        block=add(root,'ИнформЧасть');add(block,'ТипОрганизация',org)
        for item in sorted(items,key=lambda x:x.get('section','Раздел2')):
            section=item.get('section','Раздел2');node=add(block,section)
            add(node,'НомерЗаписи',f"2026_000_11_{item.get('number',1):016d}")
            add(node,'ФорматЗаписи','1.0');add(node,'ТипЗаписи',str(item.get('code',1)))
            if section=='Раздел1.1':
                add(node,'КодОтказа','03');add(node,'ДатаОтказа',day)
                add(node,'ТипКлиента','1');add(node,'ПризнакКлиента','1')
                person=add(node,'СведЮЛ');add(person,'НаимЮЛ',item.get('name','ООО Синтетический пример'))
                add(person,'ИННЮЛ','0000000000');add(person,'ОГРНЮЛ','0000000000000')
                address=add(person,'АдрРегЮЛ')
                for tag,value in [('КодОКСМ','643'),('СтранаНаименование','Россия'),('КодСубъектаПоОКАТО','00'),('Район',''),('Пункт',''),('Улица',''),('Дом',''),('Корп',''),('Оф','')]:add(address,tag,value)
            else:
                operation=add(node,'СведОперация');add(operation,'КодОтказа','07');add(operation,'ДатаОтказа',day)
                for i,name in enumerate(item.get('names',[item.get('name','Примеров Тест Тестович')]),1):
                    p=add(node,'Участник') if section=='Раздел2' else node
                    if section=='Раздел2':add(p,'СтатусУчастника',str(min(i,7)))
                    add(p,'ТипУчастника','2');add(p,'ПризнУчастника','1')
                    person=add(p,'СведФЛИП');add(person,'ФИОСтрока',name)
            if item.get('code',1)>=4:add(node,'ПричинаУдаления','Синтетический пример закрытия')
    return E.tostring(root,encoding='UTF-8',xml_declaration=True)
