(function(){
  // Pure-JS ZIP writer using STORE (no compression) — no external libs needed.
  // Format: local file headers + data + central directory + end-of-central-directory.
  function makeZip(files){
    // files: [{name, data: Uint8Array}]
    const enc = new TextEncoder();
    const parts = [];
    const central = [];
    let offset = 0;

    function crc32(data){
      let crc = 0xFFFFFFFF;
      const table = crc32.table || (crc32.table = (() => {
        const t = new Uint32Array(256);
        for (let i = 0; i < 256; i++){ let c = i; for (let j = 0; j < 8; j++) c = c & 1 ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1); t[i] = c; }
        return t;
      })());
      for (let i = 0; i < data.length; i++) crc = table[(crc ^ data[i]) & 0xFF] ^ (crc >>> 8);
      return (crc ^ 0xFFFFFFFF) >>> 0;
    }

    for (const f of files){
      const name = enc.encode(f.name);
      const crc = crc32(f.data);
      const size = f.data.length;
      // Local file header
      const lfh = new Uint8Array(30 + name.length);
      const lv = new DataView(lfh.buffer);
      lv.setUint32(0, 0x04034b50, true);  // signature
      lv.setUint16(4, 20, true);           // version needed
      lv.setUint16(6, 0, true);            // flags
      lv.setUint16(8, 0, true);            // compression: STORE
      lv.setUint16(10, 0, true);           // mod time
      lv.setUint16(12, 0, true);           // mod date
      lv.setUint32(14, crc, true);
      lv.setUint32(18, size, true);        // compressed size
      lv.setUint32(22, size, true);        // uncompressed size
      lv.setUint16(26, name.length, true);
      lv.setUint16(28, 0, true);           // extra length
      lfh.set(name, 30);

      // Central directory entry
      const cde = new Uint8Array(46 + name.length);
      const cv = new DataView(cde.buffer);
      cv.setUint32(0, 0x02014b50, true);   // signature
      cv.setUint16(4, 20, true);           // version made by
      cv.setUint16(6, 20, true);           // version needed
      cv.setUint16(8, 0, true);            // flags
      cv.setUint16(10, 0, true);           // compression: STORE
      cv.setUint16(12, 0, true); cv.setUint16(14, 0, true); // mod time/date
      cv.setUint32(16, crc, true);
      cv.setUint32(20, size, true);
      cv.setUint32(24, size, true);
      cv.setUint16(28, name.length, true);
      cv.setUint16(30, 0, true);           // extra
      cv.setUint16(32, 0, true);           // comment
      cv.setUint16(34, 0, true);           // disk start
      cv.setUint16(36, 0, true);           // internal attr
      cv.setUint32(38, 0, true);           // external attr
      cv.setUint32(42, offset, true);      // local header offset
      cde.set(name, 46);

      parts.push(lfh, f.data);
      central.push(cde);
      offset += lfh.length + size;
    }

    const centralData = concat(central);
    const cdOffset = offset;
    const cdSize = centralData.length;

    // End of central directory record
    const eocd = new Uint8Array(22);
    const ev = new DataView(eocd.buffer);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(4, 0, true); ev.setUint16(6, 0, true);
    ev.setUint16(8, files.length, true);
    ev.setUint16(10, files.length, true);
    ev.setUint32(12, cdSize, true);
    ev.setUint32(16, cdOffset, true);
    ev.setUint16(20, 0, true);

    return concat([...parts, centralData, eocd]);
  }

  function concat(arrays){
    const total = arrays.reduce((s, a) => s + a.length, 0);
    const out = new Uint8Array(total);
    let off = 0;
    for (const a of arrays){ out.set(a, off); off += a.length; }
    return out;
  }

  function saveBlob(filename, blob){
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  window.Zip = { makeZip, saveBlob };
})();
