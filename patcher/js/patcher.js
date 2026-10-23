(function(){
  // Page logic: patch the dropped exe (exe.js), fetch the files listed in files.json (json.js)
  // and offer everything as one zip (zip.js).
  const { patchExe, DLL_NAME } = window.ExePatch;
  const { makeZip, saveBlob } = window.Zip;
  const { load: loadManifest, fetchFiles, MANIFEST_URL } = window.FilesJson;

  const dropEl = document.getElementById('drop');
  const fileInput = document.getElementById('file');
  const resultEl = document.getElementById('result');
  const statusEl = document.getElementById('status');
  const infoEl = document.getElementById('info');
  const logEl = document.getElementById('log');
  const dlBtn = document.getElementById('dl');
  const footEl = document.getElementById('foot');

  const EXE_NAME = 'CSTORM.EXE'; // name of the patched exe inside the zip, whatever was uploaded
  let manifest = null, zipBlob = null, zipName = '';

  function fmtBytes(n){
    if(n < 1024) return n + ' B';
    if(n < 1024*1024) return (n/1024).toFixed(1) + ' KB';
    return (n/(1024*1024)).toFixed(2) + ' MB';
  }

  function show(kind, msg, info, log){
    resultEl.hidden = false;
    statusEl.className = 'status ' + kind;
    statusEl.textContent = msg;
    infoEl.innerHTML = '';
    for (const [k, v] of info){
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = k; dd.textContent = v; infoEl.append(dt, dd);
    }
    logEl.textContent = log.join('\n');
    logEl.hidden = !log.length;
  }

  async function handleFile(file){
    zipBlob = null; dlBtn.disabled = true;
    if (!file) return;
    const input = new Uint8Array(await file.arrayBuffer());
    const info = [['File', `${file.name} (${fmtBytes(input.length)})`]];
    let res, extras;
    try {
      if (!manifest) manifest = await loadManifest();
      res = patchExe(input);
      show('', 'Downloading the V1.3 files…', info, res.log);
      extras = await fetchFiles(manifest.files);
    } catch (e){ show('err', e.message, info, res ? res.log : []); return; }

    const entries = [{ name: EXE_NAME, data: res.bytes }, ...extras];
    zipBlob = new Blob([makeZip(entries)], { type: 'application/zip' });
    zipName = (manifest.zipName || '{exe}.zip').replace('{exe}', EXE_NAME.replace(/\.exe$/i, ''));
    info.push(['Zip', `${zipName} (${fmtBytes(zipBlob.size)})`], ['Contents', entries.map(e => e.name).join(', ')]);
    if (res.already) show('warn', `This executable already loads ${DLL_NAME}. The zip contains it unchanged, plus the V1.3 files.`, info, res.log);
    else show('ok', 'Patched successfully.', info, res.log);
    dlBtn.disabled = false;
  }

  fileInput.addEventListener('change', e => handleFile(e.target.files[0]));
  ['dragenter', 'dragover'].forEach(t => dropEl.addEventListener(t, e => { e.preventDefault(); dropEl.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(t => dropEl.addEventListener(t, e => { e.preventDefault(); dropEl.classList.remove('over'); }));
  dropEl.addEventListener('drop', e => handleFile(e.dataTransfer.files[0]));
  dlBtn.addEventListener('click', () => { if (zipBlob) saveBlob(zipName, zipBlob); });

  loadManifest().then(m => {
    manifest = m;
    footEl.textContent = 'Zip will include: ' + m.files.map(f => f.zipPath).join(', ');
  }).catch(e => {
    footEl.textContent = location.protocol === 'file:'
      ? `Can't read ${MANIFEST_URL} from a local file. Open this page from a web server (e.g. the hosted site).`
      : `Can't load ${MANIFEST_URL}: ${e.message}`;
    footEl.className = 'err';
  });
})();
