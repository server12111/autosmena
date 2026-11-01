const $ = id => document.getElementById(id);
let csrf = '', view = 'overview', offset = 0, total = 0, rows = [], currentEdit = null;
const titles = {overview:'Обзор',products:'Каталог и цены',orders:'Заказы',payments:'Пополнения',users:'Клиенты',ledger:'Движение средств',audit:'Журнал действий'};
const money = value => '$' + (Number(value) / 1000000).toFixed(2);
const date = value => value ? new Date(value * 1000).toLocaleString('ru-RU') : '—';
const statusNames = {pending:'Ожидает оплаты',paid:'Оплачен',expired:'Истёк',creating:'Создание',review:'Нужна проверка',reserved:'Принят',allocating:'Выдача номера',waiting:'Ожидаем SMS',completed:'Завершён',refunded:'Возвращено',active:'Аренда активна'};
function el(tag, text, cls) { const node = document.createElement(tag); if(text !== undefined) node.textContent = text; if(cls) node.className = cls; return node; }
async function api(path, method='GET', body) {
  const response = await fetch('/api/' + path, {method, credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, ...(body ? {body:JSON.stringify(body)} : {})});
  const data = await response.json();
  if(!response.ok) { if(response.status === 401) showLogin(); throw new Error(typeof data.detail === 'string' ? data.detail : 'Проверьте введённые данные'); }
  return data;
}
function showLogin() { $('workspace').hidden = true; $('login-screen').hidden = false; csrf = ''; }
function toast(text) { $('toast').textContent = text; $('toast').hidden = false; setTimeout(() => $('toast').hidden = true, 3000); }
async function enter() { const session = await api('session'); csrf = session.csrf; $('mode').textContent = session.demo ? 'ДЕМО' : 'LIVE'; $('login-screen').hidden = true; $('workspace').hidden = false; $('notice').hidden = !session.demo && session.supplier_ready; $('notice').textContent = session.demo ? 'Деморежим: номера, SMS и пополнения тестовые. Реальные платежи отключены.' : 'Покупки недоступны до завершения настройки поставщика api.cc.'; await load(); }
$('login-form').addEventListener('submit',async event => { event.preventDefault(); const button = event.submitter; button.disabled = true; try { const result = await api('login','POST',{password:event.target.password.value}); csrf = result.csrf; event.target.reset(); await enter(); } catch(error) { $('login-error').textContent = error.message; } finally { button.disabled = false; } });
async function changeView(name) { view=name;offset=0;$('search').value='';await load(); }
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click',() => changeView(button.dataset.view)));
document.querySelectorAll('[data-shortcut]').forEach(button => button.addEventListener('click',() => changeView(button.dataset.shortcut)));
$('go-products').onclick = () => changeView('products');
$('refresh').onclick = load;
$('logout').onclick = async () => { try { await api('logout','POST',{}); showLogin(); } catch(error) { toast(error.message); } };
$('prev').onclick = () => {offset=Math.max(0,offset-50);load();};
$('next').onclick = () => {offset+=50;load();};
let searchTimeout;
$('search').oninput = () => { clearTimeout(searchTimeout);searchTimeout=setTimeout(() => {offset=0;load();},300); };
function cell(value, cls) { return el('td',value,cls); }
function pill(value) { const td=el('td'); td.append(el('span',statusNames[value] || value,'pill '+value)); return td; }
function action(label, fn) { const button=el('button',label,'row-action'); button.type='button';button.onclick=async () => {button.disabled=true;try {await fn();}catch(error){toast(error.message);}finally{button.disabled=false;}}; return button; }
const columns = {
  products:['Товар / страна','Себестоимость','Цена продажи','Наценка','Статус',''],
  orders:['Заказ','Клиент','Номер','Цена','Статус','Создан',''],
  payments:['Счёт','Клиент','Способ','На баланс','Статус','Создан',''],
  users:['Telegram ID','Имя','Баланс','Статус','С нами',''],
  ledger:['Операция','Клиент','Изменение','Основание','Комментарий','Дата'],
  audit:['ID','Действие','Подробности','Дата']
};
function renderRow(row) {
  const tr=el('tr'), actions=el('td');
  if(view==='products') {
    const name=cell(row.name,'cell-title');name.append(el('span',row.country+' · '+({new:'первичный',restart:'повторный',renew:'продление'}[row.kind]||row.kind),'cell-sub'));
    tr.append(name,cell(money(row.cost)),cell(money(row.price),'cell-title'),cell(row.fixed_price ? 'Фикс. цена' : row.markup_bps/100+'%'),pill(row.enabled?'active':'blocked'));
    actions.append(action('Изменить',()=>editProduct(row)));tr.append(actions);
  } else if(view==='orders') {
    tr.append(cell(row.id.slice(0,8)),cell(row.user_id),cell(row.phone||'—'),cell(money(row.price)),pill(row.status),cell(date(row.created_at)));
    if(row.status==='review') actions.append(action('Разобрать',()=>reviewOrder(row)));tr.append(actions);
  } else if(view==='payments') {
    tr.append(cell(row.id.slice(0,8)),cell(row.user_id),cell(row.provider),cell(money(row.amount)),pill(row.status),cell(date(row.created_at)));
    if(row.provider==='demo' && row.status==='pending') actions.append(action('Зачислить демо',async()=>{await api('demo/payments/'+row.id+'/pay','POST',{});await load();toast('Демоплатёж зачислен');}));
    if(['creating','review'].includes(row.status) && ['cryptopay','xrocket'].includes(row.provider)) actions.append(action('Сверить счёт',()=>recoverPayment(row)));
    tr.append(actions);
  } else if(view==='users') {
    tr.append(cell(row.id),cell(row.username?'@'+row.username:'—'),cell(money(row.balance),'cell-title'),pill(row.blocked?'blocked':'active'),cell(date(row.created_at)));
    actions.append(action('Баланс',()=>editBalance(row)),action(row.blocked?'Разблокировать':'Блокировать',async()=>{await api('users/'+row.id,'PATCH',{blocked:!row.blocked});await load();}));tr.append(actions);
  } else if(view==='ledger') tr.append(cell(row.id),cell(row.user_id),cell((row.delta>0?'+':'')+money(row.delta),'cell-title'),cell(row.event),cell(row.note||'—'),cell(date(row.created_at)));
  else tr.append(cell(row.id),cell(row.action),cell(row.detail),cell(date(row.created_at)));
  for(const td of tr.children) if(td.textContent.length>50) td.title=td.textContent;
  return tr;
}
let loadId=0;
async function load() {
  const current=++loadId; $('error').hidden=true; $('page-title').textContent=titles[view];document.querySelectorAll('[data-view]').forEach(button=>button.classList.toggle('active',button.dataset.view===view));
  $('dashboard').hidden=view!=='overview';$('table-section').hidden=view==='overview';
  $('import-catalog').hidden=view!=='products';
  $('supplier-currency').hidden=view!=='products';
  try {
    if(view==='overview') {
      const data=await api('overview');if(current!==loadId)return;
      $('stats').replaceChildren();
      for(const [name,value,note,cls] of [['Выручка',money(data.revenue),'Завершённые заказы','lime'],['Пополнения',money(data.deposits),'Подтверждённые платежи',''],['Маржа',money(data.margin),'До комиссий и расходов',''],['Клиенты',String(data.users),data.review+' заказов требуют проверки','']]) {
        const card=el('article',undefined,'stat '+cls);card.append(el('span',name,'eyebrow'),el('strong',value,'value'),el('small',note));$('stats').append(card);
      }
    } else {
      const data=await api('table/'+view+'?offset='+offset+'&q='+encodeURIComponent($('search').value));if(current!==loadId)return;
      rows=data.rows;total=data.total;$('total').textContent=total+' записей';
      const heading=el('tr');columns[view].forEach(text=>heading.append(el('th',text)));$('thead').replaceChildren(heading);$('tbody').replaceChildren(...rows.map(renderRow));$('empty').hidden=rows.length>0;$('prev').disabled=offset===0;$('next').disabled=offset+50>=total;$('page-number').textContent=Math.floor(offset/50)+1+' / '+Math.max(1,Math.ceil(total/50));
    }
  } catch(error) { $('error').textContent=error.message;$('error').hidden=false; }
}
function field(labelText,name,type,value) { const label=el('label',labelText),input=el('input');input.name=name;input.type=type;input.value=value??'';if(type==='checkbox'){input.value='on';input.checked=Boolean(value);label.className='checkbox';}label.append(input);return label; }
function openDialog(title,fields,edit) {currentEdit=edit;$('dialog-title').textContent=title;$('dialog-fields').replaceChildren(...fields);$('dialog-error').textContent='';$('edit-dialog').showModal();}
function editProduct(row) {
  const mode=el('select');mode.name='priceMode';for(const [value,label] of [['markup','Процентная наценка'],['fixed','Фиксированная цена']]) {const option=el('option',label);option.value=value;mode.append(option);}mode.value=row.fixed_price?'fixed':'markup';
  const modeLabel=el('label','Способ расчёта');modeLabel.append(mode);
  const markup=field('Наценка, %','markup','number',row.markup_bps/100),fixed=field('Цена, USD','fixed','number',row.fixed_price?(row.fixed_price/1000000).toFixed(2):'');
  markup.lastChild.min=0;markup.lastChild.max=10000;fixed.lastChild.min='.01';fixed.lastChild.step='.01';
  const toggle=()=>{markup.hidden=mode.value!=='markup';fixed.hidden=mode.value!=='fixed';markup.lastChild.required=mode.value==='markup';fixed.lastChild.required=mode.value==='fixed';};mode.onchange=toggle;toggle();
  const title=field('Название в боте','name','text',row.name);title.lastChild.required=true;title.lastChild.maxLength=128;
  openDialog(row.name,[title,modeLabel,markup,fixed,field('Товар доступен в боте','enabled','checkbox',row.enabled)],{type:'product',row});
}
function editBalance(row) {
  const kind=el('select');kind.name='kind';for(const [value,label] of [['add','Зачислить'],['subtract','Списать']]){const option=el('option',label);option.value=value;kind.append(option);}const label=el('label','Операция');label.append(kind);
  const amount=field('Сумма, USD','amount','number','');amount.lastChild.step='.01';amount.lastChild.min='.01';amount.lastChild.required=true;
  const reason=field('Причина (будет сохранена в журнале)','reason','text','');reason.lastChild.required=true;reason.lastChild.minLength=3;reason.lastChild.maxLength=256;
  openDialog('Баланс клиента '+row.id,[label,amount,reason],{type:'balance',row,requestId:crypto.randomUUID()});
}
function reviewOrder(row) {
  const mode=el('select');mode.name='reviewAction';
  for(const [value,label] of [['refund','Вернуть баланс клиенту'],['attach','Привязать заказ поставщика']]){const option=el('option',label);option.value=value;mode.append(option);}
  if(row.parent_id){const option=el('option','Продление подтверждено поставщиком');option.value='renewed';mode.append(option);}
  const label=el('label','Результат сверки');label.append(mode);
  const reason=field('Основание сверки (не менее 10 символов)','reason','text','');reason.lastChild.required=true;reason.lastChild.minLength=10;reason.lastChild.maxLength=256;
  const warning=el('p','Возврат разрешён только после проверки: поставщик не списал деньги или подтвердил возврат. Если номер был куплен, привяжите его заказ.');
  const confirmed=field('Сверка с поставщиком выполнена: выбранный результат подтверждён','confirmed','checkbox',false);
  const supplierOrder=field('Номер заказа api.cc (ordernum)','supplierOrder','text','');
  const toggle=()=>{confirmed.hidden=mode.value==='attach';supplierOrder.hidden=mode.value!=='attach';};mode.onchange=toggle;toggle();
  openDialog('Сверка заказа '+row.id.slice(0,8),[warning,label,supplierOrder,reason,confirmed],{type:'review',row});
}
function recoverPayment(row) {
  const info=el('p','Укажите ID счёта из кабинета провайдера. Зачисление произойдёт только после проверки оплаты, суммы, валюты и payload.');
  const reference=el('p','Payload для сопоставления: '+row.reference);
  const id=field('ID счёта провайдера','externalId','text',row.external_id||'');id.lastChild.required=true;id.lastChild.pattern='[0-9]{1,20}';
  openDialog('Сверка пополнения',[info,reference,id],{type:'paymentRecovery',row});
}
$('import-catalog').onclick=async()=>{
  const category=field('ID категории api.cc','category','number','2');category.lastChild.min=1;category.lastChild.required=true;
  const label=field('Название страны / категории в боте','label','text','');label.lastChild.required=true;label.lastChild.maxLength=64;
  const info=el('p','Импорт обновляет себестоимость. Новые товары отключены: проверьте цену и включите нужные.');
  const categories=action('Показать категории api.cc',async()=>{const data=await api('supplier/categories');info.textContent=JSON.stringify(data,null,2);info.style.whiteSpace='pre-wrap';});
  openDialog('Импорт каталога',[info,categories,category,label],{type:'import'});
};
$('supplier-currency').onclick=async()=>{
  try {
    const current=await api('supplier/pricing');
    const select=el('select');select.name='currency';
    for(const [value,label] of [['USD','USD — доллар'],['USDT','USDT — учёт 1:1 к USD'],['CNY','CNY — юань'],['OTHER','Другая валюта']]){const option=el('option',label);option.value=value;select.append(option);}
    select.value=['USD','USDT','CNY'].includes(current.currency)?current.currency:'OTHER';
    const label=el('label','Валюта цен в кабинете api.cc');label.append(select);
    const rate=field('Сколько USD стоит одна единица валюты поставщика','rate','number',current.usd_rate||'');rate.lastChild.min='.0000000001';rate.lastChild.step='any';rate.lastChild.required=true;
    const toggle=()=>{const fixed=['USD','USDT'].includes(select.value);rate.lastChild.readOnly=fixed;if(fixed)rate.lastChild.value='1';};select.onchange=toggle;toggle();
    openDialog('Валюта поставщика',[el('p','Сверьте валюту с кабинетом api.cc. При изменении курса товары отключатся до обновления каталога и проверки цен.'),label,rate],{type:'supplierCurrency'});
  } catch(error){toast(error.message);}
};
$('close-dialog').onclick=()=>$('edit-dialog').close();
$('edit-form').onsubmit=async event=>{event.preventDefault();const button=$('save-edit');button.disabled=true;const data=new FormData(event.target);try {
  if(currentEdit.type==='product') await api('products/'+currentEdit.row.id,'PATCH',{name:data.get('name'),markup_percent:Number(data.get('markup')),fixed_price:data.get('priceMode')==='fixed'?data.get('fixed'):null,enabled:data.get('enabled')==='on'});
  else if(currentEdit.type==='balance') await api('users/'+currentEdit.row.id+'/balance','POST',{amount:data.get('amount'),subtract:data.get('kind')==='subtract',reason:data.get('reason'),request_id:currentEdit.requestId});
  else if(currentEdit.type==='review') await api('orders/'+currentEdit.row.id+'/review','POST',{action:data.get('reviewAction'),reason:data.get('reason'),supplier_order:data.get('supplierOrder'),refund_confirmed:data.get('confirmed')==='on',renewal_confirmed:data.get('confirmed')==='on'});
  else if(currentEdit.type==='paymentRecovery') await api('payments/'+currentEdit.row.id+'/recover','POST',{external_id:data.get('externalId')});
  else if(currentEdit.type==='supplierCurrency') await api('supplier/pricing','PUT',{currency:data.get('currency'),usd_rate:data.get('rate')});
  else await api('supplier/sync','POST',{category:Number(data.get('category')),label:data.get('label')});
  $('edit-dialog').close();await load();toast('Изменения сохранены');
}catch(error){$('dialog-error').textContent=error.message;}finally{button.disabled=false;}};
enter().catch(()=>showLogin());
