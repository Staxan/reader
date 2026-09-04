#!/usr/bin/env python3
"""Читалка книги: веб-интерфейс над локальной базой документов ENOT.

Читает документы, показывает их в книжной вёрстке, ведёт версии и замечания.
Зависимостей нет — только стандартная библиотека Python 3.8+.

Запуск:
    python book_reader.py                  (Windows)
    python3 book_reader.py                 (WSL)

Путь к базе берётся из config.json в корне Orchestra. Его можно перекрыть
параметром --base или переменной окружения ORCHESTRA_BASE.

Параметры:
    --base <путь>   папка базы документов
    --port <число>  порт, по умолчанию 8765
    --host <адрес>  127.0.0.1 — только эта машина, 0.0.0.0 — вся локальная сеть
                    (в сети доступно только чтение: запись всегда локальная)
"""
import argparse
import html
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agents
import binding
import hermes_link
import jobs
import notes
import settings
import structure
import sync_yonote
import theme
from agents import discover, registry

CFG = settings.load()
DEFAULT_BASE = CFG['base']

INDEX_NAME = '_index.md'
SRC_HEADER = re.compile(r'(?im)^#{0,4}\s*Источники(\s+к\s+главе[^\n]*)?\s*[:.]?\s*$')
ITEM = re.compile(r'^(\d{1,2})[.)]\s+')
MARK_RE = re.compile(r'\[(\d+)\]')

BASE = DEFAULT_BASE

# Дерево базы держим в памяти и обновляем фоновым потоком.
# Обход базы с бэкапом ENOT (под тысячу файлов) на мосту WSL→Windows занимает
# секунды, поэтому запросы читают только готовый кеш и не ждут обхода.
RESCAN = 10.0
_TREE_CACHE = {'stamp': None, 'tree': [], 'ready': False}
_LOCK = threading.Lock()


def tree_stamp():
    """Отпечаток базы: если файлы менялись, он меняется."""
    newest, count = 0.0, 0
    for dirpath, dirnames, filenames in os.walk(BASE):
        dirnames[:] = [d for d in dirnames if not d.startswith(('_', '.'))]
        for fn in filenames:
            if fn.endswith('.md'):
                count += 1
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(dirpath, fn)))
                except OSError:
                    pass
    return (count, round(newest, 2))


# ------------------------------------------------------------------ база


def read_doc(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return structure.strip_front(f.read())


def safe_path(rel):
    full = os.path.normpath(os.path.join(BASE, rel.replace('\\', os.sep)))
    if not os.path.abspath(full).startswith(os.path.abspath(BASE)):
        return None
    return full


def scan_tree():
    """Полный обход базы. Вызывается только фоновым потоком и на старте."""
    def walk(folder):
        nodes = []
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            return nodes
        for name in entries:
            if name.startswith(('_', '.')):
                continue
            full = os.path.join(folder, name)
            if os.path.isdir(full):
                title = name
                idx = os.path.join(full, INDEX_NAME)
                if os.path.exists(idx):
                    meta, _ = read_doc(idx)
                    title = meta.get('title') or name
                nodes.append({'kind': 'folder', 'title': title,
                              'rel': os.path.relpath(full, BASE).replace(os.sep, '/'),
                              'children': walk(full)})
            elif name.endswith('.md') and name != 'README.md':
                try:
                    meta, text = read_doc(full)
                except OSError:
                    continue
                nodes.append({
                    'kind': 'doc',
                    'title': meta.get('title') or name[:-3],
                    'rel': os.path.relpath(full, BASE).replace(os.sep, '/'),
                    'chars': len(text.strip()),
                    'status': meta.get('status') or '',
                    'url_id': meta.get('url_id') or '',
                    'marks': len(set(MARK_RE.findall(text))),
                })
        nodes.sort(key=lambda n: (n['kind'] != 'folder', n['title']))
        return nodes

    tree = walk(BASE)
    return tree


def refresh(force=False):
    """Пересобирает кеш, если база изменилась."""
    stamp = tree_stamp()
    if not force and stamp == _TREE_CACHE['stamp']:
        return
    tree = scan_tree()
    with _LOCK:
        _TREE_CACHE['stamp'] = stamp
        _TREE_CACHE['tree'] = tree
        _TREE_CACHE['ready'] = True


def build_tree():
    """Готовое дерево из кеша. Запрос никогда не ждёт обхода диска."""
    with _LOCK:
        return _TREE_CACHE['tree']


def watcher():
    while True:
        time.sleep(RESCAN)
        try:
            refresh()
        except Exception:
            pass


def flat_docs(nodes, out=None):
    out = [] if out is None else out
    for n in nodes:
        if n['kind'] == 'doc':
            out.append(n)
        else:
            flat_docs(n.get('children') or [], out)
    return out


def collect_sources(text):
    """номер -> краткая подпись, для подсказок над метками."""
    last = None
    for last in SRC_HEADER.finditer(text):
        pass
    if not last:
        return {}
    block = text[last.end():]
    out = {}
    cur = None
    for line in block.replace('\r\n', '\n').split('\n'):
        s = line.strip()
        if not s:
            continue
        m = ITEM.match(s)
        if m:
            cur = m.group(1)
            out[cur] = s[m.end():]
        elif cur and len(out[cur]) < 260:
            out[cur] += ' ' + s
    return {k: re.sub(r'\s+', ' ', v)[:300] for k, v in out.items()}


# ------------------------------------------------------------------ страница

JS = r"""
(function(){
const $=(s,r=document)=>r.querySelector(s);
const $$=(s,r=document)=>Array.from(r.querySelectorAll(s));

/* поиск по дереву */
const f=$('#find');
if(f)f.addEventListener('input',()=>{
  const v=f.value.trim().toLowerCase();
  $$('.dc').forEach(a=>{
    const hit=!v||a.dataset.t.toLowerCase().includes(v);
    a.style.display=hit?'':'none';
    const w=a.closest('.dcw'); if(w)w.style.display=hit?'':'none';
  });
  $$('.grp').forEach(g=>{
    const any=$$('.dc',g).some(a=>a.style.display!=='none');
    g.style.display=any?'':'none'; if(v&&any)g.open=true;
  });
});

/* большие папки подгружаются при первом раскрытии */
function bindLazy(root){
  $$('details.grp',root).forEach(d=>{
    if(d.dataset.bound)return; d.dataset.bound='1';
    d.addEventListener('toggle',async()=>{
      const box=d.querySelector(':scope > .kids');
      if(!d.open||!box||box.dataset.lazy!=='1')return;
      box.dataset.lazy='0';
      try{
        const r=await fetch('/api/branch?p='+encodeURIComponent(d.dataset.rel));
        box.innerHTML=await r.text();
        bindLazy(box);
      }catch(e){box.innerHTML='<span class="load">не загрузилось</span>'}
    });
  });
}
bindLazy(document);

/* настройки читателя живут в браузере */
const S={theme:'dark',font:'serif',size:'19',width:'44',map:'1'};
Object.keys(S).forEach(k=>{const v=localStorage.getItem('rd-'+k); if(v)S[k]=v});
function apply(){
  document.body.className='theme-'+S.theme+' font-'+S.font;
  document.documentElement.style.setProperty('--fs',S.size+'px');
  document.documentElement.style.setProperty('--col',S.width+'rem');
  const w=$('.wrap'); if(w)w.classList.toggle('nomap',S.map!=='1');
  const tb=$('#t-map'); if(tb)tb.classList.toggle('on',S.map==='1');
  const fb=$('#t-font'); if(fb)fb.textContent=S.font==='serif'?'Aa':'Aa';
  const th=$('#t-theme'); if(th)th.textContent={dark:'\u25D1',light:'\u25D0',sepia:'\u25D2'}[S.theme];
}
function set(k,v){S[k]=String(v);localStorage.setItem('rd-'+k,S[k]);apply()}
apply();
$('#t-theme')?.addEventListener('click',()=>{
  set('theme',{dark:'light',light:'sepia',sepia:'dark'}[S.theme])});
$('#t-font')?.addEventListener('click',()=>{
  set('font',S.font==='serif'?'sans':'serif')});
$('#t-less')?.addEventListener('click',()=>set('size',Math.max(15,+S.size-1)));
$('#t-more')?.addEventListener('click',()=>set('size',Math.min(26,+S.size+1)));
$('#t-wide')?.addEventListener('click',()=>set('width',S.width==='44'?'56':'44'));
$('#t-map')?.addEventListener('click',()=>set('map',S.map==='1'?'0':'1'));
$('#burger')?.addEventListener('click',()=>$('.side').classList.toggle('open'));

/* сноска: плавно к источнику и подсветить */
$$('a.ref').forEach(a=>a.addEventListener('click',e=>{
  const t=document.getElementById('src-'+a.dataset.n);
  if(!t)return; e.preventDefault();
  t.scrollIntoView({behavior:'smooth',block:'center'});
  $$('.sources li').forEach(li=>li.classList.remove('hit'));
  t.classList.add('hit');
  history.replaceState(null,'','#src-'+a.dataset.n);
}));

/* прогресс чтения и активный пункт карты */
const main=$('.main'), bar=$('#progress');
const heads=$$('article.doc h2[id],article.doc h3[id]');
const links=$$('.map a');
function onScroll(){
  if(bar&&main){
    const max=main.scrollHeight-main.clientHeight;
    bar.style.width=(max>0?(main.scrollTop/max*100):0)+'%';
  }
  let cur=null;
  heads.forEach(h=>{if(h.getBoundingClientRect().top<160)cur=h.id});
  links.forEach(l=>l.classList.toggle('on',l.dataset.h===cur));
  udState();
}

/* карта всплывает не на любой прокрутке, а только когда курсор у правого
   края — там же, где стрелки. Иначе она закрывает текст при прокрутке на
   пару строк, а нужна она ровно в тот момент, когда ищешь другое место. */
const mapBox=$('.map');
let mapT=null, mapHover=false, nearRail=false;
const RAIL=150;      /* полоса у правого края, где живут стрелки и карта */

document.addEventListener('mousemove',e=>{
  nearRail=(window.innerWidth-e.clientX)<RAIL;
},{passive:true});

function showMap(){
  if(!mapBox)return;
  mapBox.classList.add('show');
  clearTimeout(mapT);
}
function peekMap(){
  if(!mapBox||(!nearRail&&!mapHover))return;
  showMap();
  mapT=setTimeout(()=>{if(!mapHover)mapBox.classList.remove('show')},1600);
}
mapBox?.addEventListener('mouseenter',()=>{mapHover=true;showMap()});
mapBox?.addEventListener('mouseleave',()=>{mapHover=false;peekMap()});
mapBox?.addEventListener('click',e=>{if(e.target.tagName==='A')peekMap()});

/* стрелки к началу и в конец главы */
const ud=$('#updown'), udUp=$('#ud-up'), udDown=$('#ud-down');
ud?.addEventListener('mouseenter',()=>{nearRail=true;showMap()});
ud?.addEventListener('mouseleave',()=>peekMap());
function udState(){
  if(!ud||!main)return;
  const max=main.scrollHeight-main.clientHeight;
  ud.classList.toggle('show',max>500);
  udUp?.classList.toggle('off',main.scrollTop<40);
  udDown?.classList.toggle('off',main.scrollTop>max-40);
}
udUp?.addEventListener('click',()=>main.scrollTo({top:0,behavior:'smooth'}));
udDown?.addEventListener('click',
  ()=>main.scrollTo({top:main.scrollHeight,behavior:'smooth'}));

main?.addEventListener('scroll',()=>{onScroll();peekMap()},{passive:true});
onScroll();
window.addEventListener('resize',udState);

/* клавиши: j/k — прокрутка, стрелки — соседний документ */
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;
  if(e.key==='j')main.scrollBy({top:260,behavior:'smooth'});
  if(e.key==='k')main.scrollBy({top:-260,behavior:'smooth'});
  if(e.key==='Home')main.scrollTo({top:0,behavior:'smooth'});
  if(e.key==='End')main.scrollTo({top:main.scrollHeight,behavior:'smooth'});
  if(e.key==='ArrowRight'&&window.__next)location.href=window.__next;
  if(e.key==='ArrowLeft'&&window.__prev)location.href=window.__prev;
});

/* ---------------------------------------------- сообщения */
const toast=$('#toast');
let toastT=null;
function say(msg,ms){
  if(!toast)return;
  toast.textContent=msg; toast.classList.add('show');
  clearTimeout(toastT); toastT=setTimeout(()=>toast.classList.remove('show'),ms||1800);
}

/* ---------------------------------------------- место чтения
   Страница после сохранения замечания перезагружается, а браузер не умеет
   возвращать прокрутку внутри области текста. Поэтому запоминаем место сами
   и возвращаемся к нему — лучше прямо к замечанию, с которым работали. */
const SPOS='rd-pos-'+(window.__doc||'');

function rememberPos(focusId){
  try{
    sessionStorage.setItem(SPOS,JSON.stringify(
      {top:main?main.scrollTop:0,focus:focusId||''}));
  }catch(e){}
}

function flash(el){
  el.classList.add('found');
  setTimeout(()=>el.classList.remove('found'),1400);
}

function restorePos(){
  let d=null;
  try{d=JSON.parse(sessionStorage.getItem(SPOS)||'null')}catch(e){}
  if(!d)return;
  try{sessionStorage.removeItem(SPOS)}catch(e){}
  const go=()=>{
    if(d.focus){
      const el=document.getElementById(d.focus);
      if(el){
        el.scrollIntoView({block:'center'});
        flash(el);
        const m=document.querySelector('mark[data-id="'+d.focus+'"]');
        if(m)flash(m);
        return;
      }
    }
    if(main&&d.top)main.scrollTop=d.top;
  };
  /* ждём, пока страница разложится, иначе прокрутка не сработает */
  requestAnimationFrame(()=>requestAnimationFrame(go));
}

if('scrollRestoration' in history)history.scrollRestoration='manual';
restorePos();

/* ---------------------------------------------- плашка при выделении */
const art=$('article.doc'), bar2=$('#selbar');
const CAN_WRITE=document.body.dataset.write==='1';
let sel=null;   /* {block, text, occ} */

/* какой блок и какое по счёту вхождение выделено */
function readSelection(){
  const s=window.getSelection();
  if(!s||s.isCollapsed)return null;
  const text=s.toString().replace(/\s+/g,' ').trim();
  if(text.length<3)return null;
  let node=s.anchorNode;
  /* поднимаемся до блока с data-b */
  while(node&&node!==art){
    if(node.nodeType===1&&node.hasAttribute&&node.hasAttribute('data-b'))break;
    node=node.parentNode;
  }
  if(!node||node===art||!node.hasAttribute('data-b'))return null;
  /* выделение не должно вылезать за пределы блока */
  if(!node.contains(s.focusNode))return null;
  const full=node.textContent.replace(/\s+/g,' ').trim();
  if(!full.includes(text))return null;
  /* номер вхождения: считаем, сколько таких же кусков левее */
  const r=s.getRangeAt(0);
  const pre=document.createRange();
  pre.setStart(node,0); pre.setEnd(r.startContainer,r.startOffset);
  const before=pre.toString().replace(/\s+/g,' ');
  let occ=0,i=-1;
  while((i=before.indexOf(text,i+1))>=0)occ++;
  return {block:+node.dataset.b,text:text,occ:occ,node:node,bhash:node.dataset.bh||''};
}

function placeBar(){
  const s=window.getSelection();
  if(!s||s.rangeCount===0)return;
  const rect=s.getRangeAt(0).getBoundingClientRect();
  bar2.classList.add('show');
  const w=bar2.offsetWidth, h=bar2.offsetHeight;
  let x=rect.left+rect.width/2-w/2;
  x=Math.max(10,Math.min(x,innerWidth-w-10));
  let y=rect.top-h-9;
  if(y<10)y=rect.bottom+9;
  bar2.style.left=x+'px'; bar2.style.top=y+'px';
}
function hideBar(){bar2&&bar2.classList.remove('show')}

if(art&&bar2){
  /* ловим отпускание мыши, а не selectionchange: тот дрожит */
  art.addEventListener('mouseup',()=>setTimeout(()=>{
    sel=readSelection();
    if(sel)placeBar(); else hideBar();
  },0));
  art.addEventListener('keyup',e=>{
    if(!e.shiftKey)return;
    sel=readSelection(); if(sel)placeBar(); else hideBar();
  });
  /* mousedown на плашке нельзя обрабатывать браузеру: он снимет выделение */
  bar2.addEventListener('mousedown',e=>e.preventDefault());
  document.addEventListener('mousedown',e=>{
    if(!bar2.contains(e.target)&&!e.target.closest('.qform'))hideBar();
  });
  document.addEventListener('keydown',e=>{if(e.key==='Escape')hideBar()});
  main?.addEventListener('scroll',()=>{if(bar2.classList.contains('show'))placeBar()},
    {passive:true});
}

$('#sb-copy')?.addEventListener('click',async()=>{
  const t=window.getSelection().toString();
  try{await navigator.clipboard.writeText(t);say('Скопировано')}
  catch(e){say('Не удалось скопировать')}
  hideBar();
});

/* ---------------------------------------------- цитата */
const DRAFT='rd-draft-'+location.search;

function closeForm(ask){
  const f=$('.qform');
  if(!f)return true;
  const ta=f.querySelector('textarea');
  if(ask&&ta.value.trim()&&!confirm('Замечание не сохранено. Закрыть?'))return false;
  localStorage.removeItem(DRAFT);
  f.remove();
  return true;
}

function openForm(anchor){
  if(!closeForm(true))return;
  const host=art.querySelector('[data-b="'+anchor.block+'"]');
  if(!host)return;
  const f=document.createElement('div');
  f.className='qform';
  f.innerHTML='<div class="cite"></div><textarea placeholder="Ваше замечание к '+
    'выделенному тексту…"></textarea>'+
    '<div class="foot"><span>Ctrl+Enter — сохранить, Esc — отменить</span>'+
    '<span class="sp"><button data-a="cancel">Отменить</button>'+
    '<button class="ok" data-a="save">Сохранить</button></span></div>';
  f.querySelector('.cite').textContent=anchor.text;
  host.insertAdjacentElement('afterend',f);
  const ta=f.querySelector('textarea');
  const saved=localStorage.getItem(DRAFT);
  if(saved){try{const d=JSON.parse(saved);
    if(d.block===anchor.block&&d.text===anchor.text)ta.value=d.comment||''}catch(e){}}
  ta.focus();
  f.scrollIntoView({behavior:'smooth',block:'center'});

  let dT=null;
  ta.addEventListener('input',()=>{
    clearTimeout(dT);
    dT=setTimeout(()=>localStorage.setItem(DRAFT,JSON.stringify(
      {block:anchor.block,text:anchor.text,comment:ta.value})),1200);
  });
  ta.addEventListener('keydown',e=>{
    if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();saveNote(anchor,ta.value)}
    if(e.key==='Escape'){e.preventDefault();closeForm(true)}
  });
  f.querySelector('[data-a="save"]').addEventListener('click',
    ()=>saveNote(anchor,ta.value));
  f.querySelector('[data-a="cancel"]').addEventListener('click',()=>closeForm(true));

  /* клик по свободному месту сохраняет написанное */
  setTimeout(()=>{
    document.addEventListener('mousedown',function once(e){
      if(!document.body.contains(f)){document.removeEventListener('mousedown',once);return}
      if(f.contains(e.target)||bar2.contains(e.target))return;
      document.removeEventListener('mousedown',once);
      const v=ta.value.trim();
      if(v)saveNote(anchor,v); else closeForm(false);
    });
  },50);
}

async function saveNote(anchor,comment){
  const v=(comment||'').trim();
  if(!v){say('Пустое замечание не сохраняется');return}
  try{
    const r=await fetch('/api/note',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({p:window.__doc,block:anchor.block,text:anchor.text,
        occ:anchor.occ,comment:v})});
    const d=await r.json();
    if(!r.ok||!d.ok){say(d.error||'Не сохранилось',3200);return}
    localStorage.removeItem(DRAFT);
    /* запоминаем место, чтобы после перезагрузки вернуться к этой цитате */
    rememberPos(d.id);
    say('Замечание сохранено');
    location.reload();
  }catch(e){say('Сервер не ответил, замечание осталось в поле',3600)}
}

$('#sb-quote')?.addEventListener('click',()=>{
  if(!CAN_WRITE){say('Эта версия только для чтения — создайте копию',3000);
    hideBar();return}
  if(!sel){say('Выделите текст');return}
  const a={block:sel.block,text:sel.text,occ:sel.occ};
  hideBar();
  window.getSelection().removeAllRanges();
  openForm(a);
});

/* правка и удаление существующих замечаний */
art?.addEventListener('click',async e=>{
  const b=e.target.closest('button[data-act]');
  if(!b)return;
  const id=b.dataset.id;
  if(b.dataset.act==='del-note'){
    if(!confirm('Удалить замечание?'))return;
    rememberPos();
    const r=await fetch('/api/note?id='+id+'&p='+encodeURIComponent(window.__doc),
      {method:'DELETE'});
    if(r.ok){say('Удалено');location.reload()}else say('Не удалось удалить');
  }
  if(b.dataset.act==='edit-note'){
    const box=b.closest('.qbox');
    const cur=box.querySelector('.said').textContent;
    const v=prompt('Замечание:',cur);
    if(v===null)return;
    rememberPos(id);
    const r=await fetch('/api/note',{method:'PUT',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({p:window.__doc,id:id,comment:v})});
    if(r.ok){say('Изменено');location.reload()}else say('Не удалось изменить');
  }
});

/* подсветка mark <-> врезка */
$$('mark[data-id]').forEach(m=>{
  m.addEventListener('click',()=>{
    const box=document.getElementById(m.dataset.id);
    if(!box)return;
    if(box.tagName==='DETAILS')box.open=true;
    box.scrollIntoView({behavior:'smooth',block:'center'});
  });
});

/* ---------------------------------------------- версии и история */
$('#v-new')?.addEventListener('click',async()=>{
  if(!confirm('Создать новую версию документа? Текущая станет только для чтения.'))return;
  const r=await fetch('/api/version',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({p:window.__doc})});
  const d=await r.json();
  if(!r.ok||!d.ok){say(d.error||'Не удалось создать версию',3200);return}
  location.href='/doc?p='+encodeURIComponent(d.rel);
});

/* ---------------------------------------------- отправка в работу */
const jbox=$('#job');
let jtimer=null;

const MARKS={done:'\u2713',run:'\u27F3',wait:'\u25CB',fail:'\u2715',skip:'\u2013'};
const STATE={queued:'принято, ждёт',running:'в работе',done:'готово',
  failed:'сорвалось',stopped:'прервано'};

function hhmm(iso){
  if(!iso)return '';
  const t=String(iso).split('T')[1]||'';
  return t.slice(0,5);
}

function drawJob(j){
  if(!j||j.state==='none'){jbox.classList.remove('show');return}
  const total=j.total||(j.steps||[]).length||1;
  const done=j.done||0;
  const bars=(j.steps||[]).map(s=>{
    const cls=s.state==='done'||s.state==='skip'?'done':(s.state==='run'?'run':'');
    return '<i class="'+cls+'"></i>';
  }).join('');
  const steps=(j.steps||[]).map(s=>{
    const cls=s.state||'wait';
    const tm=s.at?'<span class="tm">'+hhmm(s.at)+'</span>':'';
    return '<div class="jstep '+cls+'"><span class="m">'+(MARKS[cls]||'')+
      '</span><span>'+s.title+'</span>'+tm+'</div>';
  }).join('');

  let foot='<span>'+(STATE[j.state]||j.state)+'</span>';
  if(j.stale)foot='<span class="warn">нет ответа больше двух минут</span>';
  if(j.error)foot='<span class="warn">'+j.error+'</span>';
  if(j.state==='queued'||j.state==='running'){
    foot+='<button data-a="stop">прервать</button>';
  }else{
    if(j.new_rel)foot+='<button data-a="open">открыть '+(j.version_new||'новую версию')+'</button>';
    foot+='<button data-a="close">закрыть</button>';
  }

  jbox.innerHTML=
    '<div class="jh"><span>'+(j.title||'Работа над документом')+'</span>'+
      '<span class="cnt">'+done+' / '+total+'</span>'+
      '<button class="x" data-a="hide">\u2715</button></div>'+
    '<div class="jbar">'+bars+'</div>'+
    '<div class="jsteps">'+steps+'</div>'+
    '<div class="jfoot">'+foot+'</div>';
  jbox.classList.add('show');
  jbox.dataset.new=j.new_rel||'';
}

async function pollJob(){
  if(!window.__doc)return;
  try{
    const r=await fetch('/api/job?p='+encodeURIComponent(window.__doc));
    const j=await r.json();
    drawJob(j);
    const live=j.state==='queued'||j.state==='running';
    if(live&&!jtimer)jtimer=setInterval(pollJob,1500);
    if(!live&&jtimer){clearInterval(jtimer);jtimer=null}
  }catch(e){}
}

jbox?.addEventListener('click',async e=>{
  const b=e.target.closest('button[data-a]');
  if(!b)return;
  const a=b.dataset.a;
  if(a==='hide'||a==='close'){
    if(a==='close'){
      await fetch('/api/job?p='+encodeURIComponent(window.__doc),{method:'DELETE'});
    }
    jbox.classList.remove('show');
    if(jtimer){clearInterval(jtimer);jtimer=null}
    return;
  }
  if(a==='stop'){
    if(!confirm('Прервать работу?'))return;
    await fetch('/api/job?p='+encodeURIComponent(window.__doc),{method:'DELETE'});
    pollJob();
    return;
  }
  if(a==='open'&&jbox.dataset.new){
    location.href='/doc?p='+encodeURIComponent(jbox.dataset.new);
  }
});

$('#v-send')?.addEventListener('click',async e=>{
  const btn=e.currentTarget;
  const items=$$('.qbox.note .said,.qbox.old .said').map(
    (x,i)=>(i+1)+'. '+x.textContent.trim().slice(0,90));
  if(!items.length){say('Нет открытых замечаний');return}
  if(!confirm('Отправить в работу '+items.length+
      (items.length===1?' замечание':' замечаний')+'?\n\n'+items.join('\n')))return;
  btn.disabled=true;
  try{
    const r=await fetch('/api/job',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({p:window.__doc})});
    const d=await r.json();
    if(!r.ok||!d.ok){say(d.error||'Не отправилось',3200);btn.disabled=false;return}
    say(d.message||'Задание принято',d.sent?2200:4200);
    pollJob();
  }catch(e){say('Сервер не ответил',3200);btn.disabled=false}
});

pollJob();

const hist=$('#hist');
$('#t-hist')?.addEventListener('click',async()=>{
  if(hist.classList.contains('open')){hist.classList.remove('open');return}
  hist.innerHTML='<button class="close">✕</button><h3>История документа</h3>'+
    '<div class="ev">загружаю…</div>';
  hist.classList.add('open');
  try{
    const r=await fetch('/api/history?p='+encodeURIComponent(window.__doc));
    hist.innerHTML='<button class="close">✕</button>'+await r.text();
  }catch(e){hist.innerHTML='<button class="close">✕</button><h3>История</h3>'+
    '<div class="ev">не загрузилось</div>'}
  hist.querySelector('.close').addEventListener('click',
    ()=>hist.classList.remove('open'));
});

/* ---------------------------------------------- меню документа и коллекции
   Три точки в дереве: привязать агента, снять привязку, открыть.
   Список агентов и текущая привязка приходят с сервера, поэтому меню всегда
   показывает то, что есть на самом деле, а не то, что было при загрузке. */
const menu=document.createElement('div');
menu.className='menu';
document.body.appendChild(menu);
let menuBtn=null;

function closeMenu(){
  menu.classList.remove('show');
  menuBtn?.classList.remove('open');
  menuBtn=null;
}
document.addEventListener('click',e=>{
  if(!menu.contains(e.target)&&!e.target.closest('.dots'))closeMenu();
});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeMenu()});

async function openMenu(btn){
  const rel=btn.dataset.rel, kind=btn.dataset.kind;
  if(menuBtn===btn){closeMenu();return}
  closeMenu();
  menuBtn=btn; btn.classList.add('open');
  menu.innerHTML='<div class="mt">загружаю…</div>';
  menu.classList.add('show');

  const r0=btn.getBoundingClientRect();
  menu.style.left=Math.min(r0.left,window.innerWidth-230)+'px';
  menu.style.top=(r0.bottom+6)+'px';

  let d={};
  try{
    const r=await fetch('/api/bind?p='+encodeURIComponent(rel));
    d=await r.json();
  }catch(e){menu.innerHTML='<div class="mt">сервер не ответил</div>';return}
  if(!d.ok){menu.innerHTML='<div class="mt">'+(d.error||'не вышло')+'</div>';return}

  const list=d.list||[];
  let h='<div class="mt">'+(kind==='collection'?'Агент коллекции':'Агент документа')+'</div>';
  if(!list.length){
    h+='<button data-a="agents">агентов нет · настроить</button>';
  }else{
    list.forEach(a=>{
      const on=(kind==='collection'?d.own:d.agent)===a.id;
      h+='<button data-a="bind" data-id="'+a.id+'"'+(on?' class="on"':'')+'>'+a.name+'</button>';
    });
    const own=kind==='collection'?d.own:d.own;
    if(own)h+='<div class="sep"></div><button data-a="bind" data-id="">снять привязку</button>';
    if(kind==='doc'&&d.inherited&&d.name){
      h+='<div class="mt">сейчас: '+d.name+' — от коллекции</div>';
    }
  }
  h+='<div class="sep"></div>';
  if(kind==='doc')h+='<button data-a="open">открыть документ</button>';
  h+='<button data-a="agents">настройки агентов</button>';
  menu.innerHTML=h;

  menu.querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{
    const a=b.dataset.a;
    if(a==='agents'){location.href='/agents';return}
    if(a==='open'){location.href='/doc?p='+encodeURIComponent(rel);return}
    if(a==='bind'){
      try{
        const r=await fetch('/api/bind',{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({p:rel,agent:b.dataset.id||''})});
        const j=await r.json();
        if(!r.ok||!j.ok){say(j.error||'не вышло',3200);return}
        say(b.dataset.id?('агент: '+(j.name||b.dataset.id)):'привязка снята');
        closeMenu();
        setTimeout(()=>location.reload(),500);
      }catch(e){say('сервер не ответил',3200)}
    }
  }));
}

document.addEventListener('click',e=>{
  const b=e.target.closest('.dots');
  if(!b)return;
  e.preventDefault(); e.stopPropagation();
  openMenu(b);
});

/* ---------------------------------------------- страница «Агенты» */
const agList=$('#ag-list');
if(agList){
  const post=async body=>{
    const r=await fetch('/api/agents',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const j=await r.json();
    if(!r.ok||!j.ok)say(j.error||'не вышло',3200);
    return j.ok;
  };

  const card=(a,found)=>{
    const st=found
      ? '<span class="st '+(a.state==='ready'?'live':'dead')+'">'+
        (a.state==='ready'?'готов':(a.note||'не настроен'))+'</span>'
      : '<span class="st '+(a.live?'live':'dead')+'">'+(a.live?'связь есть':(a.note||'не отвечает'))+'</span>'+
        (a.default?'<span class="st def">основной</span>':'');
    const sub=[a.profile&&('профиль: '+a.profile),a.url||a.command||''].filter(Boolean).join(' · ');
    let btns='';
    if(found){
      btns=a.state==='ready'
        ? '<button data-a="add">подключить</button>'
        : '<button data-a="how">как включить</button>';
    }else{
      btns+='<button data-a="enable">'+(a.enabled?'отключить':'включить')+'</button>';
      if(!a.default&&a.enabled)btns+='<button data-a="default">сделать основным</button>';
      btns+='<button data-a="remove">убрать</button>';
    }
    return '<div class="ag" data-id="'+(a.id||'')+'" data-found="'+(found?1:0)+'">'+
      '<div class="col"><div class="nm">'+a.name+'</div>'+
      (sub?'<div class="sub">'+sub+'</div>':'')+'</div>'+st+btns+'</div>';
  };

  const draw=d=>{
    agList.innerHTML=(d.agents||[]).map(a=>card(a,false)).join('')||
      '<div class="ag-empty">Ни одного агента не подключено.</div>';
    const fb=$('#ag-found');
    fb.innerHTML=(d.found||[]).map(a=>card(a,true)).join('')||
      '<div class="ag-empty">Новых профилей Hermes не найдено.</div>';
    $('#ag-where').textContent='Реестр: '+d.registry+
      (d.hermes?('   ·   Hermes: '+d.hermes):'');
    window.__found=d.found||[];
  };

  const load=async()=>{
    try{
      const r=await fetch('/api/agents');
      draw(await r.json());
    }catch(e){agList.innerHTML='<div class="ag-empty">Сервер не ответил.</div>'}
  };

  document.addEventListener('click',async e=>{
    const b=e.target.closest('.ag button[data-a]');
    if(!b)return;
    const row=b.closest('.ag'), id=row.dataset.id;
    const a=b.dataset.a;
    if(a==='how'){
      const f=(window.__found||[]).find(x=>x.id===id)||{};
      alert('Чтобы подключить «'+(f.name||id)+'», в его профиле Hermes нужно '+
        'включить webhook и создать подписку на книгу, затем перезапустить шлюз:\n\n'+
        'platforms:\n  webhook:\n    enabled: true\n    extra:\n      host: 127.0.0.1\n'+
        '      port: <свободный порт>\n      secret: <секрет>\n\n'+
        'hermes gateway restart --profile '+(f.profile||'<профиль>'));
      return;
    }
    if(a==='add'){
      const f=(window.__found||[]).find(x=>x.id===id);
      if(!f)return;
      if(await post({act:'add',id:f.id,name:f.name,kind:f.kind,
        profile:f.profile,url:f.url,secret:f.secret,enabled:true}))load();
      return;
    }
    if(a==='enable'){
      const on=b.textContent.trim()==='включить';
      if(await post({act:'enable',id,on}))load();
      return;
    }
    if(a==='default'){if(await post({act:'default',id}))load();return}
    if(a==='remove'){
      if(!confirm('Убрать агента из реестра? Привязки к документам останутся.'))return;
      if(await post({act:'remove',id}))load();
    }
  });

  load();

  /* добавление агента вручную: нужно, если агент на другой машине или не Hermes */
  const nfMsg=$('#nf-msg');
  const nfSay=(t,cls)=>{nfMsg.textContent=t;nfMsg.className='ag-msg'+(cls?' '+cls:'')};
  const nfData=()=>({
    name:($('#nf-name').value||'').trim(),
    url:($('#nf-url').value||'').trim(),
    secret:($('#nf-secret').value||'').trim(),
  });

  $('#nf-check')?.addEventListener('click',async()=>{
    const d=nfData();
    if(!d.url){nfSay('нужен адрес webhook','bad');return}
    nfSay('проверяю…');
    try{
      const r=await fetch('/api/agents',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({act:'check',url:d.url,secret:d.secret})});
      const j=await r.json();
      nfSay(j.message||(j.live?'связь есть':'не отвечает'),j.live?'ok':'bad');
    }catch(e){nfSay('сервер не ответил','bad')}
  });

  $('#nf-add')?.addEventListener('click',async e=>{
    const d=nfData();
    if(!d.name){nfSay('нужно имя','bad');return}
    if(!d.url){nfSay('нужен адрес webhook','bad');return}
    e.currentTarget.disabled=true;
    const ok=await post({act:'add',name:d.name,url:d.url,secret:d.secret,
      kind:'hermes-webhook',enabled:true});
    e.currentTarget.disabled=false;
    if(ok){
      nfSay('добавлен: '+d.name,'ok');
      $('#nf-name').value='';$('#nf-url').value='';$('#nf-secret').value='';
      load();
    }
  });
}

/* ---------------------------------------------- выгрузка в ENOT */
const syList=$('#sy-list');
if(syList){
  const msg=$('#sy-msg');
  const say2=(t,cls)=>{msg.textContent=t;msg.className='ag-msg'+(cls?' '+cls:'')};
  const RU={changed:'изменён',new:'нет в ENOT',same:'совпадает',
    'no-id':'нет коллекции',empty:'папка без текста'};

  const row=r=>{
    const can=r.state==='changed'||r.state==='new';
    return '<div class="sy" data-rel="'+encodeURIComponent(r.rel)+'">'+
      '<div class="col"><div class="nm">'+r.title+'</div>'+
      '<div class="sub">'+r.rel+(r.synced_at?('  ·  выгружено '+r.synced_at):'')+'</div></div>'+
      '<span class="st '+r.state+'">'+(RU[r.state]||r.state)+'</span>'+
      (can?'<button data-a="push">выгрузить</button>':'')+'</div>';
  };

  const draw=d=>{
    const c=d.counts||{};
    $('#sy-counts').innerHTML=
      ['changed','new','same','no-id','empty'].filter(k=>c[k]).map(
        k=>'<span class="'+k+'">'+(RU[k]||k)+': '+c[k]+'</span>').join('')+
      (d.key?'':'<span class="changed">ключ доступа не найден</span>');
    const rows=(d.rows||[]).filter(r=>r.state==='changed'||r.state==='new');
    syList.innerHTML=rows.map(row).join('')||
      '<div class="ag-empty">Всё совпадает с ENOT.</div>';
    window.__syPending=rows.filter(r=>r.state==='changed'||r.state==='new')
      .map(r=>r.rel);

    /* документы без коллекции: группируем по папке — указывать её
       каждому файлу отдельно было бы двадцать нажатий вместо одного */
    const orphans=(d.rows||[]).filter(r=>r.state==='no-id');
    const byDir={};
    orphans.forEach(r=>{
      const dir=r.rel.includes('/')?r.rel.slice(0,r.rel.lastIndexOf('/')):'.';
      (byDir[dir]=byDir[dir]||[]).push(r);
    });
    const ob=$('#sy-orphans');
    const dirs=Object.keys(byDir).sort();
    ob.innerHTML=dirs.length?dirs.map(dir=>
      '<div class="sy" data-dir="'+encodeURIComponent(dir)+'">'+
      '<div class="col"><div class="nm">'+(dir==='.'?'корень базы':dir)+'</div>'+
      '<div class="sub">документов: '+byDir[dir].length+'</div></div>'+
      '<button data-a="bind">указать коллекцию</button></div>').join(''):
      '<div class="ag-empty">Все документы знают свою коллекцию.</div>';
  };

  const loadSync=async()=>{
    say2('смотрю базу…');
    try{
      const r=await fetch('/api/sync');
      const d=await r.json();
      draw(d);
      say2('');
    }catch(e){syList.innerHTML='<div class="ag-empty">Сервер не ответил.</div>'}
  };

  const pushOne=async(rel,btn)=>{
    if(btn){btn.disabled=true;btn.textContent='выгружаю…'}
    try{
      const r=await fetch('/api/sync',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({p:rel})});
      const j=await r.json();
      if(!r.ok||!j.ok){
        say2(j.error||'не вышло','bad');
        if(btn){btn.disabled=false;btn.textContent='выгрузить'}
        return false;
      }
      say2(j.message||'выгружено','ok');
      return true;
    }catch(e){
      say2('сервер не ответил','bad');
      if(btn){btn.disabled=false;btn.textContent='выгрузить'}
      return false;
    }
  };

  syList.addEventListener('click',async e=>{
    const b=e.target.closest('button[data-a="push"]');
    if(!b)return;
    const rel=decodeURIComponent(b.closest('.sy').dataset.rel);
    if(await pushOne(rel,b))loadSync();
  });

  $('#sy-reload')?.addEventListener('click',loadSync);

  $('#sy-all')?.addEventListener('click',async e=>{
    const list=window.__syPending||[];
    if(!list.length){say2('выгружать нечего');return}
    if(!confirm('Выгрузить в ENOT '+list.length+' документов?'))return;
    e.currentTarget.disabled=true;
    let ok=0;
    for(let i=0;i<list.length;i++){
      say2('выгружаю '+(i+1)+' из '+list.length+'…');
      if(await pushOne(list[i],null))ok++;
    }
    e.currentTarget.disabled=false;
    say2('выгружено: '+ok+' из '+list.length,ok===list.length?'ok':'bad');
    loadSync();
  });

  /* коллекции: список для выбора и создание новой */
  const collSel=$('#sy-coll');
  const loadColls=async()=>{
    try{
      const r=await fetch('/api/collections');
      const d=await r.json();
      if(!d.ok){collSel.innerHTML='<option value="">'+(d.error||'не вышло')+'</option>';return}
      collSel.innerHTML='<option value="">— выберите коллекцию —</option>'+
        (d.items||[]).map(c=>'<option value="'+c.id+'">'+c.name+'</option>').join('');
    }catch(e){collSel.innerHTML='<option value="">сервер не ответил</option>'}
  };

  $('#sy-mkcoll')?.addEventListener('click',async e=>{
    const name=($('#sy-newcoll').value||'').trim();
    if(!name){say2('нужно имя коллекции','bad');return}
    e.currentTarget.disabled=true;
    try{
      const r=await fetch('/api/collections',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({act:'create',name})});
      const j=await r.json();
      if(!r.ok||!j.ok){say2(j.error||'не вышло','bad')}
      else{
        say2('создана коллекция: '+(j.item&&j.item.name||name),'ok');
        $('#sy-newcoll').value='';
        await loadColls();
        if(j.item&&j.item.id)collSel.value=j.item.id;
      }
    }catch(err){say2('сервер не ответил','bad')}
    e.currentTarget.disabled=false;
  });

  $('#sy-orphans')?.addEventListener('click',async e=>{
    const b=e.target.closest('button[data-a="bind"]');
    if(!b)return;
    const cid=collSel.value;
    if(!cid){say2('сначала выберите коллекцию','bad');return}
    const dir=decodeURIComponent(b.closest('.sy').dataset.dir);
    b.disabled=true;
    try{
      const r=await fetch('/api/collections',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({act:'bind',p:dir==='.'?'':dir,collection:cid})});
      const j=await r.json();
      if(!r.ok||!j.ok){say2(j.error||'не вышло','bad');b.disabled=false;return}
      say2('коллекция указана документам: '+j.count,'ok');
      loadSync();
    }catch(err){say2('сервер не ответил','bad');b.disabled=false}
  });

  loadColls();
  loadSync();
}
})();
"""


def count_docs(nodes):
    n = 0
    for x in nodes:
        n += 1 if x['kind'] == 'doc' else count_docs(x.get('children') or [])
    return n


# Большие папки (бэкап ENOT) в дерево сразу не разворачиваем: иначе страница
# весит полмегабайта. Их содержимое подгружается по клику через /api/branch.
LAZY_OVER = 25


def tree_html(nodes, active, lazy=True, reg=None):
    reg = reg if reg is not None else registry.load(CFG)
    out = []
    for n in nodes:
        if n['kind'] == 'folder':
            kids = n.get('children') or []
            on_path = active and active.startswith(n['rel'] + '/')
            heavy = lazy and not on_path and count_docs(kids) > LAZY_OVER
            inner = ('<div class="kids" data-lazy="1">'
                     '<span class="load">загружаю…</span></div>' if heavy
                     else f'<div class="kids">{tree_html(kids, active, lazy, reg)}</div>')
            own = binding.of_collection(safe_path(n['rel']) or '')
            tag = (f'<span class="who c" title="агент коллекции">'
                   f'{html.escape(registry.name_of(own, reg))}</span>' if own else '')
            out.append(
                f'<details class="grp" data-rel="{html.escape(n["rel"], quote=True)}"'
                f'{" open" if on_path else ""}>'
                f'<summary><span class="cv">&#9654;</span>'
                f'<span class="nm">{html.escape(n["title"])}</span>{tag}'
                f'<button class="dots" data-rel="{html.escape(n["rel"], quote=True)}" '
                f'data-kind="collection" title="Действия">&#8943;</button>'
                f'</summary>{inner}</details>')
        else:
            cls = 'dc on' if n['rel'] == active else 'dc'
            bg = ''
            own = binding.of_document(safe_path(n['rel']) or '')
            if own:
                bg += (f'<span class="who" title="агент документа">'
                       f'{html.escape(registry.name_of(own, reg))}</span>')
            if n['marks']:
                bg += f'<span class="bg s">{n["marks"]}</span>'
            kb = n['chars'] // 1000
            if kb:
                bg += f'<span class="bg">{kb}k</span>'
            out.append(
                f'<div class="dcw">'
                f'<a class="{cls}" href="/doc?p={urllib.parse.quote(n["rel"])}" '
                f'data-t="{html.escape(n["title"], quote=True)}">'
                f'<span class="nm">{html.escape(n["title"])}</span>{bg}</a>'
                f'<button class="dots" data-rel="{html.escape(n["rel"], quote=True)}" '
                f'data-kind="doc" title="Действия">&#8943;</button></div>')
    return ''.join(out)


def map_html(toc):
    if not toc:
        return ''
    rows = ''.join(
        f'<a href="#{a}" data-h="{a}" style="padding-left:{11 + (lvl - 2) * 12}px">'
        f'{html.escape(t)}</a>' for a, t, lvl in toc)
    return f'<nav class="map"><div class="map-t">В этой главе</div>{rows}</nav>'


def vers_html(rel, can_write, cur_v, fam, pending=0):
    """Полоса версий: переключение, копия и отправка замечаний в работу."""
    d = os.path.dirname(rel)
    items = []
    for v, fn in fam:
        r = (d + '/' + fn) if d else fn
        label = notes.vstr(v)
        if v == cur_v:
            items.append(f'<span class="cur">{label}</span>')
        else:
            items.append(f'<a href="/doc?p={urllib.parse.quote(r)}">{label}</a>')
    tail = ''
    if can_write:
        tail = '<span class="sep"></span><button id="v-new">создать копию</button>'
        if pending:
            tail += (f'<button class="send" id="v-send">отправить в работу'
                     f' · {pending}</button>')
    return f'<div class="vers">{"".join(items)}{tail}</div>'


def shell(title, tree, active, head, body, toc, nav, can_write=False,
          hist_btn=False):
    prev_js = f'window.__prev={json.dumps(nav[0])};' if nav[0] else ''
    next_js = f'window.__next={json.dumps(nav[1])};' if nav[1] else ''
    doc_js = f'window.__doc={json.dumps(active)};' if active else 'window.__doc=null;'
    hb = ('<button class="tb" id="t-hist" title="История документа">&#8635;</button>'
          if hist_btn else '')
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{theme.CSS}</style></head>
<body class="theme-dark font-serif" data-write="{'1' if can_write else '0'}">
<div class="app">
  <header class="top">
    <div class="brand"><div class="logo"></div><b>Новое время</b><span>· читалка</span></div>
    <input class="find" id="find" placeholder="Поиск по названиям">
    <div class="tools">
      <button class="tb" id="t-less" title="Меньше шрифт">&#8722;</button>
      <button class="tb" id="t-more" title="Больше шрифт">&#43;</button>
      <button class="tb" id="t-font" title="Шрифт: с засечками / без">Aa</button>
      <button class="tb" id="t-wide" title="Ширина колонки">&#8596;</button>
      <button class="tb" id="t-map" title="Карта главы">&#9776;</button>
      {hb}
      <a class="tb" href="/agents" title="Агенты: кто берёт замечания в работу">&#9737;</a>
      <a class="tb" href="/sync" title="Выгрузка в ENOT">&#8593;E</a>
      <button class="tb" id="t-theme" title="Тема">&#9681;</button>
      <button class="tb" id="burger" title="Оглавление">&#9636;</button>
    </div>
    <div class="progress" id="progress"></div>
  </header>
  <nav class="side">{tree}</nav>
  <main class="main">
    <div class="wrap">
      <article class="doc">{head}{body}</article>
      {map_html(toc)}
    </div>
  </main>
</div>
<div class="selbar" id="selbar">
  <button id="sb-copy">Копировать</button>
  <button id="sb-quote">Цитата</button>
</div>
<div class="updown" id="updown">
  <button id="ud-up" title="В начало главы (Home)">&#8593;</button>
  <button id="ud-down" title="В конец главы (End)">&#8595;</button>
</div>
<div class="hist" id="hist"></div>
<div class="job" id="job"></div>
<div class="toast" id="toast"></div>
<script>{doc_js}{prev_js}{next_js}{JS}</script>
</body></html>"""


def doc_head(meta, text, rel, sources, kicker, for_export=False,
             can_write=False, cur_v=(1, 0), fam=None, frozen_msg='',
             pending=0):
    title = meta.get('title') or os.path.basename(rel)[:-3]
    # «Глава 2. Что такое киберсоциализм_1.1» -> надзаголовок и имя
    m = re.match(r'^(Глава\s+\d+)\.\s*(.+)$', title)
    over = kicker
    name = title
    if m:
        over = m.group(1) + (' · ' + kicker if kicker else '')
        name = m.group(2)
    name = re.sub(r'_\d+\.\d+\s*$', '', name)
    name = re.sub(r'\s*—\s*(черновик|версия|v)\s*[\d.]+\s*$', '', name, flags=re.I)

    sub = ''
    blocks = structure.parse(open(safe_path(rel), encoding='utf-8').read())
    for b in blocks:
        if b['t'] == 'meta':
            sub = b['text']
            break

    over_html = f'<div class="kicker">{html.escape(over)}</div>' if over else ''
    sub_html = f'<div class="meta-line">{html.escape(sub)}</div>' if sub else ''

    if for_export:
        # в веб-версии книги служебные плашки не нужны
        return (f'<header class="head">{over_html}<h1>{html.escape(name)}</h1>'
                f'{sub_html}</header>')

    facts = []
    words = len(re.findall(r'\b[\w-]+\b', text))
    facts.append(f'<span class="fact">{words} слов</span>')
    facts.append(f'<span class="fact">≈{max(1, round(words / 180))} мин чтения</span>')
    if sources:
        facts.append(f'<span class="fact">источников: {len(sources)}</span>')
    if meta.get('url_id'):
        facts.append(
            f'<span class="fact"><a target="_blank" rel="noopener" '
            f'href="https://anewera.yonote.ru/doc/{html.escape(meta["url_id"])}">'
            f'ENOT</a></span>')
    facts.append(f'<span class="fact"><a href="/raw?p={urllib.parse.quote(rel)}">'
                 f'исходник</a></span>')
    facts.append(f'<span class="fact"><a href="/export?p={urllib.parse.quote(rel)}" '
                 f'target="_blank">HTML для сайта</a></span>')

    # кто отвечает за документ: видно сразу, кому уйдёт кнопка «в работу»
    who = binding.label(safe_path(rel) or rel, BASE)
    if who['name']:
        tail = ' (от коллекции)' if who['inherited'] and who['source'] == 'collection' else ''
        if who['source'] == 'default':
            tail = ' (по умолчанию)'
        facts.append(f'<span class="fact">агент: {html.escape(who["name"])}'
                     f'{tail}</span>')

    vers = vers_html(rel, can_write, cur_v,
                     fam or [(cur_v, os.path.basename(rel))], pending)
    frozen = f'<div class="frozen">{html.escape(frozen_msg)}</div>' if frozen_msg else ''

    return (f'<header class="head">{over_html}<h1>{html.escape(name)}</h1>'
            f'{sub_html}<div class="facts">{"".join(facts)}</div>'
            f'{vers}</header>{frozen}')


def agents_page():
    """Страница «Агенты»: реестр, найденные профили, привязка по умолчанию.

    Содержимое рисуется скриптом по /api/agents — так страница показывает
    живое состояние связи, а не то, что было на момент запроса HTML.
    """
    return ('<div class="ag-page">'
            '<h1>Агенты</h1>'
            '<p class="ag-lead">Кто может брать замечания в работу. Агент '
            'привязывается к документу или к коллекции — кнопка «отправить '
            'в работу» отправляет тому, кто привязан.</p>'
            '<div id="ag-list" class="ag-list">загружаю…</div>'
            '<h2 class="ag-h">Найдены на этой машине</h2>'
            '<div id="ag-found" class="ag-list"></div>'
            '<h2 class="ag-h">Добавить вручную</h2>'
            '<div class="ag-form">'
            '<p class="ag-hint">Нужно, если агент живёт на другой машине или '
            'это не Hermes. Секрет сохраняется в файл .env рядом с настройками, '
            'в реестр попадает только ссылка на него.</p>'
            '<label>Имя<input id="nf-name" placeholder="Гера"></label>'
            '<label>Адрес webhook'
            '<input id="nf-url" placeholder="http://127.0.0.1:8645/webhooks/gera-book">'
            '</label>'
            '<label>Секрет подписи'
            '<input id="nf-secret" type="password" placeholder="из настроек Hermes">'
            '</label>'
            '<div class="ag-row">'
            '<button id="nf-check">проверить связь</button>'
            '<button id="nf-add" class="prim">добавить</button>'
            '<span id="nf-msg" class="ag-msg"></span>'
            '</div></div>'
            '<p class="ag-note" id="ag-where"></p>'
            '</div>')


def sync_page():
    """Страница выгрузки в ENOT: что расходится и что отправить.

    Поток односторонний: файлы -> ENOT. Версии в базе неизменяемы, а документ
    в ENOT правится в браузере, поэтому обратная выгрузка неизбежно приводила
    бы к конфликтам. Файлы — источник правды, ENOT — витрина.
    """
    return ('<div class="ag-page">'
            '<h1>Выгрузка в ENOT</h1>'
            '<p class="ag-lead">Файлы — источник правды, ENOT — витрина. '
            'Выгрузка идёт в одну сторону: из базы в ENOT. Что изменилось после '
            'прошлой выгрузки, видно по отпечатку текста — без обращения к сети.</p>'
            '<p class="ag-hint">Документ не правится, а заменяется: создаётся '
            'новый, прежний уходит в архив. Иначе ENOT срезает разметку — это '
            'поведение его API, проверено опытом. Прямая ссылка на документ '
            'после выгрузки меняется.</p>'
            '<div class="ag-row" id="sy-top">'
            '<button id="sy-reload">обновить список</button>'
            '<button id="sy-all" class="prim">выгрузить всё изменённое</button>'
            '<span id="sy-msg" class="ag-msg"></span>'
            '</div>'
            '<div id="sy-counts" class="sy-counts"></div>'
            '<div id="sy-list" class="ag-list">загружаю…</div>'
            '<h2 class="ag-h">Документы без коллекции</h2>'
            '<p class="ag-hint">Файлы, которые родились локально, а не пришли '
            'из ENOT: выгружать их некуда, пока не указана коллекция. Выберите '
            'её для всей папки сразу.</p>'
            '<div class="ag-form" id="sy-noid">'
            '<label>Коллекция ENOT'
            '<select id="sy-coll"><option value="">загружаю…</option></select>'
            '</label>'
            '<div class="ag-row">'
            '<input id="sy-newcoll" placeholder="или создать новую: имя коллекции">'
            '<button id="sy-mkcoll">создать</button>'
            '</div>'
            '<div id="sy-orphans" class="ag-list"></div>'
            '</div>'
            '</div>')


def export_page(title, head, body):
    """Отдельная страница главы для переноса в веб-версию книги."""
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{theme.CSS}
body{{overflow:auto}}
.page{{max-width:44rem;margin:0 auto;padding:40px 26px 120px}}
article.doc{{padding:0;font-size:19px}}
</style></head>
<body class="theme-light font-serif">
<div class="page"><article class="doc">{head}{body}</article></div>
</body></html>"""


KIND_RU = {
    'version_created': ('создана версия', 'ver'),
    'note_added': ('замечание', 'add'),
    'note_updated': ('замечание изменено', 'add'),
    'note_deleted': ('замечание удалено', 'add'),
    'edit_made': ('правка', 'edit'),
    'uploaded': ('залито в ENOT', 'ver'),
}


def history_html(full):
    """Паспорт документа: лента событий по всем версиям, свежие сверху."""
    h = notes.load_history(full)
    events = list(reversed(h.get('events') or []))
    if not events:
        return ('<h3>История документа</h3><div class="ev">'
                'Пока ничего не происходило.</div>')
    rows = []
    for ev in events:
        label, cls = KIND_RU.get(ev.get('kind'), (ev.get('kind', '?'), ''))
        det = []
        if ev.get('quote'):
            det.append('«' + html.escape(ev['quote']) + '»')
        if ev.get('comment'):
            det.append(html.escape(ev['comment']))
        if ev.get('from_note'):
            det.append('по замечанию ' + html.escape(str(ev['from_note'])[1:]))
        if ev.get('why'):
            det.append(html.escape(ev['why']))
        if ev.get('from_version'):
            det.append(f'из v{ev["from_version"]}')
        if ev.get('status'):
            det.append('статус: ' + html.escape(str(ev['status'])))
        body = ('<div class="d">' + ' · '.join(det) + '</div>') if det else ''
        rows.append(
            f'<div class="ev"><div class="t">{theme.when(ev.get("at", ""))} '
            f'· v{ev.get("version", "?")}</div>'
            f'<span class="k {cls}">{html.escape(label)}</span>{body}</div>')
    return (f'<h3>История: {html.escape(h.get("base", ""))}</h3>'
            + ''.join(rows))


# ------------------------------------------------------------------ сервер


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'BookReader/2.0'

    def log_message(self, fmt, *args):
        pass

    def _send(self, data, ctype='text/html; charset=utf-8', code=200):
        if isinstance(data, str):
            data = data.encode('utf-8')
        try:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # браузер ушёл со страницы, не дочитав ответ — это не ошибка
            self.close_connection = True

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        tree = build_tree()
        docs = flat_docs(tree)

        if u.path in ('/', '/index.html'):
            if docs:
                self.send_response(302)
                self.send_header('Location', '/doc?p=' + urllib.parse.quote(docs[0]['rel']))
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self._send(shell('Читалка', tree_html(tree, None), None, '',
                             '<div class="empty">В базе нет документов.</div>',
                             [], (None, None)))
            return

        if u.path == '/api/tree':
            self._send(json.dumps(tree, ensure_ascii=False),
                       'application/json; charset=utf-8')
            return

        if u.path == '/api/branch':
            rel = (qs.get('p') or [''])[0]

            def find(nodes):
                for x in nodes:
                    if x['kind'] == 'folder':
                        if x['rel'] == rel:
                            return x
                        got = find(x.get('children') or [])
                        if got:
                            return got
                return None

            node = find(tree)
            if not node:
                self._send('', code=404)
                return
            # ветку раскрываем на один уровень: вложенные большие папки
            # снова остаются ленивыми, иначе бэкап ENOT весит полмегабайта
            self._send(tree_html(node.get('children') or [], None, lazy=True))
            return

        if u.path in ('/doc', '/raw', '/export'):
            rel = (qs.get('p') or [''])[0]
            full = safe_path(rel)
            if not full or not os.path.isfile(full):
                self._send(shell('Не найдено', tree_html(tree, None), None, '',
                                 '<div class="empty">Документ не найден.</div>',
                                 [], (None, None)), code=404)
                return

            meta, text = read_doc(full)
            if u.path == '/raw':
                self._send(text, 'text/plain; charset=utf-8')
                return

            sources = collect_sources(text)
            blocks = structure.parse(open(full, encoding='utf-8').read())

            # /export — готовый кусок для веб-версии книги: только статья и CSS
            if u.path == '/export':
                body, _ = theme.render(blocks, sources)
                title = meta.get('title') or os.path.basename(full)[:-3]
                head = doc_head(meta, text, rel, sources, '', for_export=True)
                if (qs.get('css') or ['1'])[0] == '0':
                    self._send(f'<article class="doc">{head}{body}</article>',
                               'text/plain; charset=utf-8')
                else:
                    self._send(export_page(title, head, body),
                               'text/html; charset=utf-8')
                return

            ann = notes.annotations(full, blocks)
            body, toc = theme.render(blocks, sources, ann)

            # соседние документы для стрелок
            idx = next((i for i, d in enumerate(docs) if d['rel'] == rel), None)
            prev_url = next_url = None
            kicker = ''
            if idx is not None:
                if idx > 0:
                    prev_url = '/doc?p=' + urllib.parse.quote(docs[idx - 1]['rel'])
                if idx + 1 < len(docs):
                    next_url = '/doc?p=' + urllib.parse.quote(docs[idx + 1]['rel'])
            parts = rel.split('/')
            if len(parts) > 1:
                kicker = parts[0]

            # версии: писать можно только в последнюю, и только с этой машины
            d, fn = os.path.split(full)
            fam = notes.family(d, fn)
            cur_v = notes.version_of(fn)
            is_last, last_v = notes.freeze_state(d, fn)
            local = settings.write_allowed(self.client_address[0], CFG)
            can_write = is_last and local

            frozen = ''
            if not is_last:
                frozen = (f'Это версия {notes.vstr(cur_v)}, только для чтения. '
                          f'Замечания видны, но добавить новые можно '
                          f'в {notes.vstr(last_v)}.')
            elif not local:
                frozen = ('Открыто по сети: чтение доступно, запись — только '
                          'с той машины, где запущена читалка.')

            pending = len(jobs.pending_notes(full)) if can_write else 0
            head = doc_head(meta, text, rel, sources, kicker,
                            can_write=can_write, cur_v=cur_v, fam=fam,
                            frozen_msg=frozen, pending=pending)
            title = meta.get('title') or os.path.basename(full)[:-3]
            self._send(shell(title, tree_html(tree, rel), rel, head, body, toc,
                             (prev_url, next_url), can_write=can_write,
                             hist_btn=True))
            return

        if u.path == '/api/history':
            rel = (qs.get('p') or [''])[0]
            full = safe_path(rel)
            if not full:
                self._send('', code=404)
                return
            self._send(history_html(full))
            return

        if u.path == '/api/job':
            rel = (qs.get('p') or [''])[0]
            full = safe_path(rel)
            if not full:
                self._json({'state': 'none'}, code=404)
                return
            job = jobs.load(full)
            if not job:
                self._json({'state': 'none'})
                return
            done, total = jobs.progress(job)
            job = dict(job)
            job['done'] = done
            job['total'] = total
            job['stale'] = jobs.is_stale(job)
            if job.get('new_doc'):
                nd = os.path.join(os.path.dirname(full), job['new_doc'])
                job['new_rel'] = os.path.relpath(nd, BASE).replace(os.sep, '/')
            self._json(job)
            return

        if u.path == '/api/agents':
            """Реестр агентов: кто подключён, кто найден, кто отвечает."""
            reg = registry.load(CFG)
            known_profiles = {a.get('profile') for a in reg['agents'] if a.get('profile')}
            known_urls = {a.get('url') for a in reg['agents'] if a.get('url')}

            out = []
            for a in reg['agents']:
                ok, msg = agents.check(a) if a.get('enabled', True) else (False, 'отключён')
                out.append({
                    'id': a['id'], 'name': a['name'],
                    'kind': a.get('kind', ''), 'profile': a.get('profile', ''),
                    'url': a.get('url', ''), 'enabled': a.get('enabled', True),
                    'default': a['id'] == reg['default'],
                    'live': ok, 'note': msg,
                })

            found = []
            for f in discover.as_agents():
                if f['profile'] in known_profiles or (f['url'] and f['url'] in known_urls):
                    continue
                found.append(f)

            self._json({'agents': out, 'found': found,
                        'default': reg['default'],
                        'registry': registry.reg_path(),
                        'hermes': discover.hermes_root()})
            return

        if u.path == '/api/bind':
            """Кто отвечает за документ или коллекцию."""
            rel = (qs.get('p') or [''])[0]
            target = safe_path(rel)
            if not target or not os.path.exists(target):
                self._json({'ok': False, 'error': 'Не найдено'}, code=404)
                return
            reg = registry.load(CFG)
            if os.path.isdir(target):
                own = binding.of_collection(target)
                self._json({'ok': True, 'kind': 'collection', 'own': own,
                            'name': registry.name_of(own, reg) if own else '',
                            'list': [{'id': a['id'], 'name': a['name']}
                                     for a in registry.enabled(reg)]})
                return
            who = binding.label(target, BASE)
            self._json({'ok': True, 'kind': 'doc',
                        'own': binding.of_document(target),
                        'agent': who['id'], 'name': who['name'],
                        'source': who['source'], 'inherited': who['inherited'],
                        'list': [{'id': a['id'], 'name': a['name']}
                                 for a in registry.enabled(reg)]})
            return

        if u.path == '/agents':
            self._send(shell('Агенты', tree_html(tree, None), None, '',
                             agents_page(), [], (None, None)))
            return

        if u.path == '/sync':
            self._send(shell('ENOT', tree_html(tree, None), None, '',
                             sync_page(), [], (None, None)))
            return

        if u.path == '/api/sync':
            """Что расходится с ENOT. По одному документу или по всей базе."""
            rel = (qs.get('p') or [''])[0]
            if rel:
                full = safe_path(rel)
                if not full or not os.path.isfile(full):
                    self._json({'ok': False, 'error': 'Документ не найден'}, code=404)
                    return
                st, meta = sync_yonote.state_of(full)
                self._json({'ok': True, 'state': st,
                            'url': sync_yonote.doc_url(full, CFG),
                            'synced_at': (meta.get('synced_at') or ''),
                            'key': bool(sync_yonote.api_key(CFG))})
                return
            rows = sync_yonote.survey(BASE)
            counts = {}
            for r in rows:
                counts[r['state']] = counts.get(r['state'], 0) + 1
            self._json({'ok': True, 'rows': rows, 'counts': counts,
                        'key': bool(sync_yonote.api_key(CFG))})
            return

        if u.path == '/api/collections':
            """Коллекции ENOT: куда выгружать документы без отметок."""
            items, err = sync_yonote.collections(CFG)
            if err:
                self._json({'ok': False, 'error': err}, code=502)
                return
            self._json({'ok': True, 'items': items})
            return

        self._send('<h1>404</h1>', code=404)

    # ------------------------------------------------------------ запись

    def _guard(self):
        """Писать разрешено с этой машины и с доверенных адресов сети."""
        if not settings.write_allowed(self.client_address[0], CFG):
            self._json({'ok': False,
                        'error': 'Запись с этого адреса не разрешена. '
                                 'Добавьте его в write_allow в config.json'},
                       code=403)
            return False
        return True

    def handle_one_request(self):
        """То же, что в родителе, но без простыни на закрытое соединение.

        Браузер закрывает соединение когда захочет: перешли на другую страницу,
        отменили запрос, сработал keep-alive. Windows отвечает на это
        ConnectionAbortedError (WinError 10053), и стандартный сервер печатает
        полный traceback. Ошибки тут нет, а Андрей видит в окне пугающую
        простыню и думает, что читалка сломалась.
        """
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False),
                   'application/json; charset=utf-8', code)

    def _body(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
            return json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
        except (ValueError, TypeError):
            return {}

    def _target(self, rel):
        """Файл документа плюс проверка, что он последняя версия."""
        full = safe_path(rel or '')
        if not full or not os.path.isfile(full):
            self._json({'ok': False, 'error': 'Документ не найден'}, code=404)
            return None
        d, fn = os.path.split(full)
        ok, last = notes.freeze_state(d, fn)
        if not ok:
            self._json({'ok': False,
                        'error': f'Версия только для чтения — пишите в v{last}'},
                       code=409)
            return None
        return full

    def do_POST(self):
        if not self._guard():
            return
        u = urllib.parse.urlparse(self.path)
        data = self._body()

        if u.path == '/api/note':
            full = self._target(data.get('p'))
            if not full:
                return
            blocks = structure.parse(open(full, encoding='utf-8').read())
            bi = data.get('block')
            if not isinstance(bi, int) or bi < 0 or bi >= len(blocks):
                self._json({'ok': False, 'error': 'Не понял, к какому абзацу'},
                           code=400)
                return
            frag = (data.get('text') or '').strip()
            btext = blocks[bi].get('text') or ''
            if not frag or frag not in btext:
                self._json({'ok': False,
                            'error': 'Выделенный текст не найден в абзаце — '
                                     'выделите заново'}, code=400)
                return
            comment = (data.get('comment') or '').strip()
            if not comment:
                self._json({'ok': False, 'error': 'Пустое замечание'}, code=400)
                return
            anchor = {'block': bi, 'text': frag,
                      'occ': int(data.get('occ') or 0),
                      'bhash': notes.text_hash(btext)}
            note = notes.add_note(full, anchor, comment)
            self._json({'ok': True, 'id': note['id']})
            return

        if u.path == '/api/version':
            rel = data.get('p') or ''
            full = safe_path(rel)
            if not full or not os.path.isfile(full):
                self._json({'ok': False, 'error': 'Документ не найден'}, code=404)
                return
            kind = data.get('kind') or 'minor'
            new_path, err = notes.create_version(full, kind=kind)
            if err:
                self._json({'ok': False, 'error': err}, code=409)
                return
            refresh(force=True)
            new_rel = os.path.relpath(new_path, BASE).replace(os.sep, '/')
            self._json({'ok': True, 'rel': new_rel})
            return

        if u.path == '/api/job':
            full = self._target(data.get('p'))
            if not full:
                return
            rel = os.path.relpath(full, BASE).replace(os.sep, '/')
            who = binding.label(full, BASE)
            job, err = jobs.create(full, agent_id=who['id'],
                                   agent_name=who['name'])
            if err:
                self._json({'ok': False, 'error': err}, code=409)
                return
            done, total = jobs.progress(job)
            agent = registry.get(who['id']) if who['id'] else None
            ok, msg = agents.send(agent, rel, job)
            if not ok:
                # задание уже создано и ждёт: агент возьмётся по слову Андрея
                jobs.add_step(full, msg, state='wait')
            self._json({'ok': True, 'total': total, 'notes': job['notes'],
                        'sent': ok, 'message': msg, 'agent': who['name']})
            return

        if u.path == '/api/bind':
            """Привязка агента к документу или к коллекции."""
            rel = (data.get('p') or '').strip()
            target = safe_path(rel)
            if not target or not os.path.exists(target):
                self._json({'ok': False, 'error': 'Не найдено'}, code=404)
                return
            aid = (data.get('agent') or '').strip()
            if aid and not registry.get(aid):
                self._json({'ok': False, 'error': 'Такого агента нет'}, code=400)
                return

            is_dir = os.path.isdir(target)
            if aid:
                if is_dir:
                    binding.bind_collection(target, aid)
                else:
                    binding.bind_document(target, aid)
            else:
                if is_dir:
                    binding.unbind_collection(target)
                else:
                    binding.unbind_document(target)

            if is_dir:
                name = registry.name_of(aid) if aid else ''
                self._json({'ok': True, 'agent': aid, 'name': name,
                            'inherited': False})
            else:
                who = binding.label(target, BASE)
                self._json({'ok': True, 'agent': who['id'], 'name': who['name'],
                            'inherited': who['inherited'],
                            'source': who['source']})
            return

        if u.path == '/api/agents':
            """Настройки агентов: добавить, включить, назначить основным."""
            act = (data.get('act') or '').strip()
            aid = (data.get('id') or '').strip()

            if act == 'add':
                agent = {
                    'id': aid or registry.make_id(data.get('name') or ''),
                    'name': (data.get('name') or '').strip() or 'Агент',
                    'kind': (data.get('kind') or 'hermes-webhook').strip(),
                    'profile': (data.get('profile') or '').strip(),
                    'url': (data.get('url') or '').strip(),
                    'secret': (data.get('secret') or '').strip(),
                    'command': (data.get('command') or '').strip(),
                    'enabled': bool(data.get('enabled', True)),
                }
                _, err = registry.upsert(agent)
            elif act == 'check':
                # проверка связи до добавления: агента в реестре ещё нет
                probe = {'name': (data.get('name') or 'агент').strip(),
                         'kind': (data.get('kind') or 'hermes-webhook').strip(),
                         'url': (data.get('url') or '').strip(),
                         'secret': (data.get('secret') or '').strip(),
                         'enabled': True}
                live, msg = agents.check(probe)
                self._json({'ok': True, 'live': live, 'message': msg})
                return
            elif act == 'enable':
                _, err = registry.set_enabled(aid, bool(data.get('on', True)))
            elif act == 'default':
                _, err = registry.set_default(aid)
            elif act == 'remove':
                _, err = registry.remove(aid)
            else:
                err = 'неизвестное действие'

            if err:
                self._json({'ok': False, 'error': err}, code=400)
                return
            self._json({'ok': True})
            return

        if u.path == '/api/sync':
            """Выгрузка документа в ENOT. Только вперёд: файлы -> ENOT."""
            rel = (data.get('p') or '').strip()
            full = safe_path(rel)
            if not full or not os.path.isfile(full):
                self._json({'ok': False, 'error': 'Документ не найден'}, code=404)
                return
            ok, msg = sync_yonote.push(full, CFG, force=bool(data.get('force')))
            if not ok:
                self._json({'ok': False, 'error': msg}, code=502)
                return
            refresh(force=True)
            self._json({'ok': True, 'message': msg,
                        'url': sync_yonote.doc_url(full, CFG)})
            return

        if u.path == '/api/collections':
            """Коллекции: создать новую или указать документу, куда выгружать."""
            act = (data.get('act') or '').strip()

            if act == 'create':
                item, err = sync_yonote.create_collection(
                    data.get('name') or '', CFG,
                    description=(data.get('description') or ''))
                if err:
                    self._json({'ok': False, 'error': err}, code=502)
                    return
                self._json({'ok': True, 'item': item})
                return

            if act == 'bind':
                rel = (data.get('p') or '').strip()
                cid = (data.get('collection') or '').strip()
                if not cid:
                    self._json({'ok': False, 'error': 'не выбрана коллекция'},
                               code=400)
                    return
                # весь каталог сразу: по одному документу указывать коллекцию
                # для папки Docs — двадцать нажатий вместо одного
                target = safe_path(rel)
                if not target or not os.path.exists(target):
                    self._json({'ok': False, 'error': 'Не найдено'}, code=404)
                    return

                files = []
                if os.path.isdir(target):
                    for dirpath, dirnames, filenames in os.walk(target):
                        dirnames[:] = [d for d in dirnames
                                       if not d.startswith(('_', '.'))
                                       and d != 'Backup']
                        files += [os.path.join(dirpath, f)
                                  for f in sorted(filenames)
                                  if f.endswith('.md') and f != 'README.md']
                else:
                    files = [target]

                done = 0
                for f in files:
                    st, _ = sync_yonote.state_of(f)
                    if st == sync_yonote.EMPTY:
                        continue
                    sync_yonote.bind_collection(f, cid,
                                                (data.get('parent') or ''))
                    done += 1
                refresh(force=True)
                self._json({'ok': True, 'count': done})
                return

            self._json({'ok': False, 'error': 'неизвестное действие'}, code=400)
            return

        self._json({'ok': False, 'error': 'Неизвестный запрос'}, code=404)

    def do_PUT(self):
        if not self._guard():
            return
        u = urllib.parse.urlparse(self.path)
        data = self._body()
        if u.path == '/api/note':
            full = self._target(data.get('p'))
            if not full:
                return
            n = notes.update_note(full, data.get('id'),
                                  comment=data.get('comment'),
                                  status=data.get('status'))
            if not n:
                self._json({'ok': False, 'error': 'Замечание не найдено'}, code=404)
                return
            self._json({'ok': True})
            return
        self._json({'ok': False, 'error': 'Неизвестный запрос'}, code=404)

    def do_DELETE(self):
        if not self._guard():
            return
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path == '/api/note':
            full = self._target((qs.get('p') or [''])[0])
            if not full:
                return
            if notes.delete_note(full, (qs.get('id') or [''])[0]):
                self._json({'ok': True})
            else:
                self._json({'ok': False, 'error': 'Замечание не найдено'}, code=404)
            return

        if u.path == '/api/job':
            full = safe_path((qs.get('p') or [''])[0])
            if not full:
                self._json({'ok': False, 'error': 'Документ не найден'}, code=404)
                return
            job = jobs.load(full)
            if not job:
                self._json({'ok': False, 'error': 'Задания нет'}, code=404)
                return
            if job.get('state') in ('queued', 'running'):
                jobs.stop(full, 'прервано вами')
            else:
                jobs.clear(full)
            self._json({'ok': True})
            return

        self._json({'ok': False, 'error': 'Неизвестный запрос'}, code=404)


def local_ips():
    """Адреса этой машины в локальной сети — чтобы подсказать, куда заходить."""
    out = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith('127.') and ip not in out:
                out.append(ip)
    except OSError:
        pass
    return out


def main():
    global BASE

    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=DEFAULT_BASE)
    ap.add_argument('--port', type=int, default=CFG.get('port', 8765))
    ap.add_argument('--host', default=CFG.get('host', '127.0.0.1'))
    a = ap.parse_args()

    if not a.base:
        print('Не задана папка базы документов.')
        print(f'Укажите её в {settings.CONFIG} полем "base"')
        print('или запустите с параметром --base "путь к папке".')
        sys.exit(1)

    BASE = os.path.abspath(a.base)
    if not os.path.isdir(BASE):
        print(f'нет папки базы: {BASE}')
        sys.exit(1)

    print(f'база: {BASE}')
    print('читаю базу…', flush=True)
    t0 = time.time()
    refresh(force=True)
    docs = flat_docs(build_tree())
    print(f'документов: {len(docs)}  ({time.time() - t0:.1f} с)')
    threading.Thread(target=watcher, daemon=True).start()
    print(f'открыть: http://localhost:{a.port}')
    if a.host == '0.0.0.0':
        allow = CFG.get('write_allow') or []
        for ip in local_ips():
            print(f'с других машин: http://{ip}:{a.port}')
        if allow:
            print('запись разрешена: эта машина + ' + ', '.join(allow))
        else:
            print('в локальной сети: чтение доступно, запись — только с этой машины')
    print('Ctrl+C — остановить.', flush=True)

    class Server(ThreadingHTTPServer):
        """Тихий сервер: браузер вправе бросить соединение когда угодно.

        Стандартный ThreadingHTTPServer на каждый обрыв печатает traceback
        (в Windows это ConnectionAbortedError, WinError 10053). Ошибки нет, но
        в окне читалки это выглядит как поломка. Настоящие ошибки по-прежнему
        видны — глушим только обрывы связи.
        """

        daemon_threads = True

        def handle_error(self, request, client_address):
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionAbortedError, ConnectionResetError,
                                BrokenPipeError)):
                return
            super().handle_error(request, client_address)

    Server((a.host, a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
