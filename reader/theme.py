"""Оформление читалки: CSS и превращение блоков в HTML.

Разметка сознательно простая и семантическая: h1/h2/h3, p, blockquote,
ul/ol, aside. Такой HTML можно перенести в веб-версию книги, подключив
только этот CSS — без правок самого текста.
"""
import html
import re

URL_RE = re.compile(r'(https?://[^\s<>"\')\]\x00-\x08]+)')
MARK_RE = re.compile(r'\[(\d+)\]')
# «Киберсоциализм — это ...» — определение: термин до тире выделяем.
# Термин — одно-три слова с большой буквы, без служебных зачинов.
DEFINITION = re.compile(
    r'^((?!Но |И |А |Или |Это |Тут |Там |Здесь |Вот |Если |Когда |Пока )'
    r'[А-ЯЁA-Z][^—.!?,:;]{2,42})\s+—\s+(это\s|не\s|тоже\s)')
# числа с единицами: суммы, проценты, годы, доли
FIGURE = re.compile(
    r'(?<![\w.,-])('
    r'\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?\s?(?:%|процент\w*|миллиард\w*|'
    r'миллион\w*|тысяч\w*|рубл\w*|долл\w*)?|'                # 1 025, 696 500 руб
    r'\d+[.,]\d+\s?(?:%|процент\w*|миллиард\w*|миллион\w*|тысяч\w*|'
    r'рубл\w*|долл\w*)|'                                     # 87,28 %
    r'\d{1,4}\s?(?:%|процент\w*|миллиард\w*|миллион\w*|тысяч\w*|'
    r'рубл\w*|долл\w*)'                                      # 15 процентов
    r')(?![\w-])')

CSS = r"""
:root{
  --bg:#0b0c0e; --bg2:#101215; --panel:#0f1113; --panel2:#16191d;
  --tx:#e9ecf1; --tx2:#b8c0cb; --mut:#868e9b; --dim:#5f6774;
  --line:rgba(255,255,255,.085); --line2:rgba(255,255,255,.16);
  --acc:#7c7bff; --acc2:#a5a4ff; --accsoft:rgba(124,123,255,.14);
  --gold:#e0b357; --green:#37c48b;
  --read:#e4e8ee;
  --serif:Georgia,'PT Serif','Times New Roman',serif;
  --sans:'Segoe UI',Inter,-apple-system,system-ui,Roboto,Arial,sans-serif;
  --body:var(--serif);
  --fs:19px; --lh:1.78; --col:44rem;
  --top:54px; --left:330px;
}
body.theme-light{
  --bg:#faf9f7; --bg2:#f2f1ee; --panel:#fff; --panel2:#f4f4f2;
  --tx:#14171c; --tx2:#3f4650; --mut:#6d7480; --dim:#8d94a0;
  --line:rgba(0,0,0,.09); --line2:rgba(0,0,0,.16);
  --acc:#5654e8; --acc2:#3b3ac9; --accsoft:rgba(86,84,232,.10);
  --gold:#a3762a; --green:#1c8f60;
  --read:#1b1f26;
}
body.theme-sepia{
  --bg:#f3ece0; --bg2:#eae1d2; --panel:#fbf6ec; --panel2:#efe7d9;
  --tx:#2c2519; --tx2:#584c3a; --mut:#7d7160; --dim:#948877;
  --line:rgba(60,45,20,.13); --line2:rgba(60,45,20,.22);
  --acc:#9a5b2c; --acc2:#7c4720; --accsoft:rgba(154,91,44,.12);
  --gold:#8a6520; --green:#3f7a4e;
  --read:#332b1e;
}
body.font-sans{--body:var(--sans); --lh:1.72}

*{box-sizing:border-box}
html{scroll-behavior:smooth}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--tx);overflow:hidden;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
button,input{font:inherit;color:inherit}
button{border:0;background:none;cursor:pointer}
a{color:inherit}
::-webkit-scrollbar{width:11px;height:11px}
::-webkit-scrollbar-thumb{background:rgba(130,135,145,.34);border-radius:99px;
  border:3px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background:rgba(130,135,145,.6);background-clip:content-box}

/* ------------------------------------------------ каркас */
.app{height:100vh;display:grid;
  grid-template-columns:var(--left) minmax(0,1fr);
  grid-template-rows:var(--top) 1fr}
.top{grid-column:1/3;display:flex;align-items:center;gap:13px;padding:0 15px;
  background:color-mix(in srgb,var(--panel) 92%,transparent);
  border-bottom:1px solid var(--line);position:relative;z-index:6;
  backdrop-filter:blur(14px);font-family:var(--sans)}
.brand{display:flex;align-items:center;gap:10px;font-weight:640;font-size:14.5px;
  letter-spacing:-.02em;width:calc(var(--left) - 30px);flex:0 0 auto}
.logo{width:28px;height:28px;border-radius:8px;flex:0 0 auto;
  background:conic-gradient(from 210deg,#7c7bff,#3b82f6,#37c48b,#7c7bff);
  box-shadow:0 0 0 1px rgba(255,255,255,.14) inset}
.brand b{font-weight:640}
.brand span{color:var(--mut);font-weight:500}
.find{flex:1;max-width:340px;height:32px;border:1px solid var(--line);
  border-radius:8px;background:var(--panel2);color:var(--tx2);padding:0 11px;
  outline:0;font-size:13px;font-family:var(--sans)}
.find:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--accsoft)}
.tools{margin-left:auto;display:flex;align-items:center;gap:6px}
.tb{height:31px;min-width:31px;padding:0 9px;border:1px solid var(--line);
  border-radius:8px;background:var(--panel2);font-size:12.5px;color:var(--tx2);
  display:flex;align-items:center;gap:6px;white-space:nowrap}
.tb:hover{border-color:var(--acc);color:var(--acc)}
a.tb{text-decoration:none;justify-content:center}
.tb.on{background:var(--accsoft);border-color:var(--acc);color:var(--acc)}
.progress{position:absolute;left:0;bottom:-1px;height:2px;width:0;
  background:linear-gradient(90deg,var(--acc),var(--acc2));transition:width .1s}

/* ------------------------------------------------ дерево */
.side{grid-column:1;grid-row:2;background:var(--panel);overflow-y:auto;
  border-right:1px solid var(--line);padding:9px 7px 50px;font-family:var(--sans)}
.grp{margin:1px 0}
.grp>summary{list-style:none;min-height:34px;display:flex;align-items:center;
  gap:7px;padding:5px 8px;border-radius:8px;cursor:pointer;font-size:12.5px;
  font-weight:620;color:var(--tx2);letter-spacing:-.01em}
.grp>summary::-webkit-details-marker{display:none}
.grp>summary:hover{background:var(--panel2)}
.cv{color:var(--mut);font-size:9px;transition:transform .18s;flex:0 0 auto}
.grp[open]>summary .cv{transform:rotate(90deg);color:var(--acc)}
.kids{margin:1px 0 5px 13px;padding-left:11px;border-left:1px solid var(--line)}
.dc{display:flex;align-items:flex-start;gap:7px;min-height:32px;padding:6px 9px;
  border-radius:8px;color:var(--tx2);font-size:12.5px;line-height:1.4;
  text-decoration:none}
.dc:hover{background:var(--panel2);color:var(--tx)}
.dc.on{background:var(--accsoft);color:var(--tx);font-weight:560}
.dc .nm{flex:1;min-width:0}

/* документ в дереве плюс кнопка действий: три точки живут рядом со ссылкой,
   а не внутри неё — иначе нажатие на них открывало бы документ */
.dcw{position:relative;display:flex;align-items:center}
.dcw .dc{flex:1;min-width:0}
.dots{position:absolute;right:4px;top:50%;transform:translateY(-50%);
  width:22px;height:22px;border:0;border-radius:6px;background:transparent;
  color:var(--dim);font-size:14px;line-height:1;cursor:pointer;opacity:0;
  display:flex;align-items:center;justify-content:center}
.dcw:hover .dots,.grp>summary:hover .dots,.dots.open{opacity:1}
.dots:hover{background:var(--panel2);color:var(--tx)}
.grp>summary{position:relative;padding-right:28px}
.grp>summary .dots{right:4px}
.grp>summary .nm{flex:1;min-width:0}

/* кто отвечает за документ: имя агента, а не цветная точка —
   цвет ничего не говорит, имя говорит всё */
.who{flex:0 0 auto;font-size:9.5px;padding:2px 6px;border-radius:99px;
  border:1px solid color-mix(in srgb,var(--acc) 45%,transparent);
  color:var(--acc2);white-space:nowrap}
.who.c{margin-left:auto;font-size:9px;opacity:.85}

/* меню действий по документу и коллекции */
.menu{position:fixed;z-index:70;min-width:190px;padding:5px;display:none;
  border:1px solid var(--line2);border-radius:11px;background:var(--panel);
  box-shadow:0 18px 44px rgba(0,0,0,.42);font-family:var(--sans)}
.menu.show{display:block}
.menu .mt{padding:7px 10px 5px;font-size:10px;font-weight:680;color:var(--dim);
  letter-spacing:.1em;text-transform:uppercase}
.menu button{display:flex;width:100%;align-items:center;gap:8px;padding:7px 10px;
  border:0;border-radius:7px;background:transparent;color:var(--tx2);
  font-size:12.5px;text-align:left;cursor:pointer}
.menu button:hover{background:var(--panel2);color:var(--tx)}
.menu button.on{color:var(--acc2)}
.menu button.on::after{content:'\2713';margin-left:auto;color:var(--acc)}
.menu .sep{height:1px;margin:4px 6px;background:var(--line)}

/* страница «Агенты» */
.ag-page{padding:44px 0 120px;font-family:var(--sans);color:var(--tx2)}
.ag-page h1{font-size:clamp(24px,2.6vw,32px);margin:0 0 10px}
.ag-lead{font-size:14px;color:var(--mut);margin:0 0 26px;max-width:44rem;
  line-height:1.6}
.ag-h{font-size:12px;font-weight:680;letter-spacing:.1em;text-transform:uppercase;
  color:var(--dim);margin:34px 0 12px}
.ag-list{display:flex;flex-direction:column;gap:9px}
.ag{display:flex;align-items:center;gap:12px;padding:13px 15px;
  border:1px solid var(--line);border-radius:12px;background:var(--panel)}
.ag .nm{font-size:14px;font-weight:600;color:var(--tx)}
.ag .sub{font-size:11.5px;color:var(--mut);margin-top:3px}
.ag .col{min-width:0;flex:1}
.ag .st{font-size:11px;padding:3px 9px;border-radius:99px;white-space:nowrap;
  border:1px solid var(--line)}
.ag .st.live{color:var(--green);border-color:color-mix(in srgb,var(--green) 40%,transparent)}
.ag .st.dead{color:var(--mut)}
.ag .st.def{color:var(--acc2);border-color:color-mix(in srgb,var(--acc) 45%,transparent)}
.ag button{height:29px;padding:0 11px;border-radius:8px;cursor:pointer;
  border:1px solid var(--line);background:var(--panel2);color:var(--tx2);
  font-size:12px;white-space:nowrap}
.ag button:hover{border-color:var(--acc);color:var(--acc)}
.ag-note{font-size:11.5px;color:var(--dim);margin-top:22px}
.ag-empty{padding:16px;border:1px dashed var(--line2);border-radius:12px;
  color:var(--mut);font-size:13px}

/* форма добавления агента вручную */
.ag-form{padding:16px 18px;border:1px solid var(--line);border-radius:12px;
  background:var(--panel);display:flex;flex-direction:column;gap:11px;
  max-width:44rem}
.ag-hint{margin:0;font-size:12.5px;color:var(--mut);line-height:1.55}
.ag-form label{display:flex;flex-direction:column;gap:5px;font-size:12px;
  color:var(--mut)}
.ag-form input{height:33px;padding:0 11px;border-radius:8px;outline:0;
  border:1px solid var(--line);background:var(--panel2);color:var(--tx);
  font-size:13px;font-family:var(--sans)}
.ag-form input:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--accsoft)}
.ag-row{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.ag-row button{height:31px;padding:0 13px;border-radius:8px;cursor:pointer;
  border:1px solid var(--line);background:var(--panel2);color:var(--tx2);
  font-size:12.5px;font-family:var(--sans)}
.ag-row button:hover{border-color:var(--acc);color:var(--acc)}
.ag-row button.prim{border-color:color-mix(in srgb,var(--acc) 55%,transparent);
  background:var(--accsoft);color:var(--acc2)}
.ag-row button:disabled{opacity:.5;pointer-events:none}
.ag-msg{font-size:12px;color:var(--mut)}
.ag-msg.ok{color:var(--green)}
.ag-msg.bad{color:var(--gold)}

/* выгрузка в ENOT */
.sy-counts{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 14px}
.sy-counts span{font-size:11.5px;padding:4px 10px;border-radius:99px;
  border:1px solid var(--line);color:var(--mut)}
.sy-counts span.changed{color:var(--gold);
  border-color:color-mix(in srgb,var(--gold) 40%,transparent)}
.sy-counts span.new{color:var(--acc2);
  border-color:color-mix(in srgb,var(--acc) 40%,transparent)}
.sy-counts span.same{color:var(--green);
  border-color:color-mix(in srgb,var(--green) 35%,transparent)}
.sy{display:flex;align-items:center;gap:11px;padding:11px 14px;
  border:1px solid var(--line);border-radius:11px;background:var(--panel)}
.sy .col{flex:1;min-width:0}
.sy .nm{font-size:13px;color:var(--tx);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.sy .sub{font-size:11px;color:var(--dim);margin-top:3px}
.sy .st{font-size:11px;padding:3px 9px;border-radius:99px;white-space:nowrap;
  border:1px solid var(--line);color:var(--mut)}
.sy .st.changed{color:var(--gold)}
.sy .st.new{color:var(--acc2)}
.sy .st.same{color:var(--green)}
.sy button{height:28px;padding:0 11px;border-radius:8px;cursor:pointer;
  border:1px solid var(--line);background:var(--panel2);color:var(--tx2);
  font-size:12px;white-space:nowrap;font-family:var(--sans)}
.sy button:hover{border-color:var(--acc);color:var(--acc)}
.sy button:disabled{opacity:.45;pointer-events:none}
.sy a{color:var(--mut);font-size:11.5px;text-decoration:none}
.sy a:hover{color:var(--acc)}
.bg{flex:0 0 auto;font-size:9.5px;padding:2px 6px;border-radius:99px;
  border:1px solid var(--line);color:var(--dim);white-space:nowrap;
  font-variant-numeric:tabular-nums}
.bg.s{color:var(--acc2);border-color:color-mix(in srgb,var(--acc) 40%,transparent)}
.load{display:block;padding:7px 9px;font-size:12px;color:var(--dim)}

/* ------------------------------------------------ полотно */
.main{grid-column:2;grid-row:2;overflow-y:auto;position:relative}
.wrap{max-width:var(--col);margin:0 auto;padding:0 30px}
.wrap.nomap .map{display:none}

article.doc{padding:46px 0 160px;color:var(--read);font-family:var(--body);
  font-size:var(--fs);line-height:var(--lh);
  letter-spacing:.0015em;hyphens:auto;-webkit-hyphens:auto}

/* ------------------------------------------------ шапка документа */
.head{margin-bottom:34px}
.kicker{font-family:var(--sans);font-size:11px;font-weight:660;
  letter-spacing:.13em;text-transform:uppercase;color:var(--acc2);
  margin-bottom:13px}
h1{font-family:var(--sans);font-size:clamp(28px,3.4vw,40px);line-height:1.14;
  letter-spacing:-.032em;font-weight:700;margin:0 0 16px;color:var(--tx)}
.meta-line{font-family:var(--sans);font-size:13px;color:var(--mut);
  font-style:normal;margin:0 0 18px}
.facts{display:flex;flex-wrap:wrap;gap:7px;padding-top:16px;
  border-top:1px solid var(--line);font-family:var(--sans)}
.fact{font-size:11.5px;color:var(--mut);border:1px solid var(--line);
  border-radius:99px;padding:4px 10px;display:inline-flex;gap:5px;
  align-items:center}
.fact a{color:var(--acc);text-decoration:none}
.fact a:hover{text-decoration:underline}

/* ------------------------------------------------ текст */
.lead{font-size:1.16em;line-height:1.62;color:var(--tx);margin:0 0 26px;
  font-weight:400}
p{margin:0 0 1.15em;text-align:left}
h2{font-family:var(--sans);font-size:1.42em;line-height:1.26;font-weight:680;
  letter-spacing:-.024em;color:var(--tx);margin:2.4em 0 .7em;
  display:flex;gap:.6em;align-items:baseline;scroll-margin-top:74px}
h2 .no{flex:0 0 auto;font-size:.72em;font-weight:700;color:var(--acc2);
  font-variant-numeric:tabular-nums;padding-top:.16em}
h2::after{content:'';position:absolute}
h3{font-family:var(--sans);font-size:1.14em;line-height:1.34;font-weight:660;
  letter-spacing:-.015em;color:var(--tx);margin:1.9em 0 .55em;
  scroll-margin-top:74px}
h2+p,h3+p,.lead+h2{margin-top:0}

/* опорная мысль */
.key{position:relative;margin:1.7em 0;padding:.15em 0 .15em 1.15em;
  border-left:3px solid var(--acc);font-size:1.09em;line-height:1.56;
  color:var(--tx);font-weight:500}
body.font-sans .key{font-weight:520}
.key em{font-style:italic}

blockquote{margin:1.6em 0;padding:.9em 1.15em;border-left:3px solid var(--gold);
  background:var(--panel2);border-radius:0 9px 9px 0;color:var(--tx2);
  font-size:.97em}
blockquote p:last-child{margin-bottom:0}

ul,ol{margin:0 0 1.3em;padding-left:1.5em}
li{margin-bottom:.5em}
li::marker{color:var(--acc2)}
ul.plain{list-style:none;padding-left:0}
ul.plain li{position:relative;padding-left:1.3em}
ul.plain li::before{content:'';position:absolute;left:.25em;top:.62em;
  width:5px;height:5px;border-radius:50%;background:var(--acc)}

pre{background:var(--panel2);border:1px solid var(--line);border-radius:10px;
  padding:13px 15px;overflow-x:auto;font-size:.78em;line-height:1.55;
  font-family:ui-monospace,Consolas,Menlo,monospace}

/* акценты внутри текста */
strong{font-weight:660;color:var(--tx)}
em{font-style:italic}
.term{font-weight:660;color:var(--tx)}
.num{font-variant-numeric:tabular-nums;font-weight:600;
  color:color-mix(in srgb,var(--tx) 82%,var(--acc) 18%)}
.qt{font-style:italic;color:var(--tx)}
a.lnk{color:var(--acc);text-decoration:none;
  border-bottom:1px solid color-mix(in srgb,var(--acc) 45%,transparent);
  word-break:break-word}
a.lnk:hover{border-bottom-color:var(--acc);background:var(--accsoft)}
a.ref{display:inline-block;vertical-align:super;font-family:var(--sans);
  font-size:.56em;line-height:1;font-weight:700;min-width:1.15em;
  text-align:center;padding:.28em .34em;margin:0 .12em;border-radius:4px;
  text-decoration:none;color:var(--acc2);background:var(--accsoft);
  border:1px solid color-mix(in srgb,var(--acc) 32%,transparent);
  transition:.14s}
a.ref:hover{background:var(--acc);color:#fff;border-color:var(--acc)}

/* источники */
.sources{margin-top:3.4em;padding-top:1.5em;border-top:1px solid var(--line2)}
.sources h2{margin-top:0;font-size:1.16em}
.sources ol{font-family:var(--sans);font-size:.79em;line-height:1.62;
  color:var(--tx2);padding-left:1.9em}
.sources li{margin-bottom:1.05em;scroll-margin-top:82px;
  transition:background .3s}
.sources li.hit{background:var(--accsoft);border-radius:7px;
  padding:.55em .7em;margin-left:-.7em}
.sources .u{display:block;margin-top:.2em}
.sources .chk{color:var(--dim)}

/* карта главы: плавает у правого края, всплывает при прокрутке.
   Отдельная колонка в сетке не нужна — карта нужна на секунду, чтобы
   перейти к нужному месту, и не должна отнимать ширину у текста. */
.map{position:fixed;right:18px;top:calc(var(--top) + 22px);width:214px;z-index:20;
  max-height:calc(100vh - var(--top) - 148px);overflow-y:auto;
  padding:13px 13px 13px 7px;border:1px solid var(--line);border-radius:13px;
  background:color-mix(in srgb,var(--panel) 93%,transparent);backdrop-filter:blur(12px);
  box-shadow:0 16px 40px rgba(0,0,0,.3);font-family:var(--sans);
  opacity:0;transform:translateX(12px);transition:opacity .22s,transform .22s;
  pointer-events:none}
.map.show{opacity:1;transform:none;pointer-events:auto}
body.theme-light .map,body.theme-sepia .map{box-shadow:0 16px 36px rgba(0,0,0,.15)}
.map-t{font-size:10px;font-weight:680;letter-spacing:.12em;text-transform:uppercase;
  color:var(--dim);margin-bottom:11px;padding-left:11px}
.map a{display:block;font-size:12px;line-height:1.4;color:var(--mut);
  text-decoration:none;padding:5px 0 5px 11px;border-left:2px solid var(--line)}
.map a:hover{color:var(--tx2);border-left-color:var(--line2)}
.map a.on{color:var(--acc2);border-left-color:var(--acc);font-weight:600}

/* прокрутка к началу и в конец: длинную главу мотать колесом неудобно.
   Место у правого края по центру: карта висит выше, окно задания ниже. */
.updown{position:fixed;right:20px;top:50%;transform:translateY(-50%);z-index:22;
  display:flex;flex-direction:column;gap:8px;opacity:0;transition:opacity .2s;
  pointer-events:none}
.updown.show{opacity:.55;pointer-events:auto}
.updown.show:hover{opacity:1}
.updown button{width:36px;height:36px;border-radius:50%;cursor:pointer;
  border:1px solid var(--line2);color:var(--tx2);font-size:15px;line-height:1;
  background:color-mix(in srgb,var(--panel) 94%,transparent);backdrop-filter:blur(10px);
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 10px 26px rgba(0,0,0,.3)}
.updown button:hover{border-color:var(--acc);color:var(--acc)}
.updown button.off{opacity:.35;pointer-events:none}

.empty{padding:80px 20px;text-align:center;color:var(--mut);font-family:var(--sans)}

/* ------------------------------------------------ цитаты и правки */
mark.q-note{background:color-mix(in srgb,var(--gold) 26%,transparent);
  color:inherit;border-radius:3px;padding:.06em 0;
  box-shadow:0 1px 0 color-mix(in srgb,var(--gold) 55%,transparent)}
mark.q-edit{background:color-mix(in srgb,var(--green) 20%,transparent);
  color:inherit;border-radius:3px;padding:.06em 0;
  box-shadow:0 1px 0 color-mix(in srgb,var(--green) 50%,transparent)}
mark.q-old{background:color-mix(in srgb,var(--gold) 11%,transparent);
  color:inherit;border-radius:3px;padding:.06em 0;
  box-shadow:0 1px 0 color-mix(in srgb,var(--gold) 26%,transparent)}
mark.q-note.hot,mark.q-edit.hot{outline:2px solid var(--acc);outline-offset:1px}

.qbox{font-family:var(--sans);font-size:.76em;line-height:1.55;
  margin:.5em 0 1.3em;border-radius:9px;border:1px solid var(--line);
  background:var(--panel2);overflow:hidden}
.qbox.note{border-left:3px solid var(--gold)}
.qbox.edit{border-left:3px solid var(--green)}
.qbox.old{border-left:3px solid color-mix(in srgb,var(--gold) 45%,transparent);
  opacity:.82}
.qbox.old .tag{color:color-mix(in srgb,var(--gold) 75%,var(--mut))}
.qbox.old:hover{opacity:1}
.qbox .qh{display:flex;align-items:center;gap:8px;padding:7px 11px;
  color:var(--mut);cursor:default}
.qbox .tag{font-weight:660;letter-spacing:.01em}
.qbox.note .tag{color:var(--gold)}
.qbox.edit .tag{color:var(--green)}
.qbox .when{color:var(--dim);font-size:.92em}
.qbox .acts{margin-left:auto;display:flex;gap:5px}
.qbox .acts button{font-size:.92em;color:var(--dim);padding:2px 6px;
  border-radius:6px;border:1px solid transparent}
.qbox .acts button:hover{color:var(--tx2);border-color:var(--line2);
  background:var(--panel)}
.qbox .body{padding:0 11px 10px}
.qbox.found,.qform.found{animation:qfound 1.4s ease-out}
mark.found{animation:mfound 1.4s ease-out}
@keyframes qfound{
  0%{box-shadow:0 0 0 3px var(--accsoft);border-color:var(--acc)}
  100%{box-shadow:0 0 0 0 transparent}}
@keyframes mfound{
  0%{outline:2px solid var(--acc);outline-offset:2px}
  100%{outline:2px solid transparent;outline-offset:2px}}
.qbox .cite{color:var(--tx2);font-style:italic;
  border-left:2px solid var(--line2);padding-left:9px;margin-bottom:7px}
.qbox .said{color:var(--tx);white-space:pre-wrap}
.qbox .was{color:var(--dim);margin-top:7px}
.qbox .why{color:var(--tx2);margin-top:5px}
summary.qh{list-style:none}
summary.qh::-webkit-details-marker{display:none}
summary.qh{cursor:pointer}
summary.qh:hover{background:var(--panel)}
details.qbox[open] summary.qh .tag::after{content:' ▾'}
details.qbox:not([open]) summary.qh .tag::after{content:' ▸'}

/* поле новой цитаты */
.qform{font-family:var(--sans);font-size:.78em;margin:.5em 0 1.3em;
  border:1px solid var(--acc);border-radius:9px;background:var(--panel2);
  box-shadow:0 0 0 3px var(--accsoft);overflow:hidden}
.qform .cite{margin:9px 11px 0;color:var(--tx2);font-style:italic;
  border-left:2px solid var(--gold);padding-left:9px}
.qform textarea{width:100%;min-height:74px;resize:vertical;border:0;outline:0;
  background:transparent;color:var(--tx);padding:9px 11px;font:inherit;
  line-height:1.55}
.qform .foot{display:flex;align-items:center;gap:9px;padding:7px 11px;
  border-top:1px solid var(--line);color:var(--dim);font-size:.92em}
.qform .foot .sp{margin-left:auto;display:flex;gap:6px}
.qform .foot button{padding:4px 10px;border-radius:7px;border:1px solid var(--line2);
  color:var(--tx2)}
.qform .foot button.ok{background:var(--acc);border-color:var(--acc);color:#fff;
  font-weight:600}
.qform .foot button:hover{filter:brightness(1.12)}

/* плашка при выделении */
.selbar{position:fixed;z-index:40;display:none;gap:4px;padding:4px;
  border-radius:10px;border:1px solid var(--line2);background:var(--panel);
  box-shadow:0 12px 34px rgba(0,0,0,.4);font-family:var(--sans);font-size:12.5px}
body.theme-light .selbar,body.theme-sepia .selbar{box-shadow:0 12px 30px rgba(0,0,0,.18)}
.selbar.show{display:flex}
.selbar button{padding:6px 11px;border-radius:7px;color:var(--tx2);
  white-space:nowrap}
.selbar button:hover{background:var(--accsoft);color:var(--acc)}

/* сообщение и панель истории */
.toast{position:fixed;right:18px;bottom:18px;z-index:60;padding:10px 14px;
  border-radius:10px;border:1px solid var(--line2);background:var(--panel);
  color:var(--tx2);font-family:var(--sans);font-size:13px;opacity:0;
  transform:translateY(10px);transition:.2s;pointer-events:none;
  box-shadow:0 12px 30px rgba(0,0,0,.3)}
.toast.show{opacity:1;transform:none}
.hist{position:fixed;top:var(--top);right:0;bottom:0;width:400px;z-index:30;
  background:var(--panel);border-left:1px solid var(--line);overflow-y:auto;
  transform:translateX(100%);transition:transform .22s;font-family:var(--sans);
  padding:16px 18px 60px}
.hist.open{transform:none}
.hist h3{margin:0 0 14px;font-size:15px;font-weight:660;color:var(--tx)}
.hist .ev{padding:9px 0;border-bottom:1px solid var(--line);font-size:12.5px;
  line-height:1.5}
.hist .ev .t{color:var(--dim);font-size:11.5px}
.hist .ev .k{font-weight:620;color:var(--tx2)}
.hist .ev .k.add{color:var(--gold)}
.hist .ev .k.edit{color:var(--green)}
.hist .ev .k.ver{color:var(--acc2)}
.hist .ev .d{color:var(--tx2);margin-top:2px}
.hist .close{position:absolute;top:12px;right:14px;color:var(--mut);
  font-size:16px;padding:4px 8px;border-radius:7px}
.hist .close:hover{background:var(--panel2);color:var(--tx)}

.frozen{margin:0 0 22px;padding:9px 13px;border-radius:9px;
  border:1px solid color-mix(in srgb,var(--gold) 40%,transparent);
  background:color-mix(in srgb,var(--gold) 10%,transparent);
  font-family:var(--sans);font-size:13px;color:var(--tx2)}

/* ------------------------------------------------ полоса версий */
.vers{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:12px;
  font-family:var(--sans);font-size:11.5px}
.vers a,.vers span.cur{padding:3px 9px;border-radius:99px;border:1px solid var(--line);
  color:var(--mut);text-decoration:none;font-variant-numeric:tabular-nums}
.vers a:hover{border-color:var(--acc);color:var(--acc)}
.vers span.cur{background:var(--accsoft);border-color:var(--acc);color:var(--acc);
  font-weight:640}
.vers button{padding:3px 10px;border-radius:99px;border:1px solid var(--line2);
  color:var(--tx2)}
.vers button:hover{border-color:var(--acc);color:var(--acc)}
.vers button.send{border-color:color-mix(in srgb,var(--acc) 55%,transparent);
  color:var(--acc2);font-weight:600}
.vers button.send:hover{background:var(--accsoft)}
.vers button[disabled]{opacity:.45;cursor:default}
.vers .sep{width:1px;height:14px;background:var(--line);margin:0 3px}

/* ------------------------------------------------ окно работы */
.job{position:fixed;right:20px;bottom:20px;z-index:50;width:372px;
  max-height:min(62vh,560px);display:none;flex-direction:column;
  border:1px solid var(--line2);border-radius:14px;background:var(--panel);
  box-shadow:0 20px 56px rgba(0,0,0,.42);font-family:var(--sans);
  overflow:hidden}
body.theme-light .job,body.theme-sepia .job{box-shadow:0 18px 44px rgba(0,0,0,.20)}
.job.show{display:flex}
.job .jh{display:flex;align-items:center;gap:9px;padding:12px 14px 10px;
  font-size:13px;font-weight:620;color:var(--tx)}
.job .jh .cnt{margin-left:auto;font-size:11.5px;font-weight:500;color:var(--mut);
  font-variant-numeric:tabular-nums}
.job .jh .x{color:var(--mut);font-size:14px;padding:2px 6px;border-radius:6px}
.job .jh .x:hover{background:var(--panel2);color:var(--tx)}

/* полоса из долей: та же логика и цвет, что у полосы чтения в шапке */
.jbar{display:flex;gap:3px;padding:0 14px 12px}
.jbar i{flex:1;height:3px;border-radius:2px;background:var(--line2);
  transition:background .3s}
.jbar i.done{background:linear-gradient(90deg,var(--acc),var(--acc2))}
.jbar i.run{background:linear-gradient(90deg,var(--acc),var(--acc2));
  animation:jpulse 1.15s ease-in-out infinite}
@keyframes jpulse{0%,100%{opacity:.42}50%{opacity:1}}

.jsteps{overflow-y:auto;padding:0 14px 12px;font-size:12.5px;line-height:1.5}
.jstep{display:flex;gap:8px;padding:5px 0;color:var(--tx2)}
.jstep .m{flex:0 0 auto;width:14px;text-align:center;color:var(--dim)}
.jstep.done .m{color:var(--green)}
.jstep.run .m{color:var(--acc2)}
.jstep.fail .m{color:var(--red,#e5534b)}
.jstep.wait{color:var(--dim)}
.jstep.run{color:var(--tx)}
.jstep .tm{margin-left:auto;flex:0 0 auto;color:var(--dim);font-size:11px;
  font-variant-numeric:tabular-nums}
.jfoot{padding:9px 14px 12px;border-top:1px solid var(--line);
  display:flex;align-items:center;gap:9px;font-size:11.5px;color:var(--mut)}
.jfoot button{margin-left:auto;padding:4px 11px;border-radius:7px;
  border:1px solid var(--line2);color:var(--tx2);font-size:12px}
.jfoot button:hover{border-color:var(--acc);color:var(--acc)}
.jfoot .warn{color:var(--gold)}

@media(max-width:900px){
  .job{right:10px;left:10px;width:auto;bottom:10px}
}

.orphan{margin-top:2.6em;padding-top:1.2em;border-top:1px solid var(--line2)}
.orphan h3{font-family:var(--sans);font-size:1em;color:var(--mut);margin:0 0 .8em}

/* Карта плавает поверх текста, поэтому прятать её нужно только там, где
   она реально накрыла бы колонку с текстом. */
@media(max-width:1100px){.map{display:none}}
@media(max-width:900px){
  .app{grid-template-columns:minmax(0,1fr)}
  .side{position:fixed;top:var(--top);bottom:0;left:0;width:290px;z-index:5;
    transform:translateX(-100%);transition:transform .2s;
    box-shadow:14px 0 40px rgba(0,0,0,.3)}
  .side.open{transform:none}
  .main{grid-column:1}
  .brand{width:auto}.brand span{display:none}
  .find{max-width:none}
  article.doc{padding:28px 0 120px;font-size:17.5px}
  .wrap{padding:0 18px}
  .updown{right:12px}
}
@media print{
  .top,.side,.map,.updown{display:none}
  body{overflow:visible;background:#fff;color:#000}
  .app{display:block;height:auto}
  .wrap{max-width:none;padding:0}
  article.doc{font-size:11.5pt;color:#000;padding:0}
  a.lnk{color:#000;border:0}
}
"""


def esc(s):
    return html.escape(s, quote=False)


def inline(s, sources=None, decorate=True, marks=None):
    """Экранирование плюс ссылки, сноски, акценты и подсветка цитат.

    marks — список {text, occ, cls, id}: фрагменты, которые надо обернуть
    подсветкой. Подсветку ставит сервер, а не браузер: выделение может
    задеть ссылку или сноску, и правка живого DOM порвала бы разметку.
    """
    sources = sources or {}
    plain = s

    s = esc(s)

    # markdown-выделения, если они где-то остались
    s = re.sub(r'\*\*([^*\n]+)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'(?<![*\w])\*([^*\n]+)\*(?![*\w])', r'<em>\1</em>', s)

    def link(m):
        raw = m.group(1)
        u = raw.rstrip('.,;)')
        tail = raw[len(u):]
        return f'<a class="lnk" href="{u}" target="_blank" rel="noopener">{u}</a>{tail}'

    s = URL_RE.sub(link, s)

    def ref(m):
        n = m.group(1)
        tip = html.escape(sources.get(n, 'источник ' + n), quote=True)
        return f'<a class="ref" href="#src-{n}" data-n="{n}" title="{tip}">{n}</a>'

    s = MARK_RE.sub(ref, s)

    if decorate:
        s = re.sub(r'«([^«»]{1,160})»', r'«<span class="qt">\1</span>»', s)
        s = FIGURE.sub(r'<span class="num">\1</span>', s)

    if marks:
        s = apply_marks(s, marks)
    return s


def apply_marks(html_text, marks):
    """Оборачивает процитированные куски в <mark>, не задевая теги.

    Работает по видимому тексту: собираем карту «позиция в тексте ->
    позиция в html», ищем цитату в тексте, вставляем теги по границам.
    Если фрагмент попал внутрь тега, подсветка не ставится — молча
    пропускаем, чтобы не сломать разметку.
    """
    # карта видимых символов
    vis, pos = [], []
    i, n = 0, len(html_text)
    while i < n:
        c = html_text[i]
        if c == '<':
            j = html_text.find('>', i)
            i = n if j < 0 else j + 1
            continue
        if c == '&':
            j = html_text.find(';', i)
            if 0 < j <= i + 10:
                ent = html_text[i:j + 1]
                ch = {'&amp;': '&', '&lt;': '<', '&gt;': '>',
                      '&quot;': '"', '&#x27;': "'"}.get(ent, '?')
                vis.append(ch)
                pos.append((i, j + 1))
                i = j + 1
                continue
        vis.append(c)
        pos.append((i, i + 1))
        i += 1

    text = ''.join(vis)
    spans = []
    for m in marks:
        needle = (m.get('text') or '').strip()
        if not needle:
            continue

        # видимый текст отличается от исходного: метка сноски [1] отрисована
        # как ссылка с цифрой без скобок. Поэтому пробуем варианты.
        cands = [needle]
        alt = MARK_RE.sub(r'\1', needle)
        if alt != needle:
            cands.append(alt)
        head = needle.split(' [')[0].strip()
        if len(head) >= 12 and head not in cands:
            cands.append(head)

        occ = m.get('occ', 0)
        start = -1
        for cand in cands:
            from_i, found = 0, -1
            for _ in range(occ + 1):
                found = text.find(cand, from_i)
                if found < 0:
                    break
                from_i = found + 1
            if found < 0:
                found = text.find(cand)
            if found >= 0:
                start = found
                needle = cand
                break
        if start < 0:
            continue
        end = start + len(needle) - 1
        if end >= len(pos):
            continue
        spans.append((pos[start][0], pos[end][1], m))

    if not spans:
        return html_text

    # вставляем с конца, чтобы смещения не поехали; вложенные пропускаем
    spans.sort(key=lambda x: x[0], reverse=True)
    out = html_text
    last_start = None
    for a, b, m in spans:
        if last_start is not None and b > last_start:
            continue                        # пересекается с уже вставленной
        cls = m.get('cls', 'q-note')
        mid = m.get('id', '')
        out = (out[:a] + f'<mark class="{cls}" data-id="{mid}">'
               + out[a:b] + '</mark>' + out[b:])
        last_start = a
    return out


def para(text, sources, marks=None):
    """Абзац. В определениях «Термин — это…» термин выделяется."""
    m = DEFINITION.match(text)
    if m and not marks:
        term = m.group(1)
        rest = text[len(term):]
        return (f'<p><span class="term">{inline(term, sources)}</span>'
                f'{inline(rest, sources)}</p>')
    return f'<p>{inline(text, sources, marks=marks)}</p>'


def when(iso):
    """2026-09-02T17:40:11 -> 2 сентября, 17:40"""
    MONTHS = ('января февраля марта апреля мая июня июля августа сентября '
              'октября ноября декабря').split()
    try:
        d, t = iso.split('T')
        y, mo, dd = (int(x) for x in d.split('-'))
        return f'{dd} {MONTHS[mo - 1]}, {t[:5]}'
    except Exception:
        return iso


def quote_box(note, state):
    """Врезка с замечанием Андрея под абзацем."""
    cite = esc(note['anchor'].get('text', ''))
    said = esc(note.get('comment', ''))
    warn = ('<div class="was">абзац после этого менялся — проверьте, '
            'к тому ли месту относится</div>' if state == 'moved' else '')
    status = {'open': '', 'done': ' · сделано', 'rejected': ' · не согласна',
              'wontfix': ' · не буду делать'}.get(note.get('status'), '')
    return (f'<div class="qbox note" id="{note["id"]}">'
            f'<div class="qh"><span class="tag">Замечание</span>'
            f'<span class="when">{when(note.get("created", ""))}{status}</span>'
            f'<span class="acts">'
            f'<button data-act="edit-note" data-id="{note["id"]}">правка</button>'
            f'<button data-act="del-note" data-id="{note["id"]}">удалить</button>'
            f'</span></div>'
            f'<div class="body"><div class="cite">{cite}</div>'
            f'<div class="said">{said}</div>{warn}</div></div>')


def inherit_box(note, state):
    """Открытое замечание из прежней версии: свёрнуто, приглушённо."""
    cite = esc(note['anchor'].get('text', ''))
    said = esc(note.get('comment', ''))
    v = note.get('from_version', '?')
    warn = ('<div class="was">абзац здесь другой — проверьте, к тому ли '
            'месту относится</div>' if state == 'moved' else '')
    return (f'<details class="qbox old" id="{note["id"]}">'
            f'<summary class="qh"><span class="tag">Замечание из v{v}</span>'
            f'<span class="when">{when(note.get("created", ""))} · '
            f'ещё не отработано</span></summary>'
            f'<div class="body"><div class="cite">{cite}</div>'
            f'<div class="said">{said}</div>{warn}</div></details>')


def edit_box(edit, state):
    """Свёрнутая плашка правки Ники: раскрывается в замечание и причину."""
    ref = edit.get('from_note')
    tag = 'Правка'
    if ref:
        num = str(ref).split('-')[-1].lstrip('n')
        src = str(ref).split('-')[0] if '-' in str(ref) else ''
        tag = f'Правка по замечанию {num}' + (f' из {src}' if src else '')
    parts = []
    if edit.get('note_text'):
        parts.append(f'<div class="cite">{esc(edit["note_text"])}</div>')
    if edit.get('was'):
        parts.append(f'<div class="was">было: {esc(edit["was"])}</div>')
    if edit.get('why'):
        parts.append(f'<div class="why">{esc(edit["why"])}</div>')
    if state == 'moved':
        parts.append('<div class="was">абзац после правки менялся</div>')
    body = ''.join(parts) or '<div class="why">без пояснения</div>'
    return (f'<details class="qbox edit" id="{edit["id"]}">'
            f'<summary class="qh"><span class="tag">{tag}</span>'
            f'<span class="when">{when(edit.get("created", ""))}</span>'
            f'</summary><div class="body">{body}</div></details>')


def orphan_block(orphans):
    """Замечания, чей текст в этой версии не нашёлся: показываем отдельно."""
    if not orphans:
        return ''
    rows = []
    for entry in orphans:
        kind, item = entry[0], entry[1]
        if kind == 'quote':
            rows.append(quote_box(item, 'ok'))
        elif kind == 'inherit':
            rows.append(inherit_box(item, 'ok'))
        else:
            rows.append(edit_box(item, 'ok'))
    return ('<section class="orphan"><h3>Замечания, текст которых '
            'в этой версии не найден</h3>' + ''.join(rows) + '</section>')


def source_item(b, sources):
    """Пункт блока источников: подпись, URL на своей строке, дата."""
    out = []
    for part in b['parts']:
        cls = ''
        if part.startswith('Источник'):
            cls = ' class="u"'
        elif part.startswith('Проверено'):
            cls = ' class="u chk"'
        if cls:
            out.append(f'<span{cls}>{inline(part, {}, decorate=False)}</span>')
        else:
            out.append(inline(part, {}, decorate=False))
    return f'<li id="src-{b["num"]}" value="{b["num"]}">' + ''.join(out) + '</li>'


def anchor(text, used):
    a = re.sub(r'[^\w\s-]', '', text.lower())
    a = re.sub(r'\s+', '-', a.strip())[:50] or 'h'
    n = 1
    base = a
    while a in used:
        n += 1
        a = f'{base}-{n}'
    used.add(a)
    return a


def render(blocks, sources, ann=None):
    """Блоки -> HTML документа. Возвращает (html, карта заголовков).

    ann — результат notes.annotations(): подсветки и врезки замечаний.
    Номер блока в привязке — это индекс в списке blocks, поэтому он
    остаётся верным независимо от того, как блок отрисован.
    """
    ann = ann or {}
    marks_map = ann.get('marks') or {}
    boxes_map = ann.get('boxes') or {}

    body, toc, used = [], [], set()
    in_src = False
    src_items = []

    def flush_src():
        if src_items:
            body.append('<ol>' + ''.join(src_items) + '</ol>')
            src_items.clear()

    for bi, b in enumerate(blocks):
        t = b['t']
        marks = marks_map.get(bi)

        if t == 'source':
            src_items.append(source_item(b, sources))
            continue
        if src_items and t != 'source':
            flush_src()

        if t == 'h1':
            continue                      # заголовок печатается в шапке
        if t == 'meta':
            continue                      # строка «Черновик…» — тоже в шапке

        if t == 'srcheader':
            if not in_src:
                body.append('<section class="sources">')
                in_src = True
            a = anchor(b['text'], used)
            toc.append((a, b['text'], 2))
            body.append(f'<h2 id="{a}">{esc(b["text"])}</h2>')
        elif t == 'h2':
            a = anchor(b['text'], used)
            label = b['text']
            toc.append((a, label, 2))
            no = f'<span class="no">{b["num"]}.</span>' if b.get('num') else ''
            body.append(f'<h2 id="{a}" data-b="{bi}">{no}'
                        f'<span>{inline(label, sources, marks=marks)}</span></h2>')
        elif t == 'h3':
            a = anchor(b['text'], used)
            toc.append((a, b['text'], 3))
            body.append(f'<h3 id="{a}" data-b="{bi}">'
                        f'{inline(b["text"], sources, marks=marks)}</h3>')
        elif t == 'lead':
            body.append(f'<p class="lead" data-b="{bi}">'
                        f'{inline(b["text"], sources, marks=marks)}</p>')
        elif t == 'key':
            body.append(f'<p class="key" data-b="{bi}">'
                        f'{inline(b["text"], sources, marks=marks)}</p>')
        elif t == 'p':
            html_p = para(b['text'], sources, marks=marks)
            body.append(html_p.replace('<p>', f'<p data-b="{bi}">', 1))
        elif t == 'quote':
            body.append(f'<blockquote data-b="{bi}"><p>'
                        f'{inline(b["text"], sources, marks=marks)}</p></blockquote>')
        elif t == 'ul':
            items = ''.join(f'<li>{inline(x, sources)}</li>' for x in b['items'])
            body.append(f'<ul class="plain" data-b="{bi}">{items}</ul>')
        elif t == 'ol':
            items = ''.join(f'<li value="{n}">{inline(x, sources)}</li>'
                            for n, x in b['items'])
            body.append(f'<ol data-b="{bi}">{items}</ol>')
        elif t == 'code':
            body.append(f'<pre data-b="{bi}"><code>{esc(b["text"])}</code></pre>')

        # врезки замечаний идут сразу после своего блока
        for entry in boxes_map.get(bi, []):
            kind, item, state = entry
            if kind == 'quote':
                body.append(quote_box(item, state))
            elif kind == 'inherit':
                body.append(inherit_box(item, state))
            else:
                body.append(edit_box(item, state))

    flush_src()
    if in_src:
        body.append('</section>')
    body.append(orphan_block(ann.get('orphans') or []))
    return '\n'.join(body), toc
