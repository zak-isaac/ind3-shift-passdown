"use strict";
let v3ReturnFocus = null;
async function v3OpenHappening(entryId,shiftId){
  const root=document.getElementById('v3-modal-root');
  v3ReturnFocus=document.activeElement;
  try{
    const response=await fetch(`/v3/happening/${entryId}?shift_id=${encodeURIComponent(shiftId)}`,{credentials:'same-origin'});
    if(!response.ok)throw Error(`Unable to open work order (HTTP ${response.status}).`);
    root.innerHTML=await response.text();
    v3AttachSpelling(root);
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

/* Spelling review never autosaves or alters the original Cetaris source. */
function v3AttachSpelling(root=document) {
  root.querySelectorAll('textarea').forEach(area => {
    if (area.dataset.v3SpellingAttached === '1') return;
    if (!area.closest('#v3-edit-form, #manual-dialog, .d2-tools')) return;
    area.dataset.v3SpellingAttached = '1';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'button button-outline v3-spelling-trigger';
    btn.textContent = 'Check spelling';
    btn.addEventListener('click', () => v3PreviewSpelling(area, btn));
    area.insertAdjacentElement('afterend', btn);
  });
}
async function v3PreviewSpelling(area, btn) {
  const old = area.parentElement.querySelector('.v3-spelling-suggestion');
  if (old) old.remove();
  if (!area.value.trim()) return;
  const shiftId = document.querySelector('#v3-edit-form [name="shift_id"]')?.value ||
                  document.querySelector('#manual-dialog [name="shift_id"]')?.value ||
                  document.querySelector('.d2-tools [name="shift_id"]')?.value;
  if (!shiftId) { alert('Unable to identify the active shift.'); return; }
  const original = area.value;
  btn.disabled = true;
  btn.textContent = 'Checking…';
  try {
    const body = new URLSearchParams({shift_id: shiftId, text: original});
    const response = await fetch('/v3/spelling/preview', {
      method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body, credentials:'same-origin'
    });
    if (!response.ok) throw Error('Spell check unavailable. Please review manually.');
    const result = await response.json();
    const panel = document.createElement('div');
    panel.className = 'v3-spelling-suggestion';
    panel.setAttribute('role','status');
    if (!result.changed) {
      panel.textContent = 'No suggested corrections. You can still edit the text yourself.';
    } else {
      const title = document.createElement('strong');
      title.textContent = 'Suggested spelling and punctuation';
      const proposed = document.createElement('p');
      proposed.textContent = result.suggested;
      proposed.style.whiteSpace = 'pre-wrap';
      const apply = document.createElement('button');
      apply.type = 'button';
      apply.className = 'button button-blue';
      apply.textContent = 'Apply suggestion';
      apply.addEventListener('click', () => {
        if (area.value !== original) {
          alert('The text has changed since the check. Please check spelling again.');
          return;
        }
        area.value = result.suggested;
        area.dispatchEvent(new Event('input', {bubbles:true}));
        panel.remove();
        area.focus();
      });
      const keep = document.createElement('button');
      keep.type = 'button';
      keep.className = 'button button-outline';
      keep.textContent = 'Keep original';
      keep.addEventListener('click', () => panel.remove());
      panel.append(title, proposed, apply, keep);
    }
    area.insertAdjacentElement('afterend', panel);
  } catch(error) {
    alert(error.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Check spelling';
  }
}
document.addEventListener('DOMContentLoaded', () => v3AttachSpelling(document));

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
