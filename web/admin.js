'use strict';
const base = new URL(document.currentScript.src).pathname.replace(/\/admin\.js$/, '');
const $ = id => document.getElementById(id);
let session = '', page = 'products', rows = [], cursor = null, hasNext = false, busy = false;
let toastTimer;
function message(value, error = false) {
  $('message').textContent = value; $('message').classList.toggle('error', error); $('message').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('message').hidden = true, error ? 14000 : 5000);
}
function signedOut() {
  session = ''; $('password').value = ''; $('content').replaceChildren(); rows = [];
  $('dashboard').hidden = true; $('login').hidden = false;
}
async function api(action, values = {}) {
  const response = await fetch(base + '/api', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...values, action, session}), cache: 'no-store'});
  const data = await response.json();
  if (!response.ok) { if (response.status === 403 && action !== 'login') signedOut(); throw new Error(data.error || 'Anfrage fehlgeschlagen.'); }
  return data;
}
function el(tag, value, cls) { const n = document.createElement(tag); if(value !== undefined)n.textContent = value; if(cls)n.className = cls; return n; }
function input(label, value, type = 'text') {
  const wrap = el('label', label); const field = el('input'); field.type = type; field.value = value ?? ''; wrap.append(field); return {wrap, field};
}
function saveButton(label, fn) {
  const button = el('button', label, 'primary'); button.type = 'button';
  button.addEventListener('click', async () => {
    button.disabled = true;
    try { await fn(); message('Änderung gespeichert.'); }
    catch(error){ message(error.message, true); }
    finally { button.disabled = false; }
  }); return button;
}
function card(title, badge) {
  const node = el('article', undefined, 'card'), header = el('div', undefined, 'card-header');
  header.append(el('h2', title)); if(badge)header.append(el('span', badge, 'badge')); node.append(header); return node;
}
const statusNames = {ACTIVE:'Aktiv', DRAFT:'Entwurf', ARCHIVED:'Archiviert'};
function renderProducts(product) {
  const node = card(product.title, statusNames[product.status] || product.status);
  const fields = el('div', undefined, 'fields');
  const title = input('Produkttitel', product.title), vendor = input('Hersteller / Marke', product.vendor), kind = input('Produkttyp', product.productType);
  title.field.maxLength = vendor.field.maxLength = kind.field.maxLength = 255;
  const statusWrap = el('label', 'Status'), status = el('select');
  Object.entries(statusNames).forEach(([value,name]) => {const option = el('option', name); option.value = value; status.append(option);}); status.value = product.status; statusWrap.append(status);
  fields.append(title.wrap, statusWrap, vendor.wrap, kind.wrap); node.append(fields);
  const actions = el('div',undefined,'form-actions'); actions.append(saveButton('Produkt speichern', async () => {
    await api('product.save', {id:product.id,title:title.field.value,status:status.value,vendor:vendor.field.value,productType:kind.field.value});
    node.querySelector('h2').textContent = title.field.value; node.querySelector('.badge').textContent = statusNames[status.value];
  })); node.append(actions);
  for (const variant of product.variants.nodes) {
    const row = el('div',undefined,'variant'); row.append(el('h3',variant.title === 'Default Title' ? 'Preis' : variant.title));
    const form = el('div',undefined,'inline-form'), price = input('Preis in Shop-Währung',variant.price,'number'); price.field.min = '0'; price.field.step = '.01';
    form.append(price.wrap,saveButton('Preis speichern',async()=>{
      if(!price.field.checkValidity() || !price.field.value)throw new Error('Bitte einen gültigen Preis eingeben.');
      await api('variant.price',{productId:product.id,id:variant.id,price:price.field.value});
    })); row.append(form); node.append(row);
  }
  if(product.variants.pageInfo.hasNextPage)node.append(el('p','Weitere Varianten vorhanden. Hier werden die ersten 100 angezeigt.','muted'));
  return node;
}
function renderInventory(product) {
  const node = card(product.title);
  for (const variant of product.variants.nodes) {
    const row = el('div',undefined,'variant'); row.append(el('h3',variant.title === 'Default Title' ? 'Standardvariante' : variant.title));
    if(!variant.inventoryItem.tracked)row.append(el('p','Bestandsverfolgung ist für diese Variante deaktiviert.','muted'));
    else if(!variant.inventoryItem.inventoryLevels.nodes.length)row.append(el('p','Kein Lagerstandort zugeordnet.','muted'));
    else for(const level of variant.inventoryItem.inventoryLevels.nodes) {
      let before = level.quantities.find(q=>q.name==='available')?.quantity;
      if(!Number.isInteger(before))continue;
      const form = el('div',undefined,'inventory-row'), count = input('Verfügbarer Bestand',before,'number'); count.field.min='0'; count.field.max='1000000'; count.field.step='1';
      form.append(el('span',level.location.name,'location'),count.wrap,saveButton('Bestand speichern',async()=>{
        if(!count.field.checkValidity() || count.field.value === '')throw new Error('Bitte einen gültigen Bestand eingeben.');
        const quantity = Number(count.field.value);
        await api('inventory.set',{inventoryItemId:variant.inventoryItem.id,locationId:level.location.id,quantity,before,requestId:crypto.randomUUID()});
        before = quantity;
      })); row.append(form);
    }
    if(variant.inventoryItem.inventoryLevels.pageInfo.hasNextPage)row.append(el('p','Weitere Lagerstandorte vorhanden; angezeigt werden die ersten 20.','muted'));
    node.append(row);
  }
  if(product.variants.pageInfo.hasNextPage)node.append(el('p','Weitere Varianten vorhanden; angezeigt werden die ersten 100.','muted'));
  return node;
}
function renderOrder(order) {
  const money=order.currentTotalPriceSet.shopMoney;
  const node=card(order.name,new Intl.NumberFormat('de-DE',{style:'currency',currency:money.currencyCode}).format(Number(money.amount)));
  const meta=el('div',undefined,'note-meta');
  meta.append(el('span',new Date(order.createdAt).toLocaleDateString('de-DE')),el('span','Zahlung: '+order.displayFinancialStatus),el('span','Versand: '+order.displayFulfillmentStatus));node.append(meta);
  const label=el('label','Bestellnotiz'),note=el('textarea');note.rows=3;note.maxLength=2000;note.value=order.note||'';label.append(note);node.append(label);
  const actions=el('div',undefined,'form-actions');actions.append(saveButton('Notiz speichern',()=>api('order.note',{id:order.id,note:note.value})));node.append(actions);return node;
}
function render() {
  $('content').replaceChildren();
  if(!rows.length){const empty=el('div',undefined,'empty');empty.append(el('h2',page==='orders'?'Noch keine Bestellungen':'Noch keine Produkte'),el('p',page==='orders'?'Hier erscheinen deine Bestellungen der letzten 60 Tage.':'Sobald Produkte in Shopify vorhanden sind, kannst du sie hier bearbeiten.'));$('content').append(empty);}
  rows.forEach(row=>$('content').append(page==='orders'?renderOrder(row):page==='inventory'?renderInventory(row):renderProducts(row)));
  $('more').hidden = !hasNext;
}
async function load(append=false){
  if(busy)return;busy=true;$('refresh').disabled=true;$('more').disabled=true;$('content').setAttribute('aria-busy','true');
  document.querySelectorAll('[data-page]').forEach(b=>b.disabled=true);
  if(!append)$('content').replaceChildren(el('p','Daten werden geladen …','loading'));
  try{
    const key=page==='orders'?'orders':'products',result=(await api(key,{after:append?cursor:null}))[key];
    rows=append?rows.concat(result.nodes):result.nodes;cursor=result.pageInfo.endCursor;hasNext=result.pageInfo.hasNextPage;render();
  }catch(error){message(error.message,true);if(session && !append){$('content').replaceChildren(el('p','Daten konnten nicht geladen werden. Bitte erneut aktualisieren.','muted'));$('more').hidden=true;}}
  finally{busy=false;$('refresh').disabled=false;$('more').disabled=false;$('content').setAttribute('aria-busy','false');document.querySelectorAll('[data-page]').forEach(b=>b.disabled=false);}
}
$('login-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;
  try{const data=await api('login',{password:$('password').value});session=data.session;$('password').value='';$('login').hidden=true;$('dashboard').hidden=false;await load();}
  catch(error){message(error.message,true);}finally{button.disabled=false;}
});
document.querySelectorAll('[data-page]').forEach(button=>button.addEventListener('click',()=>{
  page=button.dataset.page;rows=[];cursor=null;hasNext=false;
  document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b===button));
  const descriptions={products:['Produkte & Preise','Deine Produkte. Bis ins Detail.'],inventory:['Lagerbestand','Dein Bestand – je Variante und Lagerstandort.'],orders:['Bestellungen','Die letzten 60 Tage. Alles im Blick.']};
  [$('page-title').textContent,$('page-description').textContent]=descriptions[page];load();
}));
$('refresh').addEventListener('click',()=>load());$('more').addEventListener('click',()=>load(true));
$('logout').addEventListener('click',async()=>{try{await api('logout');}catch(error){message(error.message,true);}finally{signedOut();}});
window.addEventListener('pagehide',signedOut);
