let loading = false;
async function refreshDiagnostics() {
  if (loading) return; loading = true;
  const button = document.getElementById('diagnostic-refresh'); button.disabled = true;
  try {
    const snapshot = await window.IgorekDiagnostics.snapshot();
    document.getElementById('diagnostic-title').textContent = snapshot.title;
    document.getElementById('diagnostic-note').textContent = snapshot.note;
    const box = document.getElementById('diagnostic-data'); box.replaceChildren();
    for (const row of snapshot.rows) {
      const card = document.createElement('div'); card.className = 'profile-card';
      const name = document.createElement('strong'); name.textContent = row.name;
      const text = document.createElement('div'); text.textContent = row.value;
      card.append(name, text); box.append(card);
    }
  } catch { document.getElementById('diagnostic-note').textContent = 'Не удалось получить состояние. Повторите обновление.'; }
  finally { loading = false; button.disabled = false; }
}
document.getElementById('diagnostic-refresh').addEventListener('click', refreshDiagnostics);
refreshDiagnostics();
setInterval(refreshDiagnostics, 2000);
