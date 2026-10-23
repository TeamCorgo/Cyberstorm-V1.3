(function(){
  // files.json: { zipName, files: [{ src, folder, name? }] }
  // src is a path relative to files.json or a full URL; folder is the folder inside the zip ("" = root).
  const MANIFEST_URL = './files.json';

  async function load(){
    const res = await fetch(MANIFEST_URL, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`${MANIFEST_URL}: HTTP ${res.status}`);
    const m = await res.json();
    if (!Array.isArray(m.files)) throw new Error(`${MANIFEST_URL} has no "files" list.`);
    const base = new URL(MANIFEST_URL, location.href);
    m.files = m.files.map(f => {
      const url = new URL(f.src, base);
      const name = f.name || decodeURIComponent(url.pathname.split('/').pop());
      const folder = (f.folder || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
      return { url: url.href, zipPath: folder ? `${folder}/${name}` : name };
    });
    return m;
  }

  async function fetchFiles(files){
    return Promise.all(files.map(async f => {
      let res;
      try { res = await fetch(f.url); } catch { throw new Error(`Could not download ${f.url}`); }
      if (!res.ok) throw new Error(`Could not download ${f.url} (HTTP ${res.status})`);
      return { name: f.zipPath, data: new Uint8Array(await res.arrayBuffer()) };
    }));
  }

  window.FilesJson = { load, fetchFiles, MANIFEST_URL };
})();
