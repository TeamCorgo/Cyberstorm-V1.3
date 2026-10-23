(function(){
  // Adds a community.dll import to the game executable.
  // The import table is a list of 20-byte entries (one per DLL) ending in an empty entry, with
  // other data packed right after it, so it can't grow in place. Instead we copy it into the free
  // space at the end of its section, append a community.dll entry, and point the header at the copy.
  const DLL_NAME = 'community.dll';
  const DLL_IMPORT = 'CommunityVersion';

  const align = (v, a) => Math.ceil(v / a) * a;

  function parsePE(u8){
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    const u16 = o => dv.getUint16(o, true), u32 = o => dv.getUint32(o, true);
    if (u8.length < 0x40 || u16(0) !== 0x5A4D) throw new Error('This is not a Windows executable.');
    const pe = u32(0x3C);
    if (pe + 0xF8 > u8.length || u32(pe) !== 0x4550 || u16(pe + 4) !== 0x14C || u16(pe + 24) !== 0x10B)
      throw new Error('This is not a 32-bit Windows executable.');
    if (u16(pe + 22) & 0x2000) throw new Error('This is a DLL, not the game executable.');
    const opt = pe + 24, secTable = opt + u16(pe + 20);
    const sections = [];
    for (let i = 0; i < u16(pe + 6); i++){
      const h = secTable + 40 * i;
      sections.push({ hdr: h, vsize: u32(h + 8), va: u32(h + 12), rawSize: u32(h + 16), rawPtr: u32(h + 20) });
    }
    const secOf = rva => sections.find(s => rva >= s.va && rva < s.va + s.rawSize);
    const off = rva => { const s = secOf(rva); return s ? s.rawPtr + rva - s.va : -1; };
    const cstr = o => { let s = ''; while (u8[o]) s += String.fromCharCode(u8[o++]); return s; };
    return { dv, u32, opt, sections, secOf, off, cstr, importDir: opt + 104 };
  }

  // Returns [{ off, name }] for each entry in the import table.
  function readImports(P){
    const list = [];
    for (let o = P.off(P.u32(P.importDir)); o >= 0 && P.u32(o + 12); o += 20)
      list.push({ off: o, name: P.cstr(P.off(P.u32(o + 12))) });
    return list;
  }

  function patchExe(input){
    const P = parsePE(input);
    const imports = readImports(P);
    if (!imports.length) throw new Error('This executable has no import table.');
    const log = [`Imports: ${imports.map(d => d.name).join(', ')}`];

    if (imports.some(d => d.name.toLowerCase() === DLL_NAME.toLowerCase())){
      log.push(`${DLL_NAME} is already imported; leaving the executable unchanged.`);
      return { already: true, bytes: input, log };
    }

    // Block layout: entries (old + ours + empty), lookup table, address table, import name, DLL name.
    const n = imports.length;
    const tableSize = (n + 2) * 20;
    const ilt = align(tableSize, 4), iat = ilt + 8, hint = iat + 8;
    const name = hint + align(2 + DLL_IMPORT.length + 1, 2);
    const total = name + DLL_NAME.length + 1;

    // Free space: past VirtualSize if the section sets it; otherwise (Watcom) after the last non-zero byte.
    const sec = P.secOf(P.u32(P.importDir));
    const end = sec.rawPtr + sec.rawSize;
    let used = sec.vsize ? sec.rawPtr + sec.vsize : end;
    if (!sec.vsize) while (used > sec.rawPtr && input[used - 1] === 0) used--;
    const start = align(used + 4, 16); // +4 keeps any trailing null terminator intact
    if (start + total > end)
      throw new Error(`Not enough free space to add ${DLL_NAME} (need ${total} bytes, have ${Math.max(0, end - start)}).`);
    const base = sec.va + start - sec.rawPtr;

    const u8 = input.slice();
    const dv = new DataView(u8.buffer);
    const put = (o, v) => dv.setUint32(o, v, true);
    u8.fill(0, start, start + total);

    imports.forEach((d, i) => u8.set(input.subarray(d.off, d.off + 20), start + 20 * i));
    const entry = start + 20 * n;
    put(entry, base + ilt);        // lookup table
    put(entry + 12, base + name);  // DLL name
    put(entry + 16, base + iat);   // address table (filled in by Windows)
    put(start + ilt, base + hint);
    put(start + iat, base + hint);
    for (let i = 0; i < DLL_IMPORT.length; i++) u8[start + hint + 2 + i] = DLL_IMPORT.charCodeAt(i);
    for (let i = 0; i < DLL_NAME.length; i++) u8[start + name + i] = DLL_NAME.charCodeAt(i);

    put(P.importDir, base);
    put(P.importDir + 4, tableSize);
    if (sec.vsize) put(sec.hdr + 8, Math.max(sec.vsize, base + total - sec.va));
    put(sec.hdr + 36, dv.getUint32(sec.hdr + 36, true) | 0xC0000000); // readable + writable for the address table
    put(P.opt + 64, 0); // checksum: unused for games, so clear the now-stale value

    // Sanity check: re-read the patched file.
    const check = readImports(parsePE(u8)).map(d => d.name);
    if (check.length !== n + 1 || check[n] !== DLL_NAME) throw new Error('Patching failed its self-check; no file was produced.');
    log.push(`Copied the import table to file offset 0x${start.toString(16)} and added ${DLL_NAME}.`);
    return { already: false, bytes: u8, log };
  }

  window.ExePatch = { patchExe, DLL_NAME };
})();
