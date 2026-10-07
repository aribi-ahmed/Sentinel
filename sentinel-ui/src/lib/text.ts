/**
 * Text repair for agent evidence.
 *
 * Snippets reach the UI after a round-trip through Python `str()` and, for some
 * sources, a mis-decode along the way — so they arrive carrying mojibake and
 * escaped bytes that would otherwise render as tofu in every evidence panel.
 */

/**
 * Bytes 0x80-0x9F that Windows-1252 maps to printable characters. Text that was
 * encoded UTF-8 and decoded as cp1252 shows up carrying these, so they have to
 * map back to their original byte to undo the damage.
 */
const CP1252_TO_BYTE: Record<string, number> = {
  '\u20ac': 0x80, '\u201a': 0x82, '\u0192': 0x83, '\u201e': 0x84, '\u2026': 0x85,
  '\u2020': 0x86, '\u2021': 0x87, '\u02c6': 0x88, '\u2030': 0x89, '\u0160': 0x8a,
  '\u2039': 0x8b, '\u0152': 0x8c, '\u017d': 0x8e, '\u2018': 0x91, '\u2019': 0x92,
  '\u201c': 0x93, '\u201d': 0x94, '\u2022': 0x95, '\u2013': 0x96, '\u2014': 0x97,
  '\u02dc': 0x98, '\u2122': 0x99, '\u0161': 0x9a, '\u203a': 0x9b, '\u0153': 0x9c,
  '\u017e': 0x9e, '\u0178': 0x9f,
};

/** Recovers the byte a character stands for, or null if it stands for none. */
function toByte(char: string): number | null {
  const code = char.charCodeAt(0);
  if (code <= 0xff) return code;
  // Python's surrogateescape parks undecodable bytes at U+DC80-U+DCFF.
  if (code >= 0xdc80 && code <= 0xdcff) return code - 0xdc00;
  return CP1252_TO_BYTE[char] ?? null;
}

/**
 * Repairs UTF-8 that was decoded as cp1252 upstream — "â€œOther Betsâ€\udc9d"
 * for "“Other Bets”". Only byte runs that form a valid UTF-8 sequence are
 * rewritten, so correctly decoded punctuation elsewhere in the snippet is left
 * exactly as it is.
 */
function repairMojibake(text: string): string {
  if (!/[\u00c2-\u00f4]/.test(text)) return text;

  const decoder = new TextDecoder('utf-8', { fatal: true });
  const chars = Array.from(text);
  let out = '';

  for (let index = 0; index < chars.length; index += 1) {
    const lead = toByte(chars[index]);
    const width = lead == null ? 0 : lead >= 0xf0 ? 4 : lead >= 0xe0 ? 3 : lead >= 0xc2 ? 2 : 0;
    if (!width || index + width > chars.length) {
      out += chars[index];
      continue;
    }

    const bytes = [lead as number];
    for (let offset = 1; offset < width; offset += 1) {
      const byte = toByte(chars[index + offset]);
      if (byte == null || byte < 0x80 || byte > 0xbf) break;
      bytes.push(byte);
    }
    if (bytes.length !== width) {
      out += chars[index];
      continue;
    }

    try {
      out += decoder.decode(Uint8Array.from(bytes));
      index += width - 1;
    } catch {
      out += chars[index];
    }
  }

  return out;
}

export function cleanText(text: string): string {
  return repairMojibake(text)
    // Whatever the repair could not resolve would render as tofu.
    .replace(/[\ud800-\udfff\ufffd]/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .trim();
}
