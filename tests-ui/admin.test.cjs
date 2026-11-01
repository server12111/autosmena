const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {JSDOM} = require('jsdom');

async function setup() {
  const dom = new JSDOM(readFileSync('app/static/index.html','utf8'),{url:'http://localhost',runScripts:'outside-only'});
  const win = dom.window;
  const calls=[];
  win.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  win.HTMLDialogElement.prototype.close=function(){this.open=false;};
  win.fetch=async(path,options)=>{
    calls.push({path,...options});
    const data=path.endsWith('/session')?{csrf:'test-csrf',demo:true,supplier_ready:true}:
      path.endsWith('/overview')?{revenue:0,deposits:0,margin:0,users:0,review:0}:{ok:true};
    return {ok:true,json:async()=>data};
  };
  win.eval(readFileSync('app/static/app.js','utf8'));
  await new Promise(resolve=>setImmediate(resolve));
  return {dom,win,calls};
}

test('webhook settings show callback URLs and save to database API without new env fields',async()=>{
  const {dom,win,calls}=await setup();
  try {
    await win.document.getElementById('webhooks-settings').onclick();
    const form=win.document.getElementById('edit-form');
    form.elements.baseUrl.value='https://shop.example.com';
    form.elements.baseUrl.oninput();
    assert.ok(win.document.getElementById('dialog-fields').textContent.includes('https://shop.example.com/webhooks/xrocket'));
    form.elements.telegramEnabled.checked=true;
    await form.onsubmit({preventDefault(){},target:form});
    const request=calls.find(c=>c.path==='/api/webhooks' && c.method==='PUT');
    assert.deepEqual(JSON.parse(request.body),{base_url:'https://shop.example.com',telegram_enabled:true});
    assert.equal(request.headers['X-CSRF-Token'],'test-csrf');
  } finally {dom.window.close();}
});

test('price form submits enabled state and exact fixed price',async()=>{
  const {dom,win,calls}=await setup();
  try {
    win.editProduct({id:1,name:'Test',markup_bps:3000,fixed_price:null,enabled:true});
    const form=win.document.getElementById('edit-form');
    form.elements.priceMode.value='fixed';
    form.elements.fixed.value='2.35';
    await form.onsubmit({preventDefault(){},target:form});
    const request=calls.find(c=>c.method==='PATCH');
    assert.equal(request.path,'/api/products/1');
    assert.deepEqual(JSON.parse(request.body),{name:'Test',markup_percent:30,fixed_price:'2.35',enabled:true});
    assert.equal(request.headers['X-CSRF-Token'],'test-csrf');
  } finally {dom.window.close();}
});

test('review form sends explicit refund confirmation',async()=>{
  const {dom,win,calls}=await setup();
  try {
    win.reviewOrder({id:'a'.repeat(32)});
    const form=win.document.getElementById('edit-form');
    form.elements.reason.value='Supplier confirmed no charge';
    form.elements.confirmed.checked=true;
    await form.onsubmit({preventDefault(){},target:form});
    const request=calls.find(c=>c.path.includes('/review'));
    assert.equal(JSON.parse(request.body).refund_confirmed,true);
    assert.equal(JSON.parse(request.body).action,'refund');
  } finally {dom.window.close();}
});

test('supplier labels are rendered as text',async()=>{
  const {dom,win}=await setup();
  try {
    win.editProduct({id:1,name:'<img src=x onerror=alert(1)>',markup_bps:0,fixed_price:null,enabled:false});
    assert.equal(win.document.getElementById('dialog-title').textContent,'<img src=x onerror=alert(1)>');
    assert.equal(win.document.querySelector('#edit-dialog img'),null);
  } finally {dom.window.close();}
});

test('fixed price mode requires a price instead of silently applying markup',async()=>{
  const {dom,win}=await setup();
  try {
    win.editProduct({id:1,name:'Test',markup_bps:3000,fixed_price:null,enabled:true});
    const form=win.document.getElementById('edit-form');
    form.elements.priceMode.value='fixed';
    form.elements.priceMode.onchange();
    assert.equal(form.elements.fixed.required,true);
    assert.equal(form.elements.markup.required,false);
    assert.equal(form.elements.fixed.validity.valueMissing,true);
  } finally {dom.window.close();}
});

test('invoice recovery submits the selected external invoice id',async()=>{
  const {dom,win,calls}=await setup();
  try {
    win.recoverPayment({id:'a'.repeat(32),reference:'local-ref',external_id:null});
    const form=win.document.getElementById('edit-form');
    form.elements.externalId.value='42';
    await form.onsubmit({preventDefault(){},target:form});
    const request=calls.find(c=>c.path.endsWith('/recover'));
    assert.deepEqual(JSON.parse(request.body),{external_id:'42'});
    assert.equal(request.method,'POST');
  } finally {dom.window.close();}
});

test('supplier currency can be configured through the admin without env editing',async()=>{
  const {dom,win,calls}=await setup();
  try {
    await win.document.getElementById('supplier-currency').onclick();
    const form=win.document.getElementById('edit-form');
    form.elements.currency.value='USDT';form.elements.currency.onchange();
    assert.equal(form.elements.rate.value,'1');
    await form.onsubmit({preventDefault(){},target:form});
    const request=calls.find(c=>c.method==='PUT');
    assert.deepEqual(JSON.parse(request.body),{currency:'USDT',usd_rate:'1'});
  } finally {dom.window.close();}
});
