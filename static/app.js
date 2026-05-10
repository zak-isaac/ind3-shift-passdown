/* Shift Passdown Generator – app.js */

/** Clone last entry-row in a section container and append a blank copy. */
function addRow(containerId) {
  const container = document.getElementById(containerId);
  const rows = container.querySelectorAll('.entry-row');
  const last  = rows[rows.length - 1];
  const clone = last.cloneNode(true);
  clone.querySelectorAll('input, textarea, select').forEach(el => { el.value = ''; });
  container.insertBefore(clone, container.lastElementChild);
}

/** Remove the .entry-row ancestor of the clicked button. */
function removeRow(btn) {
  const row = btn.closest('.entry-row');
  if (!row) return;
  const container = row.parentElement;
  const remaining = container.querySelectorAll('.entry-row');
  if (remaining.length <= 1) {
    // Keep at least one empty row rather than removing the last one
    row.querySelectorAll('input, textarea').forEach(el => { el.value = ''; });
    return;
  }
  row.remove();
}

/** Add a blank MST-extra happening row. */
function addExtraHappening() {
  const container = document.getElementById('extra-happening-rows');
  const template  = container.querySelector('.entry-row');
  const clone     = template.cloneNode(true);
  clone.querySelectorAll('input, textarea').forEach(el => { el.value = ''; });
  container.insertBefore(clone, container.lastElementChild);
}

/** Persist technician name in localStorage so it survives form resets. */
document.addEventListener('DOMContentLoaded', () => {
  const nameField = document.getElementById('tech_name_field');
  if (!nameField) return;

  const saved = localStorage.getItem('tech_name');
  if (saved) nameField.value = saved;

  nameField.addEventListener('change', () => {
    localStorage.setItem('tech_name', nameField.value.trim());
  });
});
