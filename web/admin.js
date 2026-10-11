'use strict';
const base = new URL(document.currentScript.src).pathname.replace(/\/admin\.js$/, '');
const $ = id => document.getElementById(id);
let session = '', page = 'overview', rows = [], cursor = null, hasNext = false, busy = false;
let toastTimer;
function message(value, error = false) {
  $('message').textContent = value; $('message').classList.toggle('error', error); $('message').hidden = false;
  const dialog=[...document.querySelectorAll('dialog[open]')].at(-1);if(dialog){dialog.querySelector('.dialog-message')?.remove();const note=el('p',value,'dialog-message');note.setAttribute('role',error?'alert':'status');dialog.prepend(note);}
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('message').hidden = true, error ? 14000 : 5000);
}
function signedOut() {
  session = ''; $('password').value = ''; $('content').replaceChildren(); rows = [];
  $('dashboard').hidden = true; $('login').hidden = false;
}
async function api(action, values = {}) {
  const response = await fetch(base + '/api', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...values, action, session}), cache: 'no-store'});
  let data;
  try { data = await response.json(); }
  catch (_) { throw new Error('Die Datenabfrage liefert keine gültige Antwort (HTTP ' + response.status + '). Bei 502: Shopify-Abfrage fehlgeschlagen. Bitte neu laden und erneut anmelden.'); }
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
  })); actions.append(actionButton('Details & Varianten',()=>editCatalog('product',product.id))); node.append(actions);
  for (const variant of product.variants.nodes) {
    const row = el('div',undefined,'variant'); row.append(el('h3',variant.title === 'Default Title' ? 'Preis' : variant.title));
    const form = el('div',undefined,'inline-form'), price = input('Preis in Shop-Währung',variant.price,'number'); price.field.min = '0'; price.field.step = '.01';
    form.append(price.wrap,saveButton('Preis speichern',async()=>{
      if(!price.field.checkValidity() || !price.field.value)throw new Error('Bitte einen gültigen Preis eingeben.');
      await api('variant.price',{productId:product.id,id:variant.id,price:price.field.value});
    })); row.append(form); node.append(row);
  }
  if(product.variants.pageInfo.hasNextPage)node.append(el('p','Weitere Varianten vorhanden. Hier werden die ersten 10 angezeigt.','muted'));
  return node;
}
function renderInventory(product) {
  const node = card(product.title); node.append(actionButton('Lageretiketten & Barcode',()=>printLabel(product)));
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
    if(variant.inventoryItem.inventoryLevels.pageInfo.hasNextPage)row.append(el('p','Weitere Lagerstandorte vorhanden; angezeigt werden die ersten 5.','muted'));
    node.append(row);
  }
  if(product.variants.pageInfo.hasNextPage)node.append(el('p','Weitere Varianten vorhanden; angezeigt werden die ersten 10.','muted'));
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
function renderCollection(collection) {
  const count = collection.productsCount;
  const node = card(collection.title, String(count.count) + (count.precision === 'EXACT' ? '' : '+') + ' Produkte');
  node.append(el('p', count.count === 0 ? 'Diese Kategorie enthält noch keine Produkte.' : 'Kategorie in Shopify vorhanden.', 'muted'));
  const link = el('a', 'Kategorie im Shop öffnen ↗', 'secondary');
  link.href = '/collections/' + encodeURIComponent(collection.handle); link.target = '_blank'; link.rel = 'noopener'; node.append(link,actionButton('Kategorie bearbeiten',()=>editCatalog('collection',collection.id)),actionButton('Produkte zuordnen',()=>membership(collection)));
  return node;
}
function renderOverview(data) {
  const products = data.products.nodes, orders = data.orders.nodes;
  const grid = el('div', undefined, 'overview-grid');
  const metrics = [
    ['Produkte geprüft', products.length], ['Aktive Produkte', products.filter(p=>p.status==='ACTIVE').length],
    ['Produktentwürfe', products.filter(p=>p.status==='DRAFT').length],
    ['Bestellungen geprüft', orders.length],
    ['Noch nicht erfüllt', orders.filter(o=>o.displayFulfillmentStatus==='UNFULFILLED').length],
    ['Zahlung ausstehend', orders.filter(o=>['PENDING','PARTIALLY_PAID'].includes(o.displayFinancialStatus)).length]
  ];
  for (const [title,value] of metrics) {const item=card(title);item.append(el('strong',String(value),'metric'));grid.append(item);}
  const note=el('p','Auswertung der ersten bis zu 100 Produkte und der neuesten bis zu 50 Bestellungen innerhalb der zugänglichen letzten 60 Tage. Aktive Produkte sind nicht automatisch in jedem Vertriebskanal veröffentlicht.','muted');
  if(data.products.pageInfo.hasNextPage || data.orders.pageInfo.hasNextPage)note.append(document.createTextNode(' Weitere Daten vorhanden; dies sind keine Shop-Gesamtsummen.'));
  const check=card('Deine nächsten Schritte');
  check.append(el('p',products.length ? 'Produkte vorhanden. Entwürfe vor der Veröffentlichung prüfen.' : 'Noch keine Produkte vorhanden. Lege deine ersten Artikel in Shopify an.'));
  check.append(el('p','Versand, Zahlungsmethoden und Pflichtseiten im Shopify-Admin prüfen. Diese Einstellungen werden hier nicht automatisch geprüft.','muted'));
  $('content').replaceChildren(grid,note,check);
}
function filteredRows() {
  const term=$('search').value.trim().toLocaleLowerCase('de-DE');
  return rows.filter(row=>JSON.stringify([row.title,row.name,row.vendor,row.productType,row.note,row.handle,...(row.variants?.nodes||[]).map(v=>[v.title,v.sku,v.barcode])]).toLocaleLowerCase('de-DE').includes(term));
}
function render() {
  $('content').replaceChildren();
  if(['products','collections'].includes(page))$('content').append(actionButton(page==='products'?'＋ Produkt anlegen':'＋ Kategorie anlegen',()=>editCatalog(page==='products'?'product':'collection')));
  const visible=filteredRows();
  if(!visible.length){const empty=el('div',undefined,'empty');empty.append(el('h2',$('search').value.trim()?'Keine passenden Einträge':page==='collections'?'Noch keine Kategorien':page==='orders'?'Noch keine Bestellungen':'Noch keine Produkte'),el('p',page==='orders'?'Hier erscheinen deine Bestellungen der letzten 60 Tage.':'Sobald Produkte in Shopify vorhanden sind, kannst du sie hier bearbeiten.'));$('content').append(empty);}
  visible.forEach(row=>$('content').append(page==='orders'?renderOrder(row):page==='collections'?renderCollection(row):page==='inventory'?renderInventory(row):renderProducts(row))); 
  $('more').hidden = !hasNext;
}
async function load(append=false){
  if(busy)return;busy=true;$('list-tools').hidden=page==='overview';$('export').hidden=page!=='orders';$('refresh').disabled=true;$('more').disabled=true;$('content').setAttribute('aria-busy','true');
  document.querySelectorAll('[data-page]').forEach(b=>b.disabled=true);
  if(!append)$('content').replaceChildren(el('p','Daten werden geladen …','loading'));
  try{
    if(['discounts','ticker','mail'].includes(page)){$('list-tools').hidden=true;$('more').hidden=true;await featurePage();return;}
    if(page==='overview') {const data=await api('overview');renderOverview(data);$('more').hidden=true;return;}
    const key=page==='orders'?'orders':page==='collections'?'collections':'products',result=(await api(key,{after:append?cursor:null}))[key];
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
  page=button.dataset.page;$('search').value='';rows=[];cursor=null;hasNext=false;
  document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b===button));
  const descriptions={discounts:['Gutscheine & Rabatte','Codes und automatische Rabatte ab Mindestmenge.'],ticker:['News-Ticker','Aktuelle Hinweise für deinen Shop.'],mail:['Anfragen & Newsletter','Postfach, Entwürfe und Versandstatus.'],overview:['Shop-Übersicht','Dein aktueller Stand aus Shopify.'],collections:['Kategorien','Deine Shopify-Kollektionen und ihr Produktbestand.'],products:['Produkte & Preise','Deine Produkte. Bis ins Detail.'],inventory:['Lagerbestand','Dein Bestand – je Variante und Lagerstandort.'],orders:['Bestellungen','Die letzten 60 Tage. Alles im Blick.']};
  [$('page-title').textContent,$('page-description').textContent]=descriptions[page];load();
}));
$('refresh').addEventListener('click',()=>load());$('more').addEventListener('click',()=>load(true));
$('logout').addEventListener('click',async()=>{try{await api('logout');}catch(error){message(error.message,true);}finally{signedOut();}});
window.addEventListener('pagehide',signedOut);

$('search').addEventListener('input',render);
function csvCell(value) {
  let text=String(value ?? '');
  if (/^[\s]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text="'"+text;
  return '"'+text.replace(/"/g,'""')+'"';
}
$('export').addEventListener('click',()=>{
  const selected=filteredRows();
  const table=[['Bestellung','Datum','Zahlung','Erfüllung','Betrag','Währung','Notiz'],...selected.map(o=>[o.name,o.createdAt,o.displayFinancialStatus,o.displayFulfillmentStatus,o.currentTotalPriceSet.shopMoney.amount,o.currentTotalPriceSet.shopMoney.currencyCode,o.note])];
  const url=URL.createObjectURL(new Blob(['\ufeff'+table.map(row=>row.map(csvCell).join(';')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
  const link=el('a');link.href=url;link.download='plotwerk-bestellungen.csv';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
