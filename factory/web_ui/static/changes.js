(function (root) {
  "use strict";
  function filesTree(files, query, status) {
    const tree = { name: "", folders: new Map(), files: [] };
    for (const file of files) {
      if (query && !file.path.toLocaleLowerCase().includes(query.toLocaleLowerCase())) continue;
      if (status && file.status !== status) continue;
      const parts = file.path.split("/"); let node = tree;
      for (const name of parts.slice(0, -1)) {
        if (!node.folders.has(name)) node.folders.set(name, { name, folders: new Map(), files: [] });
        node = node.folders.get(name);
      }
      node.files.push(file);
    }
    return tree;
  }
  function statistics(data, english) {
    const text = (ru, en) => english ? en : ru;
    const totals = data.totals || {}, notes = [];
    const label = totals.partial ? text("Частичный итог: ", "Partial total: ") : text("Изменено файлов: ", "Changed: ");
    const scope = data.scope === "session_files" ? text("Файлы с записанными правками этой сессии.", "Files with recorded edits in this session.") : data.scope === "tracked_and_untracked" ? text("Отслеживаемые файлы и новые файлы вне Git; включая незакоммиченные правки.", "Tracked and untracked files, including uncommitted edits.") : text("Отслеживаемые файлы; включая добавленные в индекс и незакоммиченные правки.", "Tracked files only, including staged additions and uncommitted edits.");
    const tooltip = scope + " " + text("+ — добавленные строки, − — удалённые строки относительно указанной базы. Это не размер проекта. Фильтр дерева не меняет общий итог.", "+ means added lines, − means removed lines relative to the stated base. This is not project size. Tree filtering does not change the total.");
    const names = {binary:text("бинарные", "binary"),too_large:text("превышен размер файла", "file size limit"),batch_limit:text("лимит общего размера базовых версий", "baseline batch size limit"),no_baseline:text("нет базовой версии", "missing baseline"),not_regular:text("не обычные текстовые файлы", "not regular text files"),read_error:text("ошибка чтения", "read error")};
    if (totals.unknown) notes.push(text("Строки посчитаны для ", "Lines counted for ") + totals.counted_files + text(" из ", " of ") + totals.files + text(" показанных файлов. Без счётчика: ", " displayed files. No line count: ") + Object.entries(totals.unavailable || {}).map(([reason,count]) => (names[reason] || names.read_error) + " — " + count).join("; ") + ".");
    if (data.omitted) {
      const limits = data.omitted_by_reason || {};
      const reasons = [];
      if (limits.file_limit) reasons.push(text("лимит списка — ", "file list limit — ") + limits.file_limit);
      if (limits.untracked_limit) reasons.push(text("лимит новых файлов вне Git — ", "untracked file limit — ") + limits.untracked_limit);
      notes.push(text("Не показаны и не входят в итог: ", "Not displayed or counted: ") + data.omitted + text(" файлов (", " files (") + reasons.join("; ") + ").");
    }
    if (data.excluded_untracked) notes.push(text("Новые файлы вне Git не включены: ", "Untracked files excluded: ") + data.excluded_untracked + ".");
    return { label: label + (totals.files || 0) + (english ? " files" : totals.partial ? " файлов" : ""), scope, tooltip, notes };
  }
  function render(options) {
    const { model, data, diff, onSelect, onRefresh, onMode, onBase, onUntracked, onView, buildDiff } = options;
    const english = options.english;
    const text = (ru, en) => english ? en : ru;
    const el = (tag, cls, content) => { const node = document.createElement(tag); node.className = cls || ""; if (content != null) node.textContent = content; return node; };
    const button = (label, action, cls) => { const node = el("button", cls || "mini-button", label); node.type = "button"; node.addEventListener("click", action); return node; };
    const select = (values, selected, action, label) => {
      const node = el("select", "review-select"); node.setAttribute("aria-label", label);
      for (const [value, title] of values) { const option = el("option", "", title); option.value = value; node.append(option); }
      node.value = selected; node.addEventListener("change", () => action(node.value)); return node;
    };
    const shell = el("div", "review-shell");
    const toolbar = el("div", "review-toolbar");
    toolbar.append(select([["session",text("Сессия","Session")],["working",text("Незакоммиченные","Uncommitted")],["branch",text("Ветка","Branch")]], model.mode, onMode, text("Режим сравнения","Comparison mode")));
    if (model.mode === "branch") toolbar.append(select([["HEAD","HEAD"], ...(data.refs || []).map(ref => [ref,ref])], model.base || data.base || "HEAD", onBase, text("Базовая ветка","Base branch")));
    if (model.mode !== "session") {
      const label = el("label", "review-untracked");
      const input = el("input"); input.type = "checkbox"; input.checked = Boolean(model.includeUntracked);
      input.addEventListener("change", () => onUntracked(input.checked));
      label.append(input,document.createTextNode(text("Новые файлы вне Git", "Untracked files"))); toolbar.append(label);
    }
    const totals = data.totals || {}, summary = statistics(data, english);
    const stats = el("div","review-totals", summary.label); stats.title = summary.tooltip;
    const additions = el("span","change-additions"," +" + (totals.additions || 0)); additions.title=text("Добавленные строки", "Added lines");
    const deletions = el("span","change-deletions"," −" + (totals.deletions || 0)); deletions.title=text("Удалённые строки", "Removed lines");
    stats.append(additions,deletions);
    toolbar.append(stats,button(text("Обновить","Refresh"),onRefresh));
    const revision = (data.revision || "").slice(0,8);
    const baseLabel = data.mode === "branch" ? text("Общий предок HEAD и ","Common ancestor of HEAD and ") + (data.base || "HEAD") + " (" + revision + ")" + text(" → текущая рабочая папка", " → current working tree") : data.mode === "working" && revision ? text("Последний коммит HEAD (","Latest commit HEAD (") + revision + ")" + text(" → текущая рабочая папка", " → current working tree") : data.base_label || "";
    shell.append(toolbar,el("div","review-base",baseLabel));
    shell.append(el("div","review-base",summary.scope));
    if (data.warning) shell.append(el("p","review-note",data.warning));
    for (const note of summary.notes) shell.append(el("p","review-note",note));
    const layout = el("div","review-layout");shell.append(layout);
    const viewer = el("section","review-viewer");viewer.setAttribute("aria-label",text("Изменения выбранного файла","Selected file changes"));
    const nav = el("div","review-file-toolbar");const files = data.files || [];
    let visible = files.filter(f => (!model.query || f.path.toLocaleLowerCase().includes(model.query.toLocaleLowerCase())) && (!model.filter || f.status === model.filter));
    let index = visible.findIndex(f => f.path === diff?.path);
    const previous = button("←",()=>onSelect(visible[index-1].path));previous.disabled=index<=0;previous.title=text("Предыдущий файл","Previous file");previous.setAttribute("aria-label",previous.title);
    const next = button("→",()=>onSelect(visible[index+1].path));next.disabled=index<0 || index>=visible.length-1;next.title=text("Следующий файл","Next file");next.setAttribute("aria-label",next.title);
    nav.append(previous,next,el("span","review-position",index>=0 ? (index+1)+" / "+visible.length : ""));
    if (diff && diff.data?.status !== "deleted") nav.append(button(text("Открыть файл","Open file"),()=>onView(diff.path)));
    viewer.append(nav);
    if (options.error) viewer.append(el("p","side-error",options.error));
    else if (options.loading) viewer.append(el("p","review-note",text("Загрузка изменений…","Loading changes…")));
    else if (diff) viewer.append(buildDiff(diff));
    else viewer.append(el("p","review-note", files.length ? text("Выбери файл в дереве справа","Select a file in the tree") : text("Изменений нет","No changes")));
    const separator = el("div","review-separator");separator.tabIndex=0;separator.setAttribute("role","separator");separator.setAttribute("aria-orientation","vertical");separator.setAttribute("aria-label",text("Ширина дерева файлов","File tree width"));
    layout.style.setProperty("--tree-width",(model.width || 260)+"px");
    separator.addEventListener("pointerdown",event=>{ if(event.button!==0)return; const start=event.clientX,width=model.width||260;separator.setPointerCapture(event.pointerId);const move=e=>{model.width=Math.max(180,Math.min(480,width+start-e.clientX));layout.style.setProperty("--tree-width",model.width+"px");};const stop=()=>{separator.removeEventListener("pointermove",move);separator.removeEventListener("pointerup",stop);separator.removeEventListener("pointercancel",stop);};separator.addEventListener("pointermove",move);separator.addEventListener("pointerup",stop);separator.addEventListener("pointercancel",stop);});
    separator.addEventListener("keydown",event=>{if(!["ArrowLeft","ArrowRight"].includes(event.key))return;event.preventDefault();model.width=Math.max(180,Math.min(480,(model.width||260)+(event.key==="ArrowLeft"?20:-20)));layout.style.setProperty("--tree-width",model.width+"px");});
    const sidebar = el("aside","review-tree-panel");const search = el("input","review-search");search.type="search";search.placeholder=text("Фильтрация файлов…","Filter files…");search.setAttribute("aria-label",search.placeholder);search.value=model.query;
    const statuses = [["",text("Все статусы","All statuses")],["added",text("Добавлены","Added")],["modified",text("Изменены","Modified")],["deleted",text("Удалены","Deleted")],["renamed",text("Переименованы","Renamed")]];
    const filter=select(statuses,model.filter,value=>{model.filter=value;draw();},text("Статус файла","File status"));
    const treeArea=el("div","review-tree");treeArea.setAttribute("aria-label",text("Изменённые файлы","Changed files"));sidebar.append(search,filter,treeArea);
    function draw() {
      visible = files.filter(f => (!model.query || f.path.toLocaleLowerCase().includes(model.query.toLocaleLowerCase())) && (!model.filter || f.status === model.filter));
      index = visible.findIndex(f => f.path === diff?.path); previous.disabled=index<=0; next.disabled=index<0 || index>=visible.length-1; nav.querySelector(".review-position").textContent=index>=0?(index+1)+" / "+visible.length:"";
      treeArea.replaceChildren();const tree=filesTree(files,model.query,model.filter);let count=0;
      function branch(node,parent,depth,prefix) {
        for (const [name,folder] of [...node.folders].sort((a,b)=>a[0].localeCompare(b[0]))) {
          const path=prefix+name+"/";const collapsed=!model.query && model.collapsed.has(path);
          const row=button((collapsed?"▸ ":"▾ ")+name,()=>{if(model.collapsed.has(path))model.collapsed.delete(path);else model.collapsed.add(path);draw();},"review-folder");row.style.paddingLeft=(8+depth*14)+"px";row.setAttribute("aria-expanded",String(!collapsed));parent.append(row);
          if(!collapsed)branch(folder,parent,depth+1,path);
        }
        for(const file of node.files.sort((a,b)=>a.path.localeCompare(b.path))) {
          count++;const row=button("",()=>onSelect(file.path),"review-file"+(diff?.path===file.path?" selected":""));row.style.paddingLeft=(8+depth*14)+"px";row.title=file.path + (file.note ? " — " + file.note : "");row.setAttribute("aria-current",diff?.path===file.path?"true":"false");
          row.append(el("span","review-status",{added:"A",deleted:"D",renamed:"R",modified:"M"}[file.status]||"M"),el("span","review-filename",file.path.split("/").pop()));
          row.append(el("span","change-additions",file.additions==null?"?":"+"+file.additions),el("span","change-deletions",file.deletions==null?"":"−"+file.deletions));parent.append(row);
        }
      }
      branch(tree,treeArea,0,"");if(!count && !tree.folders.size)treeArea.append(el("p","review-note",text("Нет подходящих файлов","No matching files")));
    }
    treeArea.addEventListener("scroll",()=>{model.scroll=treeArea.scrollTop;});
    requestAnimationFrame(()=>{treeArea.scrollTop=model.scroll||0;});
    search.addEventListener("input",()=>{model.query=search.value;draw();});draw();layout.append(viewer,separator,sidebar);return shell;
  }
  root.IgorekChanges={filesTree,statistics,render};
  if(typeof module!=="undefined"&&module.exports)module.exports=root.IgorekChanges;
})(typeof window!=="undefined"?window:globalThis);
