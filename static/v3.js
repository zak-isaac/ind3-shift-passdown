"use strict";
let v3ReturnFocus = null;
async function v3OpenHappening(entryId,shiftId,editImmediately=false){
  const root=document.getElementById('v3-modal-root');
  v3ReturnFocus=document.activeElement;
  try{
    const response=await fetch(`/v3/happening/${entryId}?shift_id=${encodeURIComponent(shiftId)}`,{credentials:'same-origin'});
    if(!response.ok)throw Error(`Unable to open work order (HTTP ${response.status}).`);
    root.innerHTML=await response.text();
    root.hidden=false;document.body.style.overflow='hidden';
    if(editImmediately)v3ToggleEdit(true);
    root.querySelector('.modal-x')?.focus();
  }catch(error){alert(error.message);}
}
function v3CloseModal(){const root=document.getElementById('v3-modal-root');root.hidden=true;root.replaceChildren();document.body.style.overflow='';v3ReturnFocus?.focus();}
function v3ToggleEdit(on){
  document.getElementById('v3-view-fields').hidden=on;
  document.getElementById('v3-view-actions').hidden=on;
  document.getElementById('v3-edit-form').hidden=!on;
  if(on)document.querySelector('#v3-edit-form textarea')?.focus();
}
function v3OpenManual(){document.getElementById('manual-dialog').showModal()}
document.addEventListener('keydown',(event)=>{if(event.key==='Escape'&&!document.getElementById('v3-modal-root')?.hidden)v3CloseModal()});

// Design 2: UI-only table search/filter; the server still enforces imports and edit permissions.
let d2CurrentPage=1;
let d2SelectedCategory='';
function d2ChooseCategory(category,btn){
  d2SelectedCategory=(category||'').toLowerCase();
  document.querySelectorAll('.rs-filter').forEach(item=>{
    const active=item===btn;
    item.classList.toggle('active',active);
    item.setAttribute('aria-pressed',String(active));
  });
  d2FilterHappenings(true);
}
function d2ShowExcluded(){
  const checkbox=document.getElementById('rs-show-excluded');
  if(!checkbox)return;
  checkbox.checked=true;
  d2FilterHappenings(true);
  document.querySelector('.rs-is-excluded:not([hidden])')?.scrollIntoView({block:'nearest',behavior:'smooth'});
}
function d2FilterHappenings(resetPage=false){
  if(resetPage)d2CurrentPage=1;
  const search=(document.getElementById('d2-search')?.value||'').toLowerCase().trim();
  const includeExcluded=Boolean(document.getElementById('rs-show-excluded')?.checked);
  const rows=[...document.querySelectorAll('.d2-data-row')];
  const matched=rows.filter(row=>{
    const text=(row.dataset.search+' '+row.innerText).toLowerCase();
    return (!search||text.includes(search))&&
           (!d2SelectedCategory||row.dataset.type===d2SelectedCategory)&&
           (includeExcluded||row.dataset.excluded!=='1');
  });
  const pageSize=5,pageCount=Math.max(1,Math.ceil(matched.length/pageSize));
  d2CurrentPage=Math.max(1,Math.min(d2CurrentPage,pageCount));
  const start=(d2CurrentPage-1)*pageSize;
  const shown=new Set(matched.slice(start,start+pageSize));
  rows.forEach(row=>{row.hidden=!shown.has(row)});
  const count=document.getElementById('d2-visible-count');
  if(count)count.textContent=matched.length?
    'Showing '+(start+1)+'–'+Math.min(start+pageSize,matched.length)+' of '+matched.length+' work orders':
    'Showing 0 of 0 work orders';
  const label=document.getElementById('rs-page-label');
  if(label)label.textContent='Page '+d2CurrentPage+' of '+pageCount;
  const numberBtns=document.getElementById('rs-page-buttons');
  if(numberBtns){
    numberBtns.replaceChildren();
    for(let p=1;p<=pageCount;p++){
      const btn=document.createElement('button');btn.type='button';
      btn.textContent=String(p);btn.classList.toggle('active',p===d2CurrentPage);
      btn.setAttribute('aria-label','Page '+p);
      if(p===d2CurrentPage)btn.setAttribute('aria-current','page');
      btn.addEventListener('click',()=>{d2CurrentPage=p;d2FilterHappenings()});
      numberBtns.append(btn);
    }
  }
  const nav=document.getElementById('rs-pagination');
  if(nav){
    const buttons=nav.querySelectorAll(':scope > button');
    if(buttons.length===4){
      buttons[0].disabled=buttons[1].disabled=d2CurrentPage<=1;
      buttons[2].disabled=buttons[3].disabled=d2CurrentPage>=pageCount;
    }
  }
}
function d2ChangePage(delta){d2CurrentPage+=delta;d2FilterHappenings()}
function d2GoFirst(){d2CurrentPage=1;d2FilterHappenings()}
function d2GoLast(){const total=[...document.querySelectorAll('.d2-data-row:not([hidden])')].length;
  // Count all matching rows by moving forward until the last page; never alter data.
  d2CurrentPage=Number(document.getElementById('rs-page-buttons')?.children.length)||1;
  d2FilterHappenings();
}
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('d2-search')?.addEventListener('input',()=>d2FilterHappenings(true));
  document.getElementById('rs-show-excluded')?.addEventListener('change',()=>d2FilterHappenings(true));
  document.querySelector('.rs-filter.active')?.setAttribute('aria-pressed','true');
  d2FilterHappenings();
});
function v3ToggleAudit(button){
  const panel=document.getElementById('rs-audit-panel');
  if(!panel)return;
  panel.open=!panel.open;
  button?.setAttribute('aria-expanded',String(panel.open));
}
function v3SyncMissingKeys(){
  const form=document.getElementById('rs-loto-form');
  if(!form)return;
  const selected=[...form.querySelectorAll('[data-rs-missing-key]:checked')].map(el=>el.value);
  form.querySelector('[name="missing_keys"]').value=selected.join(', ');
  const label=document.getElementById('rs-missing-label');
  if(label)label.textContent=selected.length?selected.join(', '):'No missing keys';
}

// LOTO missing-keys picker: Key 1-20 checkbox grid, mirrors V2's dropdown behavior.
function v3ToggleLotoDropdown(btn){
  const panel=btn.parentElement.querySelector('.d2-loto-panel');
  if(panel)panel.hidden=!panel.hidden;
}
function v3UpdateLotoLabel(checkbox){
  const form=checkbox.closest('form');
  const checked=Array.from(form.querySelectorAll('.d2-loto-grid input:checked')).map(c=>c.value);
  const joined=checked.join(', ');
  form.querySelector('#v3-loto-label').textContent=joined||'Select missing keys...';
  form.querySelector('#v3-loto-missing-hidden').value=joined;
}
document.addEventListener('click',(event)=>{
  document.querySelectorAll('.d2-loto-panel').forEach(panel=>{
    if(!panel.hidden&&!panel.parentElement.contains(event.target))panel.hidden=true;
  });
});

/* The XLSX file is parsed server-side. Technician choices are returned from the uploaded file. */
function v3OpenImport(){ document.getElementById('v3-import-dialog')?.showModal(); }
function v3CloseImport(){ document.getElementById('v3-import-dialog')?.close(); }
function v3CloseTechs(){ document.getElementById('v3-tech-dialog')?.close(); }
function v3UpdateTechCount(){
  const dlg=document.getElementById('v3-tech-dialog');
  if(!dlg)return;
  const checks=[...dlg.querySelectorAll('input[name="techs"]')];
  const chosen=checks.filter(x=>x.checked).length;
  const label=document.getElementById('v3-tech-total');
  if(label)label.textContent=`${chosen} of ${checks.length} technicians selected`;
  const submit=dlg.querySelector('button[type=submit]');
  if(submit)submit.disabled=chosen===0;
}
function v3ToggleAllTechs(on){
  const dlg=document.getElementById('v3-tech-dialog');
  dlg?.querySelectorAll('input[name="techs"]').forEach(x=>{x.checked=on});
  v3UpdateTechCount();
}
document.addEventListener('DOMContentLoaded',()=>{
  const dlg=document.getElementById('v3-tech-dialog');
  if(dlg){
    if(dlg.dataset.staged==='true')dlg.showModal();
    dlg.addEventListener('change',v3UpdateTechCount);
    const search=document.getElementById('v3-tech-search');
    search?.addEventListener('input',()=>{
      const q=search.value.toLowerCase().trim();
      dlg.querySelectorAll('.d2-tech-choice').forEach(c=>{c.hidden=!c.innerText.toLowerCase().includes(q)});
    });
    v3UpdateTechCount();
  }
});
