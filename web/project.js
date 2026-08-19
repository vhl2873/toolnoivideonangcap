const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => (
  {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]
));
const STATUS_COLORS = {
  "Bản nháp":"#7c8ba0","Chưa chạy":"#7c8ba0","Đang chờ":"#e7b93c",
  "Đang chạy":"#5790ff","Tạm dừng":"#f58b45","Hoàn thành":"#36c982",
  "Lỗi":"#ee5d68","Đã hủy":"#58687b"
};

const projectId = new URLSearchParams(location.search).get("id");
let project = null;
let batchResults = [];
let selectedMediaIndex = 0;
let selectedTimelineIndex = -1;
let previewAnimationFrame = 0;
let timelineDurations = [];
const mediaDurationCache = new Map();

function stageText(p){
  const parts=[];
  if(p.job_stage)parts.push(p.job_stage);
  if(p.current_step)parts.push(p.current_step);
  return parts.join(" • ") || "Chưa có tiến trình chi tiết";
}
function videoProgressText(p){
  const total=Number(p.total_videos||0);
  const done=Number(p.processed_videos||0);
  const current=p.current_video||"";
  if(!total)return current || "Chưa có video";
  return `${done}/${total} video${current?` • ${current}`:""}`;
}

async function api(path, options={}) {
  const response = await fetch(path, {
    headers:{"Content-Type":"application/json"}, ...options
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || payload.message || "Yêu cầu thất bại");
  return payload;
}

async function loadProject() {
  const payload = await api("/api/projects");
  project = payload.projects.find(item => item.id === projectId);
  if (!project) {
    $("#project-editor-root").innerHTML = `
      <div class="standalone-error"><span class="brand-mark">FV</span>
      <h1>Không tìm thấy dự án</h1><p>Dự án đã bị xóa hoặc đường dẫn không hợp lệ.</p>
      <a class="button primary" href="/">Quay lại Dashboard</a></div>`;
    return;
  }
  document.title = `${project.name} — Fast Video Studio`;
  try { batchResults = (await api(`/api/projects/${project.id}/batch-results`)).results || []; }
  catch { batchResults = []; }
  const timelinePaths = project.settings?.timeline_paths || [];
  if(timelinePaths.length){selectedTimelineIndex=0;selectedMediaIndex=project.input_paths.indexOf(timelinePaths[0])}
  else {const browserPlayable = project.input_paths?.findIndex(path => /\.(mp4|m4v|webm|ogv)$/i.test(path));if(browserPlayable>=0)selectedMediaIndex=browserPlayable}
  render();
}

function render() {
  const p = project;
  selectedMediaIndex = Math.min(selectedMediaIndex, Math.max(0, (p.input_paths || []).length - 1));
  selectedTimelineIndex = Math.min(selectedTimelineIndex, Math.max(-1, (p.settings?.timeline_paths || []).length - 1));
  const files = (p.input_paths || []).map((path,index) => `
    <button class="media-asset ${index===selectedMediaIndex?"selected":""}" draggable="true" data-media-index="${index}" onclick="selectMedia(${index})" title="${esc(path)}">
      <span class="asset-thumb"><b>▶</b><small>${String(index+1).padStart(2,"0")}</small></span>
      <span class="asset-info"><strong>${esc(fileName(path))}</strong><small>Video nguồn • Sẵn sàng</small></span>
      <span class="asset-more" onclick="event.stopPropagation();removeMedia(${index})" title="Xóa khỏi dự án">×</span>
    </button>`).join("");
  const timelinePaths = p.settings?.timeline_paths || [];
  const activeTimelinePath=timelinePaths[selectedTimelineIndex]||timelinePaths[0];
  const activeTimelineMediaIndex=(p.input_paths||[]).indexOf(activeTimelinePath);
  const clips = timelinePaths.map((path,index) => {
    const mediaIndex=(p.input_paths||[]).indexOf(path);
    return `<div class="timeline-clip ${index===selectedTimelineIndex?"selected":""}" draggable="true" data-timeline-index="${index}" style="--clip:${Math.max(120,220-index*12)}px">
      <span class="clip-pattern"></span><b>${esc(fileName(path))}</b>
    </div>`;
  }).join("");
  const logs = (p.logs || []).slice(-40).reverse().map(log =>
    `<p>${esc(log.time?.slice(11)||"")} [${esc(log.level)}] ${esc(log.message)}</p>`
  ).join("");

  $("#project-editor-root").className = "";
  $("#project-editor-root").innerHTML = `
    <input id="video-picker" type="file" accept="video/*,.mkv,.mov,.m4v,.avi,.ts,.mts,.m2ts,.webm" multiple hidden>
    <div class="studio-editor standalone-editor">
      <header class="editor-topbar">
        <div class="editor-project-title">
          <a class="editor-back" href="/">‹</a>
          <div><small>DỰ ÁN VIDEO</small><strong>${esc(p.name)}</strong></div>
        </div>
        <div class="editor-history">
          <button title="Hoàn tác">↶</button><button title="Làm lại">↷</button>
          <span>Đã lưu tự động vào JSON</span>
        </div>
        <div class="editor-export">
          <span class="badge" style="color:${STATUS_COLORS[p.status]}" title="${esc(stageText(p))}">${esc(p.status)}</span>
          <span class="job-chip" title="${esc(videoProgressText(p))}">${p.progress||0}% • ${esc(p.current_step||"Sẵn sàng")}</span>
          <button class="button" onclick="openOutputFolder()">Mở output</button>
          <button class="button" onclick="openFinalVideo()">Mở final</button>
          <button class="button" onclick="editProject()">Cài đặt dự án</button>
          ${p.status === "Đang chạy" ? `<button class="button danger" onclick="cancelJob()">Dừng / Hủy</button>` : ""}
          <button class="button primary" onclick="selectTool('concat')">Xuất video</button>
        </div>
      </header>
      <div class="editor-main">
        <nav class="editor-rail">
          <button class="active" data-editor-tool="media" onclick="selectTool('media')"><span>▦</span>Media</button>
          <button data-editor-tool="concat" onclick="selectTool('concat')"><span>⛓</span>Nối</button>
          <button data-editor-tool="split" onclick="selectTool('split')"><span>✂</span>Băm</button>
          <button data-editor-tool="zoom" onclick="selectTool('zoom')"><span>⌕</span>Zoom</button>
          <button data-editor-tool="audio" onclick="selectTool('audio')"><span>♫</span>Âm thanh</button>
          <button data-editor-tool="batch" onclick="selectTool('batch')"><span>⚡</span>Batch AI</button>
          <button data-editor-tool="effects" onclick="selectTool('effects')"><span>✦</span>Hiệu ứng</button>
        </nav>
        <aside class="media-browser">
          <div class="browser-head">
            <div><small>THƯ VIỆN</small><h3>Media dự án</h3></div>
            <button onclick="chooseVideos()" title="Import nhiều video">＋</button>
          </div>
          <div class="browser-tabs"><button class="active">Cục bộ</button><button>Đã dùng</button></div>
          <label class="asset-search">⌕ <input id="asset-query" placeholder="Tìm video..."></label>
          <div class="asset-grid">${files || `
            <button class="asset-empty import-empty" onclick="chooseVideos()"><span>⇧</span><b>Import nhiều video</b>
            <small>Chọn file hoặc kéo thả vào đây</small></button>`}
          <div id="upload-progress" class="upload-progress hidden"><div><span></span></div><small>Đang import...</small></div></div>
        </aside>
        <main class="preview-workspace">
          <div class="preview-toolbar"><button>100%⌄</button><span></span>
            <button>⌗ Vùng hiển thị</button><button>▣ Toàn màn hình</button></div>
          <div class="video-stage">
            <div class="video-canvas ${timelinePaths.length ? "has-video" : ""}">
              <div class="canvas-brand">FAST VIDEO STUDIO</div>
              ${timelinePaths.length ? `<video id="preview-video" playsinline preload="metadata" src="/api/projects/${encodeURIComponent(p.id)}/media/${activeTimelineMediaIndex}"></video><div id="preview-error" class="preview-error hidden"></div>` : `<button class="preview-empty"><span>↓</span><b>Timeline đang trống</b><small>Kéo video từ Media xuống V1 để xem trước</small></button>`}
            </div>
          </div>
          <div class="transport"><span id="preview-current" class="timecode">00:00</span>
            <div><button onclick="stepMedia(-1)" title="Video trước">◀◀</button><button onclick="seekPreview(-5)" title="Lùi 5 giây">◀</button><button id="preview-play" class="play" onclick="togglePreview()" title="Phát / tạm dừng">▶</button><button onclick="seekPreview(5)" title="Tiến 5 giây">▶</button><button onclick="stepMedia(1)" title="Video sau">▶▶</button></div>
            <span id="preview-total" class="timecode">00:00</span>
          </div>
        </main>
        <aside class="inspector">
          <div class="inspector-tabs"><button class="active">Thuộc tính</button><button>Điều chỉnh</button></div>
          ${inspectorPanels(p)}
        </aside>
      </div>
      <section class="timeline-editor">
        <div class="timeline-tools"><button title="Chọn clip trên timeline">⌁ Chọn</button><button onclick="selectTool('split')" title="Mở công cụ băm nhỏ">✂ Tách</button><button onclick="removeTimelineClip()" title="Xóa clip đang chọn khỏi timeline">▱ Xóa</button>
          <span></span><button>−</button><input type="range" min="1" max="100" value="45"><button>＋</button></div>
        <div class="timeline-body">
          <div class="track-labels"><div><b>V1</b><small>Video chính</small></div><div><b>A1</b><small>Âm thanh</small></div></div>
          <div class="tracks"><div class="timeline-ruler"><span>00:00</span><span>00:10</span><span>00:20</span><span>00:30</span><span>00:40</span></div>
            <div class="playhead"><button type="button" class="playhead-handle" title="Giữ và kéo để tua timeline" aria-label="Kéo vạch thời gian"></button></div><div class="video-track ${timelinePaths.length?"":"empty"}" id="video-track">${clips || `<div class="timeline-drop-hint"><b>Kéo video xuống đây</b><small>Thả clip từ Media dự án vào track V1</small></div>`}</div>
            <div class="audio-track">${timelinePaths.map((_,i)=>`<div class="audio-wave" style="--clip:${Math.max(120,220-i*12)}px"></div>`).join("")}</div>
          </div>
        </div>
      </section>
      <aside class="floating-job-log">
        <button onclick="this.parentElement.classList.toggle('open')">▤ <span>${p.progress||0}%</span></button>
        <div><strong>Nhật ký xử lý</strong><div class="job-progress-card"><b>${esc(stageText(p))}</b><small>${esc(videoProgressText(p))}</small><div class="progress"><span style="width:${p.progress||0}%"></span></div></div><div class="log-box">${logs || "Chưa có nhật ký."}</div></div>
      </aside>
    </div>`;
  $("#asset-query").addEventListener("input", filterAssets);
  setupPreview();
  setupTimelineDrop();
  setupPlayheadScrub();
  loadTimelineDurations();
  $("#video-picker").addEventListener("change", event => uploadFiles([...event.target.files]));
  const browser=$(".media-browser");
  browser.addEventListener("dragover", event=>{event.preventDefault();browser.classList.add("dragging")});
  browser.addEventListener("dragleave", event=>{if(!browser.contains(event.relatedTarget))browser.classList.remove("dragging")});
  browser.addEventListener("drop", event=>{
    event.preventDefault();browser.classList.remove("dragging");
    const files=[...event.dataTransfer.files].filter(file=>file.type.startsWith("video/")||/\.(mkv|mov|m4v|avi|ts|mts|m2ts|webm)$/i.test(file.name));
    if(files.length)uploadFiles(files);
  });
  let draggedIndex=null;
  $$("[data-media-index]").forEach(asset=>{
    asset.addEventListener("dragstart", event=>{draggedIndex=+asset.dataset.mediaIndex;event.dataTransfer.effectAllowed="copyMove";event.dataTransfer.setData("text/media-index",String(draggedIndex))});
    asset.addEventListener("dragover", event=>event.preventDefault());
    asset.addEventListener("drop", event=>{event.preventDefault();event.stopPropagation();reorderMedia(draggedIndex,+asset.dataset.mediaIndex)});
  });
}

function batchResultPanel() {
  if(!batchResults.length)return `<div class="batch-results-empty">Chưa có kết quả batch.</div>`;
  return `<div class="batch-results-list">${batchResults.map(item=>`
    <div class="batch-result ${esc(item.status)}">
      <div><b>${esc(item.name)}</b><small>${esc(item.step||item.status)}${item.error?` • ${esc(item.error)}`:""}</small></div>
      <span class="batch-status">${esc(item.status)}</span>
      <button type="button" onclick="openBatchResult(${item.index},'folder')" ${item.folder_path?"":"disabled"}>Folder</button>
      <button type="button" onclick="openBatchResult(${item.index},'final')" ${item.final_path?"":"disabled"}>Final</button>
      <button type="button" onclick="openBatchResult(${item.index},'log')" ${item.log_path?"":"disabled"}>Log</button>
      <button type="button" onclick="retrySingleFailedVideo(${item.index})" ${item.status==="error"?"":"disabled"}>Retry 1</button>
    </div>`).join("")}</div>`;
}

function inspectorPanels(p) {
  return `
    <section class="inspector-panel active" data-tool-panel="media">
      <span class="eyebrow">THÔNG TIN DỰ ÁN</span><h3>${esc(p.name)}</h3>
      <div class="property-list"><label>Video nguồn <b>${p.input_paths?.length||0} file</b></label>
      <label>Đầu ra <b>${esc(p.output_path||"Chưa chọn")}</b></label>
      <label>Ưu tiên <b>${esc(p.priority)}</b></label><label>Tiến trình <b>${p.progress||0}%</b></label><label>Giai đoạn <b>${esc(p.job_stage||"—")}</b></label><label>Đang làm <b>${esc(p.current_step||"—")}</b></label><label>Video hiện tại <b>${esc(videoProgressText(p))}</b></label></div>
      <div class="batch-results-card"><div class="batch-results-head"><b>Kết quả từng video</b><button type="button" onclick="retryFailedVideos()">Retry lỗi</button></div>${batchResultPanel()}</div>
      <button class="button full" onclick="editProject()">Chỉnh file và đầu ra</button>
    </section>
    <section class="inspector-panel" data-tool-panel="concat"><span class="eyebrow">NỐI VIDEO DÀI</span><h3>Thiết lập xuất</h3>
      <label>Định dạng<select id="concat-format"><option value="mkv">MKV — khuyên dùng</option><option value="mp4">MP4</option></select></label>
      <label class="check"><input id="concat-safe" type="checkbox"> Sửa timestamp an toàn</label>
      <div class="info-callout">Stream copy giúp nối nhanh mà không giảm chất lượng.</div>
      <button class="button primary full" onclick="runTool('concat')">Nối ${p.input_paths?.length||0} video</button>
    </section>
    <section class="inspector-panel" data-tool-panel="split"><span class="eyebrow">BĂM NHỎ VIDEO</span><h3>Chia theo thời lượng</h3>
      <label>Thời lượng mỗi đoạn<div class="input-unit"><input id="split-seconds" type="number" min="1" value="60"><span>giây</span></div></label>
      <label class="check"><input id="split-accurate" type="checkbox"> Chính xác từng khung hình</label>
      <button class="button primary full" onclick="runTool('split')">Bắt đầu băm nhỏ</button>
    </section>
    <section class="inspector-panel" data-tool-panel="zoom"><span class="eyebrow">BIẾN ĐỔI</span><h3>Phóng to / thu nhỏ</h3>
      <label>Thu phóng<div class="range-row"><input id="zoom-range" type="range" min="25" max="300" value="110" oninput="$('#zoom-percent').value=this.value"><input id="zoom-percent" type="number" value="110"></div></label>
      <div class="xy-grid"><label>Vị trí X<input id="zoom-x" type="number" value="0"></label><label>Vị trí Y<input id="zoom-y" type="number" value="0"></label></div>
      <button class="button primary full" onclick="runTool('zoom')">Áp dụng biến đổi</button>
    </section>
    <section class="inspector-panel" data-tool-panel="audio"><span class="eyebrow">ÂM THANH</span><h3>Tách nhạc nền</h3>
      <label>Phương pháp<select id="audio-mode"><option value="audio">Trích xuất audio</option><option value="audio_ai">AI tách vocal / nhạc nền</option></select></label>
      <label>Định dạng<select id="audio-format"><option>mp3</option><option>wav</option><option>aac</option></select></label>
      <div class="info-callout">AI cần cài Demucs. Trích xuất thường dùng FFmpeg.</div>
      <button class="button primary full" onclick="runTool('audio')">Bắt đầu tách âm thanh</button>
    </section>
    <section class="inspector-panel" data-tool-panel="batch"><span class="eyebrow">PIPELINE HÀNG LOẠT</span><h3>Voice → Cắt đoạn → Zoom so le</h3>
      <label class="check"><input id="batch-ai-voice" type="checkbox" checked> Tách voice khỏi nhạc nền bằng AI</label>
      <label class="check"><input id="batch-remove-bg" type="checkbox" checked> Xóa nhạc nền, giữ giọng nói chính</label>
      <label>Thời lượng mỗi Part<div class="input-unit"><input id="batch-seconds" type="number" min="3" step="0.5" value="5"><span>phút</span></div></label>
      <div class="xy-grid"><label>Zoom đoạn lẻ %<input id="batch-odd-zoom" type="number" min="25" max="300" value="100"></label><label>Zoom đoạn chẵn %<input id="batch-even-zoom" type="number" min="25" max="300" value="110"></label></div>
      <label>Kiểu zoom<select id="batch-zoom-mode"><option value="center">Cắt vào tâm hình</option><option value="custom">Tùy chỉnh vị trí</option></select></label>
      <div class="xy-grid"><label>Vị trí X<input id="batch-pos-x" type="number" value="0"></label><label>Vị trí Y<input id="batch-pos-y" type="number" value="0"></label></div>
      <label>Chất lượng CRF<input id="batch-crf" type="number" min="12" max="35" value="20"></label>
      <label>Bitrate video<input id="batch-bitrate" placeholder="auto hoặc 8M" value="auto"></label>
      <label>Ghép final<select id="batch-final-mode"><option value="fast">Nhanh - stream copy</option><option value="safe">An toàn - re-encode final</option></select></label>
      <label>Encode video<select id="batch-encoder-mode"><option value="auto">Auto - ưu tiên NVIDIA, lỗi thì về CPU</option><option value="nvidia">NVIDIA NVENC</option><option value="cpu">CPU libx264</option></select></label>
      <label class="check"><input id="batch-resume" type="checkbox" checked> Tiếp tục từ file đã xử lý nếu chạy lại</label>
      <label class="check"><input id="batch-retry-failed" type="checkbox"> Chỉ chạy lại các video đang lỗi</label>
      <div class="info-callout">Output mỗi video: audio/voice.wav, parts/part_001.mp4..., final.mp4. Audio được xử lý nguyên track rồi ghép lại cuối để giảm lệch sync.</div>
      <button class="button primary full" onclick="runTool('batch')">Chạy pipeline hàng loạt</button>
    </section>
    <section class="inspector-panel" data-tool-panel="effects"><span class="eyebrow">HIỆU ỨNG</span><h3>Hiệu ứng nhanh</h3>
      <div class="effect-grid"><button data-effect="fade_in" onclick="toggleEffect(this)">Fade in</button><button data-effect="blur" onclick="toggleEffect(this)">Blur</button><button data-effect="brightness" onclick="toggleEffect(this)">Tăng sáng</button><button data-effect="grayscale" onclick="toggleEffect(this)">Đen trắng</button><button data-effect="flip" onclick="toggleEffect(this)">Lật ngang</button><button data-effect="rotate" onclick="toggleEffect(this)">Xoay 90°</button></div>
      <button class="button primary full" onclick="runTool('effects')">Áp dụng hiệu ứng</button>
    </section>`;
}

function selectTool(tool) {
  $$("[data-editor-tool]").forEach(button => button.classList.toggle("active", button.dataset.editorTool===tool));
  $$("[data-tool-panel]").forEach(panel => panel.classList.toggle("active", panel.dataset.toolPanel===tool));
}

async function openOutputFolder() {
  try {
    const result=await api(`/api/projects/${project.id}/open-output`, {method:"POST", body:"{}"});
    toast(result.message);
    await refreshProject();
  } catch(error) { toast(error.message); }
}

async function openFinalVideo() {
  try {
    const result=await api(`/api/projects/${project.id}/open-final`, {method:"POST", body:"{}"});
    toast(result.message);
    await refreshProject();
  } catch(error) { toast(error.message); }
}

async function openBatchResult(index,type) {
  try {
    const suffix = type === 'log' ? '?log' : type === 'folder' ? '?folder' : '?final';
    const result=await api(`/api/projects/${project.id}/open-result/${index}${suffix}`, {method:"POST", body:"{}"});
    toast(result.message);
  } catch(error) { toast(error.message); }
}

async function cancelJob() {
  if(!confirm("Dừng/hủy tác vụ đang chạy? FFmpeg hiện tại sẽ bị dừng."))return;
  try {
    const result=await api(`/api/projects/${project.id}/cancel`, {method:"POST", body:"{}"});
    toast(result.message);
    await refreshProject();
  } catch(error) { toast(error.message); }
}

function currentTimelinePaths() {
  return [...(project.settings?.timeline_paths||[])];
}

function batchOptions(extra={}) {
  return {
    timeline_paths: currentTimelinePaths(),
    enable_ai_voice:$("#batch-ai-voice")?.checked ?? true,
    remove_background:$("#batch-remove-bg")?.checked ?? true,
    segment_seconds:+($("#batch-seconds")?.value || 5),
    odd_zoom_percent:+($("#batch-odd-zoom")?.value || 100),
    even_zoom_percent:+($("#batch-even-zoom")?.value || 110),
    zoom_mode:$("#batch-zoom-mode")?.value || "center",
    pos_x:+($("#batch-pos-x")?.value || 0),
    pos_y:+($("#batch-pos-y")?.value || 0),
    crf:$("#batch-crf")?.value || "20",
    bitrate:$("#batch-bitrate")?.value || "auto",
    final_concat_mode:$("#batch-final-mode")?.value || "fast",
    encoder_mode:$("#batch-encoder-mode")?.value || "auto",
    resume_enabled:$("#batch-resume")?.checked ?? true,
    retry_failed_only:$("#batch-retry-failed")?.checked ?? false,
    ...extra,
  };
}

async function retryFailedVideos() {
  const failedCount=batchResults.filter(item=>item.status==="error").length;
  if(!failedCount){toast("Chưa có video lỗi để retry");return;}
  const options=batchOptions({retry_failed_only:true});
  if(!options.timeline_paths.length){toast("Hãy kéo video từ thư viện xuống timeline trước");return;}
  try {
    const result=await api(`/api/projects/${project.id}/run`, {method:"POST", body:JSON.stringify({operation:"batch_voice_cut_zoom", options})});
    toast(result.message);
    await refreshProject();
  } catch(error) { toast(error.message); }
}

async function retrySingleFailedVideo(index) {
  const item=batchResults.find(entry=>entry.index===index);
  if(!item || item.status!=="error"){toast("Video này chưa ở trạng thái lỗi");return;}
  const options=batchOptions({timeline_paths:[item.source], retry_failed_only:false});
  try {
    const result=await api(`/api/projects/${project.id}/run`, {method:"POST", body:JSON.stringify({operation:"batch_voice_cut_zoom", options})});
    toast(result.message);
    await refreshProject();
  } catch(error) { toast(error.message); }
}

async function runTool(operation) {
  let actual=operation, options={};
  if(operation==="concat") options={safe_mode:$("#concat-safe").checked,format:$("#concat-format").value};
  options.timeline_paths=currentTimelinePaths();
  if(!options.timeline_paths.length){toast("Hãy kéo video từ thư viện xuống timeline trước");return}
  if(operation==="split") options={segment_seconds:+$("#split-seconds").value,accurate:$("#split-accurate").checked};
  if(operation==="zoom") options={zoom_percent:+$("#zoom-percent").value,pos_x:+$("#zoom-x").value,pos_y:+$("#zoom-y").value};
  if(operation==="audio"){actual=$("#audio-mode").value;options={audio_format:$("#audio-format").value}}
  if(operation==="batch"){
    actual="batch_voice_cut_zoom";
    options=batchOptions();
  }
  if(operation==="effects"){options={effects:$$(`[data-effect].active`).map(button=>button.dataset.effect)};if(!options.effects.length){toast("Hãy chọn ít nhất một hiệu ứng");return}}
  try {
    const result=await api(`/api/projects/${project.id}/run`, {
      method:"POST", body:JSON.stringify({operation:actual, options})
    });
    toast(result.message);
    await refreshProject();
  } catch(error) { toast(error.message); }
}

function setupTimelineDrop() {
  const track=$("#video-track");
  if(!track)return;
  const clips=()=>$$(`[data-timeline-index]`,track);
  const insertionIndex=clientX=>{
    const items=clips();
    for(let i=0;i<items.length;i++) if(clientX < items[i].getBoundingClientRect().left + items[i].offsetWidth/2) return i;
    return items.length;
  };
  const clearMarker=()=>{track.classList.remove("drag-over");clips().forEach(clip=>clip.classList.remove("drop-before"))};
  clips().forEach(clip=>{
    clip.addEventListener("dragstart",event=>{
      event.stopPropagation();
      event.dataTransfer.effectAllowed="move";
      event.dataTransfer.setData("text/timeline-index",clip.dataset.timelineIndex);
      clip.classList.add("dragging-clip");
    });
    clip.addEventListener("dragend",()=>{clip.classList.remove("dragging-clip");clearMarker()});
  });
  track.addEventListener("dragover",event=>{
    event.preventDefault();track.classList.add("drag-over");
    const moving=event.dataTransfer.types.includes("text/timeline-index");
    event.dataTransfer.dropEffect=moving?"move":"copy";
    clips().forEach(clip=>clip.classList.remove("drop-before"));
    const at=insertionIndex(event.clientX);if(clips()[at])clips()[at].classList.add("drop-before");
  });
  track.addEventListener("dragleave",event=>{if(!track.contains(event.relatedTarget))clearMarker()});
  track.addEventListener("drop",async event=>{
    event.preventDefault();event.stopPropagation();
    const at=insertionIndex(event.clientX);
    const timelineRaw=event.dataTransfer.getData("text/timeline-index");
    const mediaRaw=event.dataTransfer.getData("text/media-index");
    clearMarker();
    if(timelineRaw!==""){await moveTimelineClip(+timelineRaw,at);return}
    if(mediaRaw!==""&&project.input_paths?.[+mediaRaw])await addToTimeline(+mediaRaw,at);
  });
}async function saveTimeline(paths,message) {
  const settings={...(project.settings||{}),timeline_paths:paths};
  await api(`/api/projects/${project.id}`,{method:"PATCH",body:JSON.stringify({settings})});
  project.settings=settings;render();toast(message);
}
async function addToTimeline(mediaIndex,insertAt) {
  const paths=[...(project.settings?.timeline_paths||[])];
  const at=Math.max(0,Math.min(paths.length,Number.isInteger(insertAt)?insertAt:paths.length));
  paths.splice(at,0,project.input_paths[mediaIndex]);selectedTimelineIndex=at;selectedMediaIndex=mediaIndex;
  await saveTimeline(paths,"Đã thêm clip vào timeline");
}
async function moveTimelineClip(from,to) {
  const paths=[...(project.settings?.timeline_paths||[])];
  if(from<0||from>=paths.length)return;
  const [clip]=paths.splice(from,1);if(to>from)to--;to=Math.max(0,Math.min(paths.length,to));
  paths.splice(to,0,clip);selectedTimelineIndex=to;
  await saveTimeline(paths,"Đã cập nhật vị trí clip trên timeline");
}
function selectTimelineClip(timelineIndex,mediaIndex,event) {
  const ratio=event?.currentTarget?Math.max(0,Math.min(1,(event.clientX-event.currentTarget.getBoundingClientRect().left)/event.currentTarget.offsetWidth)):0;
  loadTimelinePosition(timelineIndex,ratio,true);
}
async function removeTimelineClip() {
  const paths=[...(project.settings?.timeline_paths||[])];
  if(selectedTimelineIndex<0||!paths[selectedTimelineIndex]){toast("Hãy chọn một clip trên timeline");return}
  paths.splice(selectedTimelineIndex,1);selectedTimelineIndex=Math.min(selectedTimelineIndex,paths.length-1);
  await saveTimeline(paths,"Đã xóa clip khỏi timeline; video gốc vẫn còn trong thư viện");
}
function setupPlayheadScrub() {
  const playhead=$('.playhead');if(!playhead)return;
  const handle=$('.playhead-handle')||playhead;
  let suppressClick=false;
  const seekAt=(clientX,autoplay=false)=>{
    const clips=$$('[data-timeline-index]');if(!clips.length)return;
    let index=0,ratio=0;
    if(clientX<=clips[0].getBoundingClientRect().left){index=0;ratio=0}
    else if(clientX>=clips.at(-1).getBoundingClientRect().right){index=clips.length-1;ratio=1}
    else for(let i=0;i<clips.length;i++){const rect=clips[i].getBoundingClientRect();if(clientX>=rect.left&&clientX<=rect.right){index=i;ratio=(clientX-rect.left)/rect.width;break}}
    setPlayheadDragPosition(index,ratio);
    loadTimelinePosition(index,ratio,autoplay);
  };
  const track=$('#video-track');
  track?.addEventListener('click',event=>{if(!suppressClick&&event.target.closest('.timeline-clip'))seekAt(event.clientX,true)});
  handle.addEventListener('pointerdown',event=>{
    if(event.button!==0)return;
    event.preventDefault();event.stopPropagation();suppressClick=true;
    const video=$('#preview-video'),wasPlaying=video&&!video.paused;video?.pause();
    playhead.classList.add('scrubbing');seekAt(event.clientX,false);
    const move=moveEvent=>{moveEvent.preventDefault();seekAt(moveEvent.clientX,false)};
    const up=upEvent=>{
      seekAt(upEvent.clientX,false);playhead.classList.remove('scrubbing');
      window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);
      if(wasPlaying)$('#preview-video')?.play().catch(()=>{});
      setTimeout(()=>{suppressClick=false},80);
    };
    window.addEventListener('pointermove',move,{passive:false});window.addEventListener('pointerup',up,{once:true});window.addEventListener('pointercancel',up,{once:true});
  });
}async function loadTimelineDurations() {
  const paths=project?.settings?.timeline_paths||[];
  timelineDurations=await Promise.all(paths.map(async path=>{
    if(mediaDurationCache.has(path))return mediaDurationCache.get(path);
    const mediaIndex=project.input_paths.indexOf(path);
    try{const info=await api(`/api/projects/${project.id}/media/${mediaIndex}/info`);const duration=Math.max(0,+info.duration||0);mediaDurationCache.set(path,duration);return duration}catch{return 0}
  }));
  const total=timelineDurations.reduce((sum,value)=>sum+value,0);
  const rulerLabels=$$('.timeline-ruler span');rulerLabels.forEach((label,index)=>label.textContent=formatTime(total*index/Math.max(1,rulerLabels.length-1)));
  const track=$('#video-track');const gapPixels=Math.max(0,(timelineDurations.length-1)*2);const available=Math.max(900,track?.clientWidth||900)-gapPixels;
  $$('[data-timeline-index]').forEach((clip,index)=>clip.style.width=`${Math.max(1,total?timelineDurations[index]/total*available:1)}px`);
  $$('.audio-wave').forEach((clip,index)=>clip.style.width=`${Math.max(1,total?timelineDurations[index]/total*available:1)}px`);
  syncGlobalTimelineUI($('#preview-video'));
}
function globalTimelineTime(video) {
  const before=timelineDurations.slice(0,Math.max(0,selectedTimelineIndex)).reduce((sum,value)=>sum+value,0);
  return before+(video?.currentTime||0);
}
function syncGlobalTimelineUI(video) {
  const total=timelineDurations.reduce((sum,value)=>sum+value,0);
  const current=Math.min(total,globalTimelineTime(video));
  if($('#preview-current'))$('#preview-current').textContent=formatTime(current);
  if($('#preview-total'))$('#preview-total').textContent=formatTime(total);
  if($('#preview-seek'))$('#preview-seek').value=total?Math.round(current/total*1000):0;
  syncTimelinePlayhead(video);
}
function seekGlobalTimeline(ratio,autoplay=false) {
  const total=timelineDurations.reduce((sum,value)=>sum+value,0);if(!total)return;
  let target=Math.max(0,Math.min(total,total*ratio)),elapsed=0,index=0;
  for(;index<timelineDurations.length;index++){const duration=timelineDurations[index];if(target<=elapsed+duration||index===timelineDurations.length-1){loadTimelinePosition(index,duration?(target-elapsed)/duration:0,autoplay);return}elapsed+=duration}
}
function setupPreview() {
  const video=$('#preview-video'),seek=$('#preview-seek');if(!video||!seek)return;
  video.addEventListener('loadedmetadata',()=>syncGlobalTimelineUI(video));
  video.addEventListener('timeupdate',()=>syncGlobalTimelineUI(video));
  video.addEventListener('play',()=>{$('#preview-play').textContent='❚❚';startPlayheadAnimation(video)});
  video.addEventListener('pause',()=>{$('#preview-play').textContent='▶';cancelAnimationFrame(previewAnimationFrame);syncGlobalTimelineUI(video)});
  video.addEventListener('ended',playNextTimelineClip);
  video.addEventListener('error',()=>{const error=$('#preview-error');error.textContent='Trình duyệt không phát được codec này. Hãy chuyển clip sang MP4/H.264.';error.classList.remove('hidden')});
}function selectMedia(index) {
  if(!project?.input_paths?.[index])return;
  selectedMediaIndex=index;
  $$('[data-media-index]').forEach(el=>el.classList.toggle('selected',+el.dataset.mediaIndex===index));
}
function loadTimelinePosition(timelineIndex,ratio=0,autoplay=false) {
  const paths=project?.settings?.timeline_paths||[];
  const mediaIndex=project?.input_paths?.indexOf(paths[timelineIndex]);
  if(mediaIndex<0)return;
  const video=$('#preview-video');
  const same=selectedTimelineIndex===timelineIndex&&selectedMediaIndex===mediaIndex;
  selectedTimelineIndex=timelineIndex;selectedMediaIndex=mediaIndex;
  $$('[data-timeline-index]').forEach(el=>el.classList.toggle('selected',+el.dataset.timelineIndex===timelineIndex));
  if(!video)return;
  const apply=()=>{if(Number.isFinite(video.duration))video.currentTime=Math.max(0,Math.min(video.duration,video.duration*ratio));syncTimelinePlayhead(video);if(autoplay)video.play().catch(()=>{})};
  if(!same){video.src=`/api/projects/${encodeURIComponent(project.id)}/media/${mediaIndex}`;video.load();video.addEventListener('loadedmetadata',apply,{once:true})}else apply();
}
function setPlayheadDragPosition(index,ratio) {
  const playhead=$('.playhead'),clip=$(`[data-timeline-index="${index}"]`);if(!playhead||!clip)return;
  playhead.style.left=`${clip.offsetLeft+clip.offsetWidth*Math.max(0,Math.min(1,ratio))}px`;
  const total=timelineDurations.reduce((sum,value)=>sum+value,0);
  const current=timelineDurations.slice(0,index).reduce((sum,value)=>sum+value,0)+(timelineDurations[index]||0)*ratio;
  if($('#preview-current'))$('#preview-current').textContent=formatTime(current);
  if($('#preview-total'))$('#preview-total').textContent=formatTime(total);
}function syncTimelinePlayhead(video) {
  const playhead=$('.playhead');
  if(playhead?.classList.contains('scrubbing'))return;
  const clip=$(`[data-timeline-index="${selectedTimelineIndex}"]`);
  if(!playhead||!clip||!video)return;
  const ratio=Number.isFinite(video.duration)&&video.duration>0?Math.max(0,Math.min(1,video.currentTime/video.duration)):0;
  playhead.style.left=`${clip.offsetLeft+clip.offsetWidth*ratio}px`;
  playhead.classList.add('active');
}
function startPlayheadAnimation(video) {
  cancelAnimationFrame(previewAnimationFrame);
  const tick=()=>{if(video.paused||video.ended)return;syncTimelinePlayhead(video);previewAnimationFrame=requestAnimationFrame(tick)};
  previewAnimationFrame=requestAnimationFrame(tick);
}
function togglePreview() {
  const paths=project?.settings?.timeline_paths||[];
  if(paths.length&&selectedTimelineIndex<0){const mediaIndex=project.input_paths.indexOf(paths[0]);selectTimelineClip(0,mediaIndex);setTimeout(()=>$("#preview-video")?.play().catch(()=>{}),0);return}
  const video=$("#preview-video");if(video)video.paused?video.play().catch(()=>{}):video.pause();
}
function playNextTimelineClip(){
  const paths=project?.settings?.timeline_paths||[];if(selectedTimelineIndex<0||selectedTimelineIndex>=paths.length-1)return;
  const next=selectedTimelineIndex+1;selectTimelineClip(next,project.input_paths.indexOf(paths[next]));
  setTimeout(()=>$("#preview-video")?.play().catch(()=>{}),0);
}
function seekPreview(seconds) { const video=$("#preview-video"); if(video && Number.isFinite(video.duration)) video.currentTime=Math.max(0,Math.min(video.duration,video.currentTime+seconds)); }
function stepMedia(direction) {
  const timeline=project?.settings?.timeline_paths||[];
  if(timeline.length){const current=selectedTimelineIndex<0?0:selectedTimelineIndex;const next=(current+direction+timeline.length)%timeline.length;selectTimelineClip(next,project.input_paths.indexOf(timeline[next]));return}
  const count=project?.input_paths?.length||0;if(count)selectMedia((selectedMediaIndex+direction+count)%count);
}
function formatTime(seconds) { seconds=Math.max(0,Math.floor(seconds||0));const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=seconds%60;return h?`${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`:`${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`; }
function toggleEffect(button) { button.classList.toggle("active"); }
function chooseVideos() {
  $("#video-picker").click();
}

async function uploadFiles(files) {
  if(!files.length)return;
  const progress=$("#upload-progress");
  progress.classList.remove("hidden");
  let completed=0;
  try {
    for(const file of files) {
      await uploadOne(file, percent=>{
        const total=((completed + percent/100)/files.length)*100;
        progress.querySelector("span").style.width=`${total}%`;
        progress.querySelector("small").textContent=`Đang import ${file.name} • ${Math.round(percent)}% • ${completed+1}/${files.length}`;
      });
      completed++;
    }
    toast(`Đã import ${files.length} video theo đúng thứ tự`);
    await refreshProject();
  } catch(error) {
    toast(`Import lỗi: ${error.message}`);
  }
}

function uploadOne(file,onProgress) {
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();
    xhr.open("POST",`/api/projects/${project.id}/upload`);
    xhr.setRequestHeader("Content-Type","application/octet-stream");
    xhr.setRequestHeader("X-File-Name",encodeURIComponent(file.name));
    xhr.upload.onprogress=event=>{if(event.lengthComputable)onProgress(event.loaded/event.total*100)};
    xhr.onload=()=>xhr.status>=200&&xhr.status<300?resolve(JSON.parse(xhr.responseText)):reject(new Error(JSON.parse(xhr.responseText||"{}").error||`HTTP ${xhr.status}`));
    xhr.onerror=()=>reject(new Error("Mất kết nối với server local"));
    xhr.send(file);
  });
}

async function reorderMedia(from,to) {
  if(from===null||from===to||from<0||to<0)return;
  const paths=[...(project.input_paths||[])];
  const [moved]=paths.splice(from,1);paths.splice(to,0,moved);
  await api(`/api/projects/${project.id}`,{method:"PATCH",body:JSON.stringify({input_paths:paths})});
  project.input_paths=paths;render();toast("Đã thay đổi thứ tự video");
}

async function removeMedia(index) {
  if(!confirm("Xóa video này khỏi dự án? File gốc vẫn được giữ trong thư mục upload."))return;
  const paths=[...(project.input_paths||[])];paths.splice(index,1);
  await api(`/api/projects/${project.id}`,{method:"PATCH",body:JSON.stringify({input_paths:paths})});
  project.input_paths=paths;render();toast("Đã xóa video khỏi dự án");
}
function editProject() {
  location.href = `/?edit=${encodeURIComponent(project.id)}`;
}
function filterAssets(event) {
  const query=event.target.value.toLocaleLowerCase("vi");
  $$(".media-asset").forEach(item => item.classList.toggle("hidden", !item.textContent.toLocaleLowerCase("vi").includes(query)));
}
async function refreshProject() {
  const payload=await api("/api/projects");
  const updated=payload.projects.find(item=>item.id===projectId);
  if(updated){
    project=updated;
    try { batchResults = (await api(`/api/projects/${project.id}/batch-results`)).results || []; }
    catch { batchResults = []; }
    render();
  }
}
function fileName(path){return String(path).split(/[\\/]/).pop()}
function toast(message){const el=document.createElement("div");el.className="toast";el.textContent=message;$("#toast-container").append(el);setTimeout(()=>el.remove(),2800)}

loadProject().catch(error => {
  $("#project-editor-root").innerHTML=`<div class="standalone-error"><h1>Không thể tải dự án</h1><p>${esc(error.message)}</p><a class="button primary" href="/">Quay lại Dashboard</a></div>`;
});
setInterval(()=>{if(project?.status==="Đang chạy")refreshProject()},3000);
