/* QR CODE (tiny self-contained encoder)
   Byte mode, error correction level M, versions 1–6 (up to 106 bytes) — plenty for the
   start link. Follows ISO/IEC 18004; the tests decode it and check the Reed–Solomon blocks. */

import { doc, SVGNS } from './util.js';

export const QR = (() => {
  const TOTAL = [0, 26, 44, 70, 100, 134, 172]; // codewords per version
  const ECC = [0, 10, 16, 26, 18, 24, 16];      // ECC codewords per block (level M)
  const BLOCKS = [0, 1, 1, 1, 2, 2, 4];         // blocks (level M)
  function gfMul(x, y) {
    let z = 0;
    for (let i = 7; i >= 0; i--) {
      z = (z << 1) ^ ((z >>> 7) * 0x11D);
      z ^= ((y >>> i) & 1) * x;
    }
    return z;
  }
  function rsDivisor(deg) {
    const r = new Array(deg).fill(0);
    r[deg - 1] = 1;
    let root = 1;
    for (let i = 0; i < deg; i++) {
      for (let j = 0; j < deg; j++) {
        r[j] = gfMul(r[j], root);
        if (j + 1 < deg) r[j] ^= r[j + 1];
      }
      root = gfMul(root, 2);
    }
    return r;
  }
  function rsRemainder(data, div) {
    const r = div.map(() => 0);
    data.forEach((b) => {
      const f = b ^ r.shift();
      r.push(0);
      div.forEach((c, i) => { r[i] ^= gfMul(c, f); });
    });
    return r;
  }
  function codewords(bytes, v) {
    const nb = BLOCKS[v], ecl = ECC[v], cap = (TOTAL[v] - ecl * nb) * 8;
    const bits = [];
    const put = (val, len) => { for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1); };
    put(4, 4); put(bytes.length, 8); bytes.forEach((b) => put(b, 8));
    put(0, Math.min(4, cap - bits.length));
    put(0, (8 - (bits.length % 8)) % 8);
    for (let pad = 0xEC; bits.length < cap; pad ^= 0xEC ^ 0x11) put(pad, 8);
    const data = [];
    for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));
    const short = Math.floor(TOTAL[v] / nb), nShort = nb - (TOTAL[v] % nb), div = rsDivisor(ecl);
    const blocks = [];
    for (let i = 0, k = 0; i < nb; i++) {
      const len = short - ecl + (i < nShort ? 0 : 1);
      const d = data.slice(k, k + len); k += len;
      blocks.push([d, rsRemainder(d, div)]);
    }
    const out = [];
    for (let i = 0; i <= short - ecl; i++) blocks.forEach(([d]) => { if (i < d.length) out.push(d[i]); });
    for (let i = 0; i < ecl; i++) blocks.forEach(([, e]) => out.push(e[i]));
    return out;
  }
  function maskHit(m, x, y) {
    switch (m) {
      case 0: return (x + y) % 2 === 0;
      case 1: return y % 2 === 0;
      case 2: return x % 3 === 0;
      case 3: return (x + y) % 3 === 0;
      case 4: return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0;
      case 5: return ((x * y) % 2) + ((x * y) % 3) === 0;
      case 6: return (((x * y) % 2) + ((x * y) % 3)) % 2 === 0;
      default: return (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
    }
  }
  function penalty(g) {
    const n = g.length;
    let p = 0, dark = 0;
    for (let pass = 0; pass < 2; pass++) {
      for (let a = 0; a < n; a++) {
        let run = 0, prev = -1, line = '';
        for (let b = 0; b < n; b++) {
          const c = pass ? g[b][a] : g[a][b];
          line += c;
          if (c === prev) run++; else { if (run >= 5) p += run - 2; run = 1; prev = c; }
        }
        if (run >= 5) p += run - 2;
        const s = '0000' + line + '0000';
        for (const pat of ['10111010000', '00001011101']) {
          for (let k = s.indexOf(pat); k >= 0; k = s.indexOf(pat, k + 1)) p += 40;
        }
      }
    }
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        dark += g[y][x];
        if (x < n - 1 && y < n - 1) {
          const c = g[y][x];
          if (c === g[y][x + 1] && c === g[y + 1][x] && c === g[y + 1][x + 1]) p += 3;
        }
      }
    }
    return p + Math.floor(Math.abs(dark * 20 - n * n * 10) / (n * n)) * 10;
  }
  /* Returns a size×size grid of 0/1 (1 = dark), or null when the text is too long. */
  function matrix(text) {
    const bytes = Array.from(new TextEncoder().encode(text));
    let v = 1;
    while (v <= 6 && 12 + bytes.length * 8 > (TOTAL[v] - ECC[v] * BLOCKS[v]) * 8) v++;
    if (v > 6) return null;
    const n = 17 + 4 * v;
    const g = Array.from({ length: n }, () => new Array(n).fill(0));
    const fn = Array.from({ length: n }, () => new Array(n).fill(false));
    const set = (grid, x, y, d) => { grid[y][x] = d ? 1 : 0; fn[y][x] = true; };
    for (let i = 0; i < n; i++) { set(g, 6, i, i % 2 === 0); set(g, i, 6, i % 2 === 0); }
    [[3, 3], [n - 4, 3], [3, n - 4]].forEach(([cx, cy]) => {
      for (let dy = -4; dy <= 4; dy++) {
        for (let dx = -4; dx <= 4; dx++) {
          const x = cx + dx, y = cy + dy, d = Math.max(Math.abs(dx), Math.abs(dy));
          if (x >= 0 && y >= 0 && x < n && y < n) set(g, x, y, d !== 2 && d !== 4);
        }
      }
    });
    if (v >= 2) {
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) set(g, n - 7 + dx, n - 7 + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      }
    }
    const format = (grid, mask) => {
      const data = mask; // level M = 00
      let rem = data;
      for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
      const bits = ((data << 10) | rem) ^ 0x5412;
      const bit = (i) => (bits >>> i) & 1;
      for (let i = 0; i <= 5; i++) set(grid, 8, i, bit(i));
      set(grid, 8, 7, bit(6)); set(grid, 8, 8, bit(7)); set(grid, 7, 8, bit(8));
      for (let i = 9; i < 15; i++) set(grid, 14 - i, 8, bit(i));
      for (let i = 0; i < 8; i++) set(grid, n - 1 - i, 8, bit(i));
      for (let i = 8; i < 15; i++) set(grid, 8, n - 15 + i, bit(i));
      set(grid, 8, n - 8, 1); // dark module
    };
    format(g, 0); // reserve the format areas
    const cw = codewords(bytes, v);
    let i = 0;
    for (let right = n - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < n; vert++) {
        for (let j = 0; j < 2; j++) {
          const x = right - j, y = ((right + 1) & 2) === 0 ? n - 1 - vert : vert;
          if (fn[y][x]) continue;
          g[y][x] = i < cw.length * 8 ? (cw[i >>> 3] >>> (7 - (i & 7))) & 1 : 0;
          i++;
        }
      }
    }
    let best = null, bestP = Infinity;
    for (let m = 0; m < 8; m++) {
      const t = g.map((row, y) => row.map((c, x) => (!fn[y][x] && maskHit(m, x, y) ? c ^ 1 : c)));
      format(t, m);
      const p = penalty(t);
      if (p < bestP) { bestP = p; best = t; }
    }
    return best;
  }
  function draw(svg, text) {
    const m = matrix(text);
    if (!m) return false;
    const n = m.length, q = 4;
    let d = '';
    m.forEach((row, y) => row.forEach((c, x) => { if (c) d += `M${x + q} ${y + q}h1v1h-1z`; }));
    svg.setAttribute('viewBox', `0 0 ${n + 2 * q} ${n + 2 * q}`);
    svg.setAttribute('shape-rendering', 'crispEdges');
    svg.dataset.n = String(n);
    svg.dataset.text = text;
    svg.textContent = '';
    const bg = doc.createElementNS(SVGNS, 'rect');
    bg.setAttribute('width', '100%'); bg.setAttribute('height', '100%'); bg.setAttribute('class', 'qr-bg');
    const p = doc.createElementNS(SVGNS, 'path');
    p.setAttribute('d', d); p.setAttribute('class', 'qr-dots');
    svg.append(bg, p);
    return true;
  }
  return { matrix, draw };
})();
