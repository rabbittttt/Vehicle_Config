"""Safely maintain five literal shared configuration constants without importing code."""
import ast
import base64
import copy
import difflib
import hashlib
import io
import json
import math
import os
import pprint
import sqlite3
import tempfile
import threading
import tokenize
import uuid
from datetime import datetime, timezone
from pathlib import Path
from catalog import Conflict

TABLES = {
    'gen_names':['id','source','target'],
    'option_names':['id','source','target'],
    'merge_groups':['id','name','members'],
    'battery_rules':['id','generation','version','sku','capacity'],
    'option_prices':['id','generation','category','name','price','default','versions','match_versions','seat'],
}
FIELDS = TABLES
CONSTANTS = {'gen_names':'GEN_DISPLAY_NAME_MAP','option_names':'MERGE_OPTIONS','merge_groups':'MERGE_GROUPS',
             'battery_rules':'BATTERY_RULES','option_prices':'OPTION_CONFIG'}
LIST_FIELDS = {'members','generation','version','sku','versions','match_versions'}
ARRAY_FIELDS = {'merge_groups':{'members'},'battery_rules':{'generation','version','sku'},'option_prices':{'versions','match_versions'}}


def empty_rules(): return {key:[] for key in TABLES}
def now(): return datetime.now(timezone.utc).isoformat()
def digest(raw): return hashlib.sha256(raw).hexdigest()
def dump(value): return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def stable_id(table,key): return str(uuid.uuid5(uuid.NAMESPACE_URL,'vehicle-shared-rules:'+table+':'+dump(key)))


def validate(data):
    errors=[]
    if not isinstance(data,dict) or set(data)!=set(TABLES):return ['规则表结构必须包含全部五张表']
    for table,fields in TABLES.items():
        if not isinstance(data[table],list):return [table+' 必须为列表']
        if len(data[table])>10000:return [table+' 超过 10000 行']
        ids=set();keys=set()
        for row in data[table]:
            if not isinstance(row,dict) or set(row)!=set(fields):return [table+' 字段不完整或包含未知字段']
            for field,value in row.items():
                if field in ARRAY_FIELDS.get(table,set()):
                    if not isinstance(value,list) or any(not isinstance(v,str) or not v.strip() or len(v)>4000 for v in value):return [table+' / '+field+' 必须是非空文本组成的列表']
                    if len(value)!=len(set(value)):errors.append(table+' / '+row.get('id','')+' / '+field+' 存在重复成员')
                elif field=='price':
                    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:return ['选配价格必须为有限的非负数字']
                elif field=='default':
                    if not isinstance(value,bool):return ['默认选配必须为布尔值']
                elif not isinstance(value,str) or len(value)>4000:return [table+' / '+field+' 必须是文本']
            ident=row['id']
            if not ident.strip() or ident in ids:errors.append(table+' 编号缺失或重复')
            ids.add(ident)
            required={'gen_names':['source','target'],'option_names':['source','target'],'merge_groups':['name'],
                      'battery_rules':['capacity'],'option_prices':['generation','category','name']}[table]
            for field in required:
                if not row[field].strip():errors.append(table+' / '+ident+' / '+field+' 不能为空')
            if table in ['gen_names','option_names']:key=row['source']
            elif table=='merge_groups':
                key=row['name']
                if not row['members']:errors.append('汇总组 '+row['name']+' 至少选择一个成员')
            elif table=='battery_rules':key=dump([row['generation'],row['version'],row['sku']])
            else:key=dump([row['generation'],row['category'],row['name'],row['versions'],row['match_versions'],row['seat']])
            if key in keys:errors.append(table+' / '+ident+' 存在重复规则')
            keys.add(key)
    owners={};groups={r['name']:r['members'] for r in data['merge_groups']}
    for name,members in groups.items():
        for member in members:
            if member in owners and owners[member]!=name:errors.append(member+' 同时归属 '+owners[member]+' 和 '+name)
            owners[member]=name
    visiting=set();done=set()
    def visit(name):
        if name in visiting:errors.append('汇总组存在循环：'+name);return
        if name in done:return
        visiting.add(name)
        for member in groups[name]:
            if member in groups:visit(member)
        visiting.remove(name);done.add(name)
    for name in groups:visit(name)
    return errors


def diff_rules(before,after):
    changes=[]
    for table in TABLES:
        left={r['id']:r for r in before[table]};right={r['id']:r for r in after[table]}
        for ident in left.keys()|right.keys():
            if left.get(ident)!=right.get(ident):changes.append({'table':table,'id':ident,'field':'记录','before':left.get(ident),'after':right.get(ident)})
        if table=='battery_rules' and [r['id'] for r in before[table]]!=[r['id'] for r in after[table]]:
            changes.append({'table':table,'id':'priority','field':'优先级','before':[r['id'] for r in before[table]],'after':[r['id'] for r in after[table]]})
    return changes


def _text(value):
    if not isinstance(value,str):raise ValueError('规则名称及条件必须是字符串')
    return value

def _strings(value):
    if not isinstance(value,list) or any(not isinstance(v,str) for v in value):raise ValueError('规则条件和成员必须是字符串列表')
    return value


def decode_constant(table,value):
    result=[]
    if table in ['gen_names','option_names','merge_groups']:
        if not isinstance(value,dict):raise ValueError(CONSTANTS[table]+' 必须为字典')
        for key,val in value.items():
            _text(key)
            if table=='merge_groups':result.append({'id':stable_id(table,key),'name':key,'members':_strings(val)})
            else:result.append({'id':stable_id(table,key),'source':key,'target':_text(val)})
    elif table=='battery_rules':
        if not isinstance(value,list):raise ValueError('BATTERY_RULES 必须为列表')
        counts={}
        for rule in value:
            if not isinstance(rule,tuple) or len(rule)!=4:raise ValueError('电池规则必须为四元组 (generation, version, sku, capacity)')
            generation,version,sku,capacity=rule
            key=dump([generation,version,sku]);counts[key]=counts.get(key,0)+1
            result.append({'id':stable_id(table,[key,counts[key]]),'generation':_strings(generation),'version':_strings(version),'sku':_strings(sku),'capacity':_text(capacity)})
    else:
        if not isinstance(value,dict):raise ValueError('OPTION_CONFIG 必须为代际→分类字典')
        counts={}
        for generation,categories in value.items():
            _text(generation)
            if not isinstance(categories,dict):raise ValueError('选配分类必须为字典')
            for category,options in categories.items():
                _text(category)
                if not isinstance(options,list):raise ValueError('选配项目必须为列表')
                for item in options:
                    if not isinstance(item,tuple) or len(item)!=4:raise ValueError('选配项目必须为四元组 (name, price, default, restrictions)')
                    name,price,default,restrictions=item
                    if not isinstance(restrictions,dict) or set(restrictions)-{'versions','match_versions','seat'}:raise ValueError('选配 restrictions 包含未知字段或不是字典')
                    versions=_strings(restrictions.get('versions',[]));matches=_strings(restrictions.get('match_versions',[]));seat=_text(restrictions.get('seat',''))
                    key=[generation,category,name,versions,matches,seat];fingerprint=dump(key);counts[fingerprint]=counts.get(fingerprint,0)+1
                    result.append({'id':stable_id(table,[key,counts[fingerprint]]),'generation':generation,'category':category,'name':_text(name),'price':price,'default':default,'versions':versions,'match_versions':matches,'seat':seat})
    return result


def encode_constant(table,rows):
    if table in ['gen_names','option_names']:return {r['source']:r['target'] for r in rows}
    if table=='merge_groups':return {r['name']:r['members'] for r in rows}
    if table=='battery_rules':return [(r['generation'],r['version'],r['sku'],r['capacity']) for r in rows]
    result={}
    for row in rows:
        restrictions={key:row[key] for key in ['versions','match_versions','seat'] if row[key]}
        result.setdefault(row['generation'],{}).setdefault(row['category'],[]).append((row['name'],row['price'],row['default'],restrictions))
    return result


def _literal(node):
    for part in ast.walk(node):
        if isinstance(part,ast.Dict):
            keys=[]
            for key in part.keys:
                if key is None:raise ValueError('规则字典不允许展开表达式')
                item=ast.literal_eval(key)
                if any(item==old for old in keys):raise ValueError('规则字典存在重复 key：'+str(item))
                keys.append(item)
    return ast.literal_eval(node)


def parse_source(raw):
    encoding,_=tokenize.detect_encoding(io.BytesIO(raw).readline)
    text=raw.decode(encoding);tree=ast.parse(text);compile(tree,'<shared_config>','exec')
    nodes={};values={};data=empty_rules();managed=set(CONSTANTS.values())
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):continue
        assigned=[child.id for child in ast.walk(node) if isinstance(child,ast.Name) and isinstance(child.ctx,ast.Store) and child.id in managed]
        if not assigned:continue
        if not isinstance(node,ast.Assign) or len(node.targets)!=1 or not isinstance(node.targets[0],ast.Name) or node.targets[0].id not in managed:
            raise ValueError('受维护常量只允许模块顶层单目标赋值')
        name=node.targets[0].id
        if name in nodes:raise ValueError('受维护常量重复赋值：'+name)
        try:values[name]=_literal(node.value)
        except (ValueError,TypeError,SyntaxError) as exc:raise ValueError(name+' 必须是字面量：'+str(exc)) from None
        nodes[name]=node.value
    for table,name in CONSTANTS.items():
        if name in values:data[table]=decode_constant(table,values[name])
    errors=validate(data)
    if errors:raise ValueError('\n'.join(errors))
    return {'raw':raw,'text':text,'encoding':encoding,'newline':'\r\n' if '\r\n' in text else '\n','nodes':nodes,'values':values,'data':data,'hash':digest(raw)}


def render_source(parsed,data):
    errors=validate(data)
    if errors:raise ValueError('\n'.join(errors))
    text=parsed['text'];lines=text.splitlines(keepends=True)
    def offset(line,column):return sum(len(value) for value in lines[:line-1])+len(lines[line-1].encode('utf-8')[:column].decode('utf-8'))
    edits=[];append=[]
    for table,name in CONSTANTS.items():
        value=encode_constant(table,data[table])
        if name in parsed['values'] and value==parsed['values'][name]:continue
        if name not in parsed['values'] and not data[table]:continue
        literal=pprint.pformat(value,width=100,sort_dicts=False).replace('\n',parsed['newline'])
        if name in parsed['nodes']:
            node=parsed['nodes'][name];edits.append((offset(node.lineno,node.col_offset),offset(node.end_lineno,node.end_col_offset),literal))
        else:append.append(name+' = '+literal+parsed['newline'])
    for start,end,replacement in sorted(edits,reverse=True):text=text[:start]+replacement+text[end:]
    if append:text=text.rstrip('\r\n')+parsed['newline']*2+parsed['newline'].join(append)
    try:raw=text.encode(parsed['encoding'])
    except UnicodeEncodeError:raise ValueError('新增规则包含源文件编码无法表示的字符') from None
    # Validate both Python syntax and the exact supported structure before touching the source.
    parse_source(raw)
    return raw


class RuleStore:
    def __init__(self,root,source_path=None):
        self.root=Path(root)/'rules';self.root.mkdir(parents=True,exist_ok=True)
        self.source_path=Path(source_path).resolve() if source_path else None
        self.lock=threading.RLock();self.recovery_error=''
        self.db=sqlite3.connect(self.root/'rules.sqlite3',check_same_thread=False,timeout=15)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY,revision INTEGER NOT NULL,data TEXT NOT NULL); CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,kind TEXT,actor TEXT,time TEXT,data TEXT,changes TEXT); CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY,payload TEXT NOT NULL);')
        if not self.db.execute('SELECT 1 FROM state WHERE id=1').fetchone():
            source=self._source();data=source['parsed']['data'] if source.get('parsed') else empty_rules()
            with self.db:
                self.db.executemany('INSERT INTO state VALUES(?,?,?)',[(1,0,dump(data)),(2,0,dump(data))])
                self._set_meta('source_hash',source['hash'] if source['available'] else '')
        self._recover()
    def _set_meta(self,key,value):self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,value))
    def _meta(self,key):
        row=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone();return row[0] if row else ''
    def _state(self,ident):
        revision,data=self.db.execute('SELECT revision,data FROM state WHERE id=?',(ident,)).fetchone();return {'revision':revision,'data':json.loads(data)}
    def _source(self):
        info={'path':str(self.source_path) if self.source_path else '', 'hash':'','available':False,'present':[],'missing':list(CONSTANTS.values()),'error':''}
        if self.source_path is None:info['error']='未配置 shared_config.py 路径';return info
        try:
            raw=self.source_path.read_bytes();info['hash']=digest(raw)
            parsed=parse_source(raw);info.update(available=True,present=list(parsed['nodes']),missing=[n for n in CONSTANTS.values() if n not in parsed['nodes']],parsed=parsed)
        except (OSError,ValueError,SyntaxError,UnicodeError,RecursionError) as exc:info['error']=str(exc)
        return info
    def _pending(self):
        row=self.db.execute('SELECT payload FROM journal WHERE id=1').fetchone();return json.loads(row[0]) if row else None
    def _finish_publish(self,transaction):
        with self.db:
            self.db.execute('UPDATE state SET revision=revision+1,data=? WHERE id=1',(dump(transaction['data']),))
            self.db.execute('UPDATE state SET revision=revision+1,data=? WHERE id=2',(dump(transaction['data']),))
            self._set_meta('source_hash',transaction['new_hash'])
            self.db.execute('INSERT INTO events(kind,actor,time,data,changes) VALUES(?,?,?,?,?)',('发布',transaction['actor'],transaction['time'],dump(transaction['data']),dump(transaction['changes'])))
            self.db.execute('DELETE FROM journal WHERE id=1')
    def _recover(self):
        transaction=self._pending()
        if not transaction:return
        try:
            current=digest(self.source_path.read_bytes()) if self.source_path else ''
            if current==transaction['new_hash']:
                self._finish_publish(transaction)
            elif current==transaction['old_hash']:
                with self.db:self.db.execute('DELETE FROM journal WHERE id=1')
            else:self.recovery_error='上次发布未完成，源文件已另行修改；核对备份后显式重新载入'
        except Exception as exc:self.recovery_error='上次发布恢复失败：'+str(exc)
    def get(self):
        with self.lock:
            source=self._source();source.pop('parsed',None);source['external_modified']=bool(source['hash']!=self._meta('source_hash'))
            draft=self._state(1);published=self._state(2)
            status='recovery_required' if self._pending() else 'unavailable' if not source['available'] else 'external_modified' if source['external_modified'] else 'draft' if draft['data']!=published['data'] else 'synced'
            return {'draft':draft,'published':published,'history':[dict(zip(['id','kind','actor','time'],row)) for row in self.db.execute('SELECT id,kind,actor,time FROM events ORDER BY id DESC')],
                    'source':source,'sync':{'status':status,'error':self.recovery_error,'backup_directory':str(self.root/'backups')}}
    def _revision(self,revision):
        if revision!=self._state(1)['revision']:raise Conflict('规则草稿已被其他人修改，请重新载入')
    def _validate(self,data):
        errors=validate(data)
        if errors:raise ValueError('\n'.join(errors))
    def _ready(self):
        if self._pending():raise Conflict(self.recovery_error or '上次发布未完成，请重启恢复或显式重新载入')
    def _event(self,kind,actor,data,changes):self.db.execute('INSERT INTO events(kind,actor,time,data,changes) VALUES(?,?,?,?,?)',(kind,actor,now(),dump(data),dump(changes)))
    def save(self,data,revision,actor):
        with self.lock:
            self._ready();self._validate(data)
            with self.db:
                self.db.execute('BEGIN IMMEDIATE');self._revision(revision)
                changes=diff_rules(self._state(1)['data'],data)
                self.db.execute('UPDATE state SET revision=revision+1,data=? WHERE id=1',(dump(data),));self._event('草稿',actor,data,changes)
            return self.get()
    def preview(self,data,revision):
        with self.lock:
            self._revision(revision);source=self._source();errors=validate(data)
            if self._pending():errors.append(self.recovery_error or '上次发布未完成')
            if not source['available']:errors.append(source['error'])
            elif source['hash']!=self._meta('source_hash'):errors.append('源文件已被其他程序修改，请先重新载入')
            script_diff=''
            if not errors:
                try:
                    raw=render_source(source['parsed'],data)
                    script_diff=''.join(difflib.unified_diff(source['parsed']['text'].splitlines(True),raw.decode(source['parsed']['encoding']).splitlines(True),fromfile='shared_config.py（当前）',tofile='shared_config.py（发布后）'))
                except (ValueError,SyntaxError) as exc:errors.append(str(exc))
            return {'errors':errors,'changes':diff_rules(self._state(2)['data'],data) if not validate(data) else [],'script_diff':script_diff,'source_hash':source['hash']}
    def _atomic_source(self,raw,expected_hash):
        if digest(self.source_path.read_bytes())!=expected_hash:raise Conflict('源文件在发布期间发生变化')
        fd,name=tempfile.mkstemp(prefix='.'+self.source_path.name+'.',suffix='.tmp',dir=self.source_path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.chmod(name,self.source_path.stat().st_mode)
            if digest(self.source_path.read_bytes())!=expected_hash:raise Conflict('源文件在发布期间发生变化')
            os.replace(name,self.source_path)
        finally:
            if os.path.exists(name):os.unlink(name)
    def publish(self,data,revision,source_hash,actor):
        with self.lock:
            self._ready();self._validate(data);source=self._source()
            if not source['available']:raise ValueError(source['error'])
            if source['hash']!=source_hash or source_hash!=self._meta('source_hash'):raise Conflict('源文件已变化，请重新预览或重新载入')
            raw=render_source(source['parsed'],data);changes=diff_rules(self._state(2)['data'],data)
            if not changes:raise ValueError('没有需要发布的规则变更')
            backup_dir=self.root/'backups';backup_dir.mkdir(exist_ok=True)
            backup=backup_dir/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex+'.py')
            with backup.open('xb') as stream:stream.write(source['parsed']['raw']);stream.flush();os.fsync(stream.fileno())
            transaction={'old_hash':source_hash,'new_hash':digest(raw),'data':copy.deepcopy(data),'changes':changes,'actor':actor,'time':now(),'backup':str(backup)}
            with self.db:
                self.db.execute('BEGIN IMMEDIATE');self._revision(revision);self._ready()
                self.db.execute('INSERT INTO journal VALUES(1,?)',(dump(transaction),))
            try:
                self._atomic_source(raw,source_hash)
                self._finish_publish(transaction)
            except Exception:
                self._recover()
                if self._pending() or self._meta('source_hash')!=transaction['new_hash']:raise
            return self.get()
    def reload(self,revision,source_hash,actor):
        with self.lock:
            source=self._source()
            if not source['available']:raise ValueError(source['error'])
            if source['hash']!=source_hash:raise Conflict('源文件再次变化，请重新检查')
            data=source['parsed']['data']
            with self.db:
                self.db.execute('BEGIN IMMEDIATE');self._revision(revision)
                changes=diff_rules(self._state(1)['data'],data)
                self.db.execute('UPDATE state SET revision=revision+1,data=?',(dump(data),))
                self._set_meta('source_hash',source_hash);self._event('重新载入',actor,data,changes)
                self.db.execute('DELETE FROM journal WHERE id=1')
            self.recovery_error='';return self.get()
    def history(self,ident):
        with self.lock:
            row=self.db.execute('SELECT data,changes FROM events WHERE id=?',(ident,)).fetchone()
            if not row:raise ValueError('规则版本不存在')
            return {'data':json.loads(row[0]),'changes':json.loads(row[1])}
