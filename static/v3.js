"use strict";
let v3ReturnFocus = null;
async function v3OpenHappening(entryId,shiftId){
  const root=document.getElementById('v3-modal-root');
  v3ReturnFocus=document.activeElement;
  try{
    const response=await fetch(`/v3/happening/${entryId}?shift_id=${encodeURIComponent(shiftId)}`,{credentials:'same-origin'});
    if(!response.ok)throw Error(`Unable to open work order (HTTP ${response.status}).`);
    root.innerHTML=await response.text();
    root.hidden=false;document.body.style.overflow='hidden';
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
function d2FilterHappenings(){
  const q=(document.getElementById('d2-search')?.value||'').toLowerCase().trim();
  const type=(document.getElementById('d2-type')?.value||'').toLowerCase();
  const asset=(document.getElementById('d2-asset')?.value||'').toLowerCase();
  let visible=0;
  document.querySelectorAll('.d2-data-row').forEach(row=>{
    const text=(row.dataset.search+' '+row.innerText).toLowerCase();
    const keep=(!q||text.includes(q))&&(!type||row.dataset.type===type)&&(!asset||row.dataset.asset===asset);
    row.hidden=!keep;
    if(keep)visible++;
  });
  const count=document.getElementById('d2-visible-count');if(count)count.textContent=String(visible);
}
for(const id of ['d2-search','d2-type','d2-asset'])document.getElementById(id)?.addEventListener('input',d2FilterHappenings);

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
