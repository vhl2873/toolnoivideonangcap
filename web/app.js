const TASKS = ["Nối video","Chuẩn hóa video","Chia nhỏ video","Phóng to/thu nhỏ","Thêm hiệu ứng","Batch voice + cut + zoom"];
const STATUSES = ["Bản nháp","Chưa chạy","Đang chờ","Đang chạy","Tạm dừng","Hoàn thành","Lỗi","Đã hủy"];
const PRIORITIES = ["Khẩn cấp","Cao","Bình thường","Thấp"];
const STATUS_COLORS = {
  "Bản nháp":"#7c8ba0","Chưa chạy":"#7c8ba0","Đang chờ":"#e7b93c","Đang chạy":"#5790ff",
  "Tạm dừng":"#f58b45","Hoàn thành":"#36c982","Lỗi":"#ee5d68","Đã hủy":"#58687b"
};
const PAGE_TITLES = {
  overview:"Tổng quan dự án",projects:"Quản lý dự án",queue:"Hàng đợi xử lý",
  outputs:"File đầu ra",history:"Lịch sử hoạt động",settings:"Cài đặt hệ thống",workspace:"Không gian dự án"
};
const state = {projects:[], selectedId:null, statusFilter:"", query:"", page:"overview", settings:{}};
const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]));
const ICON_PATHS = {
  open:'<path d="M14 3h7v7"/><path d="M10 14 21 3"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/>',
  play:'<path d="m7 4 13 8-13 8z"/>', pause:'<path d="M8 5v14M16 5v14"/>',
  edit:'<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4z"/>',
  copy:'<rect width="13" height="13" x="9" y="9" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  trash:'<path d="M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6M10 11v5M14 11v5"/>',
  plus:'<path d="M12 5v14M5 12h14"/>', more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  folder:'<path d="M3 6h6l2 2h10v11H3z"/>', search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>'
};
const icon = name => `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">${ICON_PATHS[name]||""}</svg>`;

async function api(path, options={}) {
  const response = await fetch(path, {headers:{"Content-Type":"application/json"}, ...options});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Yêu cầu thất bại");
  return payload;
}

function optionList(values, current="") {
  return values.map(v => `<option ${v===current?"selected":""}>${esc(v)}</option>`).join("");
}

async function load() {
  const [projectData, system, settings] = await Promise.all([
    api("/api/projects"), api("/api/system"), api("/api/settings")
  ]);
  state.projects = projectData.projects;
  state.settings = settings;
  $("#engine-status").textContent = system.ffmpeg_ready ? "Sẵn sàng" : "Chưa tìm thấy";
  $("#engine-dot").classList.toggle("ready", system.ffmpeg_ready);
  $("#disk-stat").textContent = `Ổ đĩa trống: ${system.disk_free_gb} GB`;
  $("#data-path").textContent = system.data_file;
  render();
  const params=new URLSearchParams(location.search);
  const editId=params.get("edit");
  if(editId){const item=state.projects.find(p=>p.id===editId);if(item)openProjectDialog(item)}
  else if(params.get("new")==="1")openProjectDialog();
}

function filteredProjects() {
  const status = state.statusFilter || $("#status-filter").value;
  const task = $("#task-filter").value;
  const priority = $("#priority-filter").value;
  const q = state.query.toLocaleLowerCase("vi");
  return state.projects.filter(p =>
    (!status || p.status===status) && (!task || p.task_type===task) &&
    (!priority || p.priority===priority) &&
    (!q || `${p.name} ${(p.input_paths||[]).join(" ")} ${p.output_path}`.toLocaleLowerCase("vi").includes(q))
  );
}

function render() {
  renderStats();
  renderProjects();
  renderSecondaryPages();
  renderWorkspace();
  const queued = state.projects.filter(p=>p.status==="Đang chờ").length;
  const running = state.projects.filter(p=>p.status==="Đang chạy").length;
  $("#queue-stat").textContent = `Hàng đợi: ${queued}`;
  $("#running-stat").textContent = `Đang chạy: ${running}`;
}

function renderStats() {
  const items = [
    ["Tổng dự án","", "#4b82f8"],
    ["Đang chờ","Đang chờ","#e7b93c"],
    ["Đang chạy","Đang chạy","#5790ff"],
    ["Hoàn thành","Hoàn thành","#36c982"],
    ["Cần xử lý","Lỗi","#ee5d68"]
  ];
  $("#stats").innerHTML = items.map(([label,status,color]) => {
    const count = status ? state.projects.filter(p=>p.status===status).length : state.projects.length;
    return `<button class="stat-card ${state.statusFilter===status?"active":""}" data-status="${esc(status)}">
      <i style="background:${color}"></i><small>${label}</small><strong>${count}</strong>
    </button>`;
  }).join("");
  $$(".stat-card").forEach(el => el.onclick = () => {
    state.statusFilter = el.dataset.status;
    $("#status-filter").value = "";
    render();
  });
}

function renderProjects() {
  const projects = filteredProjects();
  $("#empty-projects").classList.toggle("hidden", projects.length>0);
  $("#project-rows").innerHTML = projects.map(p => `
    <tr data-id="${p.id}" class="${state.selectedId===p.id?"selected":""}">
      <td><div class="project-cell project-link" onclick="openProjectWorkspace('${p.id}')"><span class="project-icon">▶</span><span>
        <strong>${esc(p.name)}</strong><small>${p.input_paths?.length||0} video • ${esc(p.output_path||"Chưa chọn đầu ra")}</small>
      </span></div></td>
      <td>${esc(p.task_type)}</td><td><span class="priority">${esc(p.priority)}</span></td>
      <td><span class="badge" style="color:${STATUS_COLORS[p.status]}">${esc(p.status)}</span></td>
      <td><div class="progress"><span style="width:${p.progress||0}%"></span></div><div class="progress-label">${p.progress||0}%</div></td>
      <td><div class="row-actions">
        <button class="action-icon primary" data-row-action="open" data-id="${p.id}" title="Mở dự án">${icon("open")}</button>
        <button class="action-icon success" data-row-action="run" data-id="${p.id}" title="Chạy dự án">${icon("play")}</button>
        <button class="action-icon" data-row-action="edit" data-id="${p.id}" title="Chỉnh sửa">${icon("edit")}</button>
        <button class="action-icon" data-row-action="duplicate" data-id="${p.id}" title="Nhân bản">${icon("copy")}</button>
        <button class="action-icon danger" data-row-action="delete" data-id="${p.id}" title="Xóa dự án">${icon("trash")}</button>
      </div></td>
    </tr>`).join("");
  $$("#project-rows tr").forEach(row => row.onclick = event => {
    if (event.target.closest(".row-actions")) return;
    state.selectedId = row.dataset.id; openProjectWorkspace(row.dataset.id);
  });
  $$("[data-row-action]").forEach(button => button.onclick = event => {
    event.stopPropagation();
    const project=state.projects.find(item=>item.id===button.dataset.id);
    if(!project)return;
    if(button.dataset.rowAction==="open")openProjectWorkspace(project.id);
    else projectAction(button.dataset.rowAction,project);
  });
  renderDetail();
}

function renderDetail() {
  const p = state.projects.find(item=>item.id===state.selectedId);
  if (!p) {
    $("#project-detail").innerHTML = `<div class="detail-empty"><div><div class="empty-icon">◎</div><h3>Chi tiết dự án</h3><p>Chọn một dự án trong danh sách.</p></div></div>`;
    return;
  }
  const logs = (p.logs||[]).slice(-8).reverse();
  $("#project-detail").innerHTML = `
    <span class="eyebrow">CHI TIẾT DỰ ÁN</span><h2>${esc(p.name)}</h2>
    <span class="badge" style="color:${STATUS_COLORS[p.status]}">${esc(p.status)}</span>
    <div class="detail-meta">
      <div class="meta-row"><span>Tác vụ</span><b>${esc(p.task_type)}</b></div>
      <div class="meta-row"><span>Nguồn</span><b>${p.input_paths?.length||0} video</b></div>
      <div class="meta-row"><span>Đầu ra</span><b>${esc(p.output_path||"Chưa chọn")}</b></div>
      <div class="meta-row"><span>Ưu tiên</span><b>${esc(p.priority)}</b></div>
      <div class="meta-row"><span>Ngày tạo</span><b>${formatDate(p.created_at)}</b></div>
    </div>
    <div class="progress"><span style="width:${p.progress||0}%"></span></div><div class="progress-label">${p.progress||0}% hoàn thành</div>
    <div class="detail-actions">
      <button class="button primary" data-action="run">Chạy / Tiếp tục</button>
      <button class="button" data-action="pause">Tạm dừng</button>
      <button class="button" data-action="edit">Chỉnh sửa</button>
      <button class="button" data-action="duplicate">Nhân bản</button>
    </div>
    <span class="eyebrow">NHẬT KÝ GẦN ĐÂY</span>
    <div class="log-box">${logs.length ? logs.map(l=>`<div>${esc(l.time?.slice(11)||"")} [${esc(l.level)}] ${esc(l.message)}</div>`).join("") : "Chưa có nhật ký."}</div>`;
  $$("[data-action]", $("#project-detail")).forEach(button => button.onclick = () => projectAction(button.dataset.action,p));
}

function showActionMenu(button) {
  const p = state.projects.find(item=>item.id===state.selectedId);
  if (!p) return;
  const action = prompt("Chọn thao tác:\n1. Chạy\n2. Tạm dừng\n3. Chỉnh sửa\n4. Nhân bản\n5. Xóa");
  const map = {"1":"run","2":"pause","3":"edit","4":"duplicate","5":"delete"};
  if (map[action]) projectAction(map[action],p);
}

async function projectAction(action,p) {
  try {
    if (action==="run") await updateProject(p.id,{status:"Đang chờ"});
    if (action==="pause") await updateProject(p.id,{status:"Tạm dừng"});
    if (action==="edit") openProjectDialog(p);
    if (action==="duplicate") { await api(`/api/projects/${p.id}/duplicate`,{method:"POST"}); await reloadProjects(); toast("Đã nhân bản dự án"); }
    if (action==="delete" && confirm(`Xóa dự án “${p.name}”?`)) {
      await api(`/api/projects/${p.id}`,{method:"DELETE"}); state.selectedId=null; await reloadProjects(); toast("Đã xóa dự án");
    }
  } catch(error) { toast(error.message); }
}

async function updateProject(id,changes) {
  await api(`/api/projects/${id}`,{method:"PATCH",body:JSON.stringify(changes)});
  await reloadProjects();
}

async function reloadProjects() {
  state.projects = (await api("/api/projects")).projects;
  render();
  const params=new URLSearchParams(location.search);
  const editId=params.get("edit");
  if(editId){const item=state.projects.find(p=>p.id===editId);if(item)openProjectDialog(item)}
  else if(params.get("new")==="1")openProjectDialog();
}

function openProjectWorkspace(id) {
  window.location.href=`/project.html?id=${encodeURIComponent(id)}`;
}

function renderWorkspace() {
  const root=$("#page-workspace");
  const p=state.projects.find(project=>project.id===state.selectedId);
  if(!p){root.innerHTML=emptyLine("Không tìm thấy dự án");return}
  const files=(p.input_paths||[]).map((path,index)=>`
    <button class="media-asset ${index===0?"selected":""}" title="${esc(path)}">
      <span class="asset-thumb"><b>▶</b><small>${String(index+1).padStart(2,"0")}</small></span>
      <span class="asset-info"><strong>${esc(path.split(/[\\/]/).pop())}</strong><small>Video nguồn • Sẵn sàng</small></span>
      <span class="asset-more">•••</span>
    </button>`).join("");
  const clips=(p.input_paths||[]).map((path,index)=>`
    <div class="timeline-clip" style="--clip:${Math.max(120,220-index*12)}px">
      <span class="clip-pattern"></span><b>${esc(path.split(/[\\/]/).pop())}</b>
    </div>`).join("");
  root.innerHTML=`
    <div class="studio-editor">
      <header class="editor-topbar">
        <div class="editor-project-title"><button class="editor-back" onclick="navigate('projects')">‹</button><div><small>DỰ ÁN</small><strong>${esc(p.name)}</strong></div></div>
        <div class="editor-history"><button title="Hoàn tác">↶</button><button title="Làm lại">↷</button><span>Đã lưu tự động</span></div>
        <div class="editor-export"><span class="badge" style="color:${STATUS_COLORS[p.status]}">${esc(p.status)}</span><button class="button" onclick="projectActionById('edit','${p.id}')">Cài đặt dự án</button><button class="button primary" onclick="selectEditorTool('concat')">Xuất video</button></div>
      </header>
      <div class="editor-main">
        <nav class="editor-rail">
          <button class="active" data-editor-tool="media" onclick="selectEditorTool('media')"><span>▦</span>Media</button>
          <button data-editor-tool="concat" onclick="selectEditorTool('concat')"><span>⛓</span>Nối</button>
          <button data-editor-tool="split" onclick="selectEditorTool('split')"><span>✂</span>Băm</button>
          <button data-editor-tool="zoom" onclick="selectEditorTool('zoom')"><span>⌕</span>Zoom</button>
          <button data-editor-tool="audio" onclick="selectEditorTool('audio')"><span>♫</span>Âm thanh</button>
          <button data-editor-tool="effects" onclick="selectEditorTool('effects')"><span>✦</span>Hiệu ứng</button>
        </nav>
        <aside class="media-browser">
          <div class="browser-head"><div><small>THƯ VIỆN</small><h3>Media dự án</h3></div><button onclick="projectActionById('edit','${p.id}')">＋</button></div>
          <div class="browser-tabs"><button class="active">Cục bộ</button><button>Đã dùng</button></div>
          <label class="asset-search">⌕ <input placeholder="Tìm video..."></label>
          <div class="asset-grid">${files||`<div class="asset-empty"><span>⇧</span><b>Thêm video nguồn</b><small>Mở Cài đặt dự án để thêm file</small></div>`}</div>
        </aside>
        <main class="preview-workspace">
          <div class="preview-toolbar"><button>100%⌄</button><span></span><button>⌗ An toàn vùng hiển thị</button><button>▣ Toàn màn hình</button></div>
          <div class="video-stage"><div class="video-canvas"><div class="canvas-brand">FAST VIDEO STUDIO</div><div class="play-orb">▶</div><p>${esc(p.name)}</p><small>${p.input_paths?.length||0} video • Preview dự án</small></div></div>
          <div class="transport"><span class="timecode">00:00:00:00</span><div><button>◀◀</button><button>◀</button><button class="play">▶</button><button>▶</button><button>▶▶</button></div><span class="timecode">--:--:--:--</span></div>
        </main>
        <aside class="inspector">
          <div class="inspector-tabs"><button class="active">Thuộc tính</button><button>Điều chỉnh</button></div>
          <section class="inspector-panel active" data-tool-panel="media"><span class="eyebrow">THÔNG TIN DỰ ÁN</span><h3>${esc(p.name)}</h3><div class="property-list"><label>Video nguồn <b>${p.input_paths?.length||0} file</b></label><label>Đầu ra <b>${esc(p.output_path||"Chưa chọn")}</b></label><label>Ưu tiên <b>${esc(p.priority)}</b></label><label>Tiến trình <b>${p.progress||0}%</b></label></div><button class="button full" onclick="projectActionById('edit','${p.id}')">Chỉnh file và đầu ra</button></section>
          <section class="inspector-panel" data-tool-panel="concat"><span class="eyebrow">NỐI VIDEO DÀI</span><h3>Thiết lập xuất</h3><label>Định dạng<select id="concat-format"><option value="mkv">MKV — khuyên dùng</option><option value="mp4">MP4</option></select></label><label class="check"><input id="concat-safe" type="checkbox"> Sửa timestamp an toàn</label><div class="info-callout">Stream copy giúp nối nhanh mà không giảm chất lượng.</div><button class="button primary full" onclick="runWorkspaceTool('concat')">Nối ${p.input_paths?.length||0} video</button></section>
          <section class="inspector-panel" data-tool-panel="split"><span class="eyebrow">BĂM NHỎ VIDEO</span><h3>Chia theo thời lượng</h3><label>Thời lượng mỗi đoạn<div class="input-unit"><input id="split-seconds" type="number" min="1" value="60"><span>giây</span></div></label><label class="check"><input id="split-accurate" type="checkbox"> Chính xác từng khung hình</label><button class="button primary full" onclick="runWorkspaceTool('split')">Bắt đầu băm nhỏ</button></section>
          <section class="inspector-panel" data-tool-panel="zoom"><span class="eyebrow">BIẾN ĐỔI</span><h3>Phóng to / thu nhỏ</h3><label>Thu phóng<div class="range-row"><input id="zoom-range" type="range" min="25" max="300" value="110" oninput="document.querySelector('#zoom-percent').value=this.value"><input id="zoom-percent" type="number" min="25" max="300" value="110"></div></label><div class="xy-grid"><label>Vị trí X<input id="zoom-x" type="number" value="0"></label><label>Vị trí Y<input id="zoom-y" type="number" value="0"></label></div><button class="button primary full" onclick="runWorkspaceTool('zoom')">Áp dụng biến đổi</button></section>
          <section class="inspector-panel" data-tool-panel="audio"><span class="eyebrow">ÂM THANH</span><h3>Tách nhạc nền</h3><label>Phương pháp<select id="audio-mode"><option value="audio">Trích xuất audio</option><option value="audio_ai">AI tách vocal / nhạc nền</option></select></label><label>Định dạng<select id="audio-format"><option>mp3</option><option>wav</option><option>aac</option></select></label><div class="info-callout">AI cần cài Demucs trên máy. Trích xuất thường dùng FFmpeg.</div><button class="button primary full" onclick="runWorkspaceTool('audio')">Bắt đầu tách âm thanh</button></section>
          <section class="inspector-panel" data-tool-panel="effects"><span class="eyebrow">HIỆU ỨNG</span><h3>Hiệu ứng nhanh</h3><div class="effect-grid"><button>Fade</button><button>Blur</button><button>Tăng sáng</button><button>Đen trắng</button><button>Lật ngang</button><button>Xoay 90°</button></div><div class="info-callout">Chọn dự án “Thêm hiệu ứng” để dùng cấu hình effect đầy đủ.</div></section>
        </aside>
      </div>
      <section class="timeline-editor">
        <div class="timeline-tools"><button>⌁ Chọn</button><button>✂ Tách</button><button>▱ Xóa</button><span></span><button>−</button><input type="range" min="1" max="100" value="45"><button>＋</button></div>
        <div class="timeline-body"><div class="track-labels"><div><b>V1</b><small>Video chính</small></div><div><b>A1</b><small>Âm thanh</small></div></div><div class="tracks"><div class="timeline-ruler"><span>00:00</span><span>00:10</span><span>00:20</span><span>00:30</span><span>00:40</span></div><div class="playhead"></div><div class="video-track">${clips}</div><div class="audio-track">${(p.input_paths||[]).map((_,i)=>`<div class="audio-wave" style="--clip:${Math.max(120,220-i*12)}px"></div>`).join("")}</div></div></div>
      </section>
      <aside class="floating-job-log"><button onclick="this.parentElement.classList.toggle('open')">▤ <span>${p.progress||0}%</span></button><div><strong>Nhật ký xử lý</strong><div class="log-box">${(p.logs||[]).slice(-30).reverse().map(l=>`<p>${esc(l.time?.slice(11)||"")} [${esc(l.level)}] ${esc(l.message)}</p>`).join("")}</div></div></aside>
    </div>`;
}

function selectEditorTool(tool) {
  $$("[data-editor-tool]").forEach(button=>button.classList.toggle("active",button.dataset.editorTool===tool));
  $$("[data-tool-panel]").forEach(panel=>panel.classList.toggle("active",panel.dataset.toolPanel===tool));
}
async function runWorkspaceTool(operation) {
  const p=state.projects.find(project=>project.id===state.selectedId);
  if(!p)return;
  let actual=operation,options={};
  if(operation==="concat") options={safe_mode:$("#concat-safe").checked,format:$("#concat-format").value};
  if(operation==="split") options={segment_seconds:+$("#split-seconds").value,accurate:$("#split-accurate").checked};
  if(operation==="zoom") options={zoom_percent:+$("#zoom-percent").value,pos_x:+$("#zoom-x").value,pos_y:+$("#zoom-y").value};
  if(operation==="audio"){actual=$("#audio-mode").value;options={audio_format:$("#audio-format").value}}
  try{
    const result=await api(`/api/projects/${p.id}/run`,{method:"POST",body:JSON.stringify({operation:actual,options})});
    toast(result.message);await reloadProjects();navigate("workspace");
  }catch(error){toast(error.message)}
}
function renderSecondaryPages() {
  $("#page-projects").innerHTML = pageTemplate("Dự án","Thiết lập và quản lý toàn bộ workspace video",[
    ["＋","Tạo dự án","Chỉ cần đặt tên, sau đó cấu hình trong workspace."],
    ["✎","Chỉnh cấu hình","Mỗi tác vụ có biểu mẫu thiết lập riêng."],
    ["⧉","Nhân bản","Thử cấu hình mới mà không ảnh hưởng dự án gốc."]
  ], `<div class="project-toolbar">
        <button class="button primary" onclick="openNewProject()">＋ Tạo dự án mới</button>
        <button class="button" onclick="runAllProjects()">▶ Chạy tất cả</button>
        <button class="button" onclick="pauseAllProjects()">Ⅱ Tạm dừng tất cả</button>
      </div>
      <div class="wide-panel panel project-page-list">${projectCards()}</div>`);
  const queue = state.projects.filter(p=>["Đang chờ","Đang chạy","Tạm dừng"].includes(p.status));
  $("#page-queue").innerHTML = pageTemplate("Hàng đợi","Điều phối FFmpeg theo trạng thái và ưu tiên",[
    ["▶","Chạy tiếp","Tự động chọn dự án ưu tiên cao nhất."],["Ⅱ","Tạm dừng","Giữ nguyên cấu hình và tiến trình."],["↕","Sắp xếp","Ưu tiên Khẩn cấp, Cao, Bình thường, Thấp."]
  ], `<div class="wide-panel panel">${queue.length?queue.map(queueRow).join(""):emptyLine("Hàng đợi đang trống")}</div>`);
  const outputs = state.projects.filter(p=>p.status==="Hoàn thành"||p.output_path);
  $("#page-outputs").innerHTML = pageTemplate("File đầu ra","Kết quả video và thư mục lưu trữ",[
    ["▤","Kết quả","Theo dõi output theo từng dự án."],["◉","Dung lượng","Cảnh báo dung lượng ổ đĩa trước khi chạy."],["↗","Truy cập nhanh","Sao chép đường dẫn thư mục đầu ra."]
  ], `<div class="wide-panel panel">${outputs.length?outputs.map(outputRow).join(""):emptyLine("Chưa có file đầu ra")}</div>`);
  const logs = state.projects.flatMap(p=>(p.logs||[]).map(l=>({...l,project:p.name}))).sort((a,b)=>(b.time||"").localeCompare(a.time||""));
  $("#page-history").innerHTML = pageTemplate("Lịch sử","Nhật ký hoạt động của tất cả dự án",[
    ["✓","Hoàn thành","Theo dõi các lần xử lý thành công."],["!","Cảnh báo","Phát hiện sớm lỗi nguồn và đầu ra."],["↻","Chạy lại","Mở dự án lỗi để sửa cấu hình."]
  ], `<div class="wide-panel panel">${logs.length?logs.slice(0,50).map(historyRow).join(""):emptyLine("Chưa có lịch sử")}</div>`);
  renderSettings();
}

function pageTemplate(title,subtitle,features,body) {
  return `<div class="page-hero"><div><span class="eyebrow">FAST VIDEO STUDIO</span><h2>${title}</h2><p>${subtitle}</p></div></div>
    <div class="feature-grid">${features.map(([icon,name,desc])=>`<article class="feature-card"><span>${icon}</span><h3>${name}</h3><p>${desc}</p></article>`).join("")}</div>${body}`;
}
function projectCards(){
  return state.projects.length ? state.projects.map(p=>`
    <article class="project-page-row">
      <div class="project-cell project-link" onclick="openProjectWorkspace('${p.id}')"><span class="project-icon">▶</span><span>
        <strong>${esc(p.name)}</strong><small>${p.input_paths?.length||0} video • ${esc(p.task_type)}</small>
      </span></div>
      <span class="badge" style="color:${STATUS_COLORS[p.status]}">${esc(p.status)}</span>
      <span class="priority">${esc(p.priority)}</span>
      <div class="progress-cell"><div class="progress"><span style="width:${p.progress||0}%"></span></div><small>${p.progress||0}%</small></div>
      <div class="project-row-actions">
        <button class="action-icon primary" onclick="openProjectWorkspace('${p.id}')" title="Mở dự án">${icon("open")}</button>
        <button class="action-icon success" onclick="projectActionById('run','${p.id}')" title="Chạy">${icon("play")}</button>
        <button class="action-icon" onclick="projectActionById('edit','${p.id}')" title="Chỉnh sửa">${icon("edit")}</button>
        <button class="action-icon" onclick="projectActionById('duplicate','${p.id}')" title="Nhân bản">${icon("copy")}</button>
        <button class="action-icon danger" onclick="projectActionById('delete','${p.id}')" title="Xóa">${icon("trash")}</button>
      </div>
    </article>`).join("") : emptyLine("Chưa có dự án");
}
function queueRow(p){return `<div class="queue-row"><div><strong>${esc(p.name)}</strong><small>${esc(p.task_type)}</small></div><span class="badge" style="color:${STATUS_COLORS[p.status]}">${esc(p.status)}</span><span>${esc(p.priority)}</span><span>${p.progress||0}%</span></div>`}
function outputRow(p){return `<div class="output-row"><div><strong>${esc(p.name)}</strong><small>${esc(p.output_path||"Chưa chọn")}</small></div><span>${esc(p.task_type)}</span><span>${p.progress||0}%</span><button class="button" onclick="copyPath('${esc(p.output_path||"")}')">Sao chép</button></div>`}
function historyRow(l){return `<div class="history-row"><div><strong>${esc(l.project)}</strong><small>${esc(l.message)}</small></div><span>${esc(l.level)}</span><span>${formatDate(l.time)}</span><span></span></div>`}
function emptyLine(text){return `<div class="empty"><div class="empty-icon">◎</div><h3>${text}</h3></div>`}

function renderSettings() {
  const s=state.settings;
  $("#page-settings").innerHTML = `<div class="page-hero"><div><span class="eyebrow">LOCAL CONFIG</span><h2>Cài đặt</h2><p>Hiệu năng, hàng đợi và tăng tốc phần cứng</p></div><button id="save-settings" class="button primary">Lưu cài đặt</button></div>
  <div class="settings-grid">
    <section class="panel settings-card"><h3>Hiệu năng FFmpeg</h3>
      <label>Số dự án chạy đồng thời <input id="max-jobs" type="number" min="1" max="8" value="${s.max_concurrent_jobs||2}"></label>
      <label>Số luồng mỗi dự án <input id="ffmpeg-threads" type="number" min="1" max="32" value="${s.ffmpeg_threads||4}"></label>
      <label>Cho phép sử dụng GPU <input id="use-gpu" class="switch" type="checkbox" ${s.use_gpu?"checked":""}></label>
    </section>
    <section class="panel settings-card"><h3>Hành vi hàng đợi</h3>
      <label>Tự chạy dự án tiếp theo <input id="auto-next" class="switch" type="checkbox" ${s.auto_start_next?"checked":""}></label>
      <p style="color:var(--muted);margin-top:22px">Dữ liệu được lưu cục bộ trong file JSON bằng cơ chế ghi atomic.</p>
    </section>
  </div>`;
  $("#save-settings").onclick=saveSettings;
}

async function saveSettings(){
  state.settings=await api("/api/settings",{method:"PATCH",body:JSON.stringify({
    max_concurrent_jobs:+$("#max-jobs").value,ffmpeg_threads:+$("#ffmpeg-threads").value,
    use_gpu:$("#use-gpu").checked,auto_start_next:$("#auto-next").checked
  })}); toast("Đã lưu cài đặt");
}

function navigate(page) {
  state.page=page;
  $$(".page").forEach(el=>el.classList.toggle("active",el.id===`page-${page}`));
  $$(".nav-item").forEach(el=>el.classList.toggle("active",el.dataset.page===page));
  $("#page-title").textContent=PAGE_TITLES[page];
  $(".sidebar").classList.remove("open");
}

function openProjectDialog(project=null) {
  const form=$("#project-form"); form.reset();
  const creating=!project;
  form.classList.toggle("simple-create",creating);
  $("#project-dialog").classList.toggle("simple-dialog",creating);
  $("#dialog-title").textContent=creating?"Đặt tên dự án":"Chỉnh sửa dự án";
  $("#project-dialog .eyebrow").textContent=creating?"DỰ ÁN MỚI":"CẤU HÌNH DỰ ÁN";
  $("#project-submit").textContent=creating?"Tạo dự án":"Lưu thay đổi";
  form.elements.id.value=project?.id||"";
  form.elements.name.value=project?.name||"";
  form.elements.task_type.innerHTML=optionList(TASKS,project?.task_type||TASKS[0]);
  form.elements.priority.innerHTML=optionList(PRIORITIES,project?.priority||"Bình thường");
  form.elements.input_paths.value=(project?.input_paths||[]).join("\n");
  form.elements.output_path.value=project?.output_path||"";
  renderTaskSettings(project?.task_type||TASKS[0],project?.settings||{});
  $("#project-dialog").showModal();
}

function renderTaskSettings(task,settings={}) {
  const map={
    "Nối video":`<h3>Thiết lập nối video</h3><div class="setting-fields"><label>Định dạng<select name="format">${optionList(["MKV","MP4"],settings.format)}</select></label><label>Chế độ<select name="concat_mode">${optionList(["Siêu nhanh","An toàn sửa timestamp"],settings.concat_mode)}</select></label><label>Kiểm tra tương thích<select name="analyze">${optionList(["Có","Không"],settings.analyze)}</select></label></div>`,
    "Chuẩn hóa video":`<h3>Thiết lập chuẩn hóa</h3><div class="setting-fields"><label>Độ phân giải<select name="resolution">${optionList(["1920 x 1080","1280 x 720","1080 x 1920","Giữ nguyên"],settings.resolution)}</select></label><label>FPS<select name="fps">${optionList(["24","25","30","50","60","Giữ nguyên"],settings.fps)}</select></label><label>Codec<select name="codec">${optionList(["H.264","H.265","AV1"],settings.codec)}</select></label></div>`,
    "Chia nhỏ video":`<h3>Thiết lập băm nhỏ</h3><div class="setting-fields"><label>Chia theo<select name="split_mode">${optionList(["Thời gian","Số lượng","Mốc tùy chọn"],settings.split_mode)}</select></label><label>Giây mỗi đoạn<input name="segment_seconds" type="number" value="${settings.segment_seconds||60}"></label><label>Số đoạn<input name="part_count" type="number" value="${settings.part_count||10}"></label></div>`,
    "Phóng to/thu nhỏ":`<h3>Thiết lập zoom / crop / pad</h3><div class="setting-fields"><label>Zoom (%)<input name="zoom" type="number" min="25" max="300" value="${settings.zoom||110}"></label><label>Vị trí X<input name="pos_x" type="number" value="${settings.pos_x||0}"></label><label>Vị trí Y<input name="pos_y" type="number" value="${settings.pos_y||0}"></label></div>`,
    "Thêm hiệu ứng":`<h3>Thiết lập hiệu ứng</h3><div class="setting-fields"><label>Hiệu ứng<select name="effect">${optionList(["Fade in/out","Blur","Tăng sáng","Tương phản","Làm nét","Đen trắng","Lật ngang","Xoay 90°"],settings.effect)}</select></label><label>Cường độ<input name="intensity" type="range" min="1" max="100" value="${settings.intensity||50}"></label><label>Tốc độ<select name="speed">${optionList(["1.0x","1.25x","1.5x","2.0x"],settings.speed)}</select></label></div>`
  };
  $("#task-settings").innerHTML=map[task]||"";
}

function taskSettingsFromForm(form) {
  const values={};
  $$("input,select",$("#task-settings")).forEach(input=>values[input.name]=input.type==="number"?+input.value:input.value);
  return values;
}

async function submitProject(event) {
  event.preventDefault();
  const form=event.currentTarget,id=form.elements.id.value;
  const payload=id ? {
    name:form.elements.name.value,task_type:form.elements.task_type.value,
    priority:form.elements.priority.value,input_paths:form.elements.input_paths.value.split("\n").map(v=>v.trim()).filter(Boolean),
    output_path:form.elements.output_path.value,settings:taskSettingsFromForm(form)
  } : {name:form.elements.name.value};
  try {
    if(id) await api(`/api/projects/${id}`,{method:"PATCH",body:JSON.stringify(payload)});
    else {
      const created=await api("/api/projects",{method:"POST",body:JSON.stringify(payload)});
      $("#project-dialog").close();
      window.location.href=`/project.html?id=${encodeURIComponent(created.id)}`;
      return;
    }
    $("#project-dialog").close(); await reloadProjects(); toast("Đã cập nhật dự án");
  } catch(error){toast(error.message)}
}

function formatDate(value){return value?new Date(value).toLocaleString("vi-VN"):"--"}
function toast(message){const el=document.createElement("div");el.className="toast";el.textContent=message;$("#toast-container").append(el);setTimeout(()=>el.remove(),2600)}
window.openProjectWorkspace=openProjectWorkspace;
window.selectEditorTool=selectEditorTool;
window.runWorkspaceTool=runWorkspaceTool;
window.editById=id=>openProjectDialog(state.projects.find(p=>p.id===id));
window.copyPath=async path=>{if(path){await navigator.clipboard.writeText(path);toast("Đã sao chép đường dẫn")}};
window.openNewProject=()=>openProjectDialog();
window.projectActionById=(action,id)=>{
  const project=state.projects.find(p=>p.id===id);
  if(project) projectAction(action,project);
};
window.runAllProjects=async()=>{
  const runnable=state.projects.filter(p=>["Bản nháp","Chưa chạy","Tạm dừng","Lỗi"].includes(p.status));
  await Promise.all(runnable.map(p=>api(`/api/projects/${p.id}`,{method:"PATCH",body:JSON.stringify({status:"Đang chờ"})})));
  await reloadProjects(); toast(`Đã đưa ${runnable.length} dự án vào hàng đợi`);
};
window.pauseAllProjects=async()=>{
  const active=state.projects.filter(p=>["Đang chạy","Đang chờ"].includes(p.status));
  await Promise.all(active.map(p=>api(`/api/projects/${p.id}`,{method:"PATCH",body:JSON.stringify({status:"Tạm dừng"})})));
  await reloadProjects(); toast(`Đã tạm dừng ${active.length} dự án`);
};

function init() {
  $("#status-filter").innerHTML=`<option value="">Tất cả trạng thái</option>${STATUSES.map(v=>`<option>${v}</option>`)}`;
  $("#task-filter").innerHTML=`<option value="">Tất cả tác vụ</option>${TASKS.map(v=>`<option>${v}</option>`)}`;
  $("#priority-filter").innerHTML=`<option value="">Tất cả ưu tiên</option>${PRIORITIES.map(v=>`<option>${v}</option>`)}`;
  $$(".nav-item").forEach(el=>el.onclick=()=>navigate(el.dataset.page));
  $("#new-project").onclick=()=>openProjectDialog(); $$(".create-trigger").forEach(el=>el.onclick=()=>openProjectDialog());
  $$(".close-dialog").forEach(el=>el.onclick=()=>$("#project-dialog").close());
  $("#project-form").onsubmit=submitProject;
  $("#project-form").elements.task_type.onchange=event=>renderTaskSettings(event.target.value);
  $("#global-search").oninput=event=>{state.query=event.target.value;renderProjects()};
  ["status-filter","task-filter","priority-filter"].forEach(id=>$("#"+id).onchange=()=>{if(id==="status-filter")state.statusFilter="";renderProjects()});
  $("#mobile-menu").onclick=()=>$(".sidebar").classList.toggle("open");
  load().catch(error=>toast(error.message));
  setInterval(()=>{if(state.projects.some(p=>p.status==="Đang chạy"))reloadProjects()},3000);
}
document.addEventListener("DOMContentLoaded",init);
