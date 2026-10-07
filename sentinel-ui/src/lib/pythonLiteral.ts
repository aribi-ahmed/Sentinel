/**
 * Parser for Python `repr()` output.
 *
 * Several agent tools hand their results back as `str(python_object)`, so the
 * evidence payloads arrive as Python literals rather than JSON: single-quoted
 * strings, `True`/`False`/`None`, and tuples. Rewriting those into JSON with
 * regexes breaks the moment a value contains an apostrophe ("Google's") or a
 * brace, which is exactly what search snippets are full of — so this walks the
 * source properly instead.
 */

const ESCAPES: Record<string, string> = {
  n: '\n',
  r: '\r',
  t: '\t',
  b: '\b',
  f: '\f',
  v: '\v',
  a: '\x07',
  '0': '\0',
  '\\': '\\',
  "'": "'",
  '"': '"',
  '\n': '',
};

class PythonLiteralParser {
  private index = 0;
  private readonly source: string;

  constructor(source: string) {
    this.source = source;
  }

  parse(): unknown {
    this.skipSpace();
    const value = this.readValue();
    this.skipSpace();
    if (this.index < this.source.length) {
      throw new SyntaxError(`Trailing input at ${this.index}`);
    }
    return value;
  }

  private get current(): string {
    return this.source[this.index];
  }

  private skipSpace() {
    while (this.index < this.source.length && /\s/.test(this.source[this.index])) this.index += 1;
  }

  private expect(char: string) {
    if (this.source[this.index] !== char) {
      throw new SyntaxError(`Expected ${char} at ${this.index}`);
    }
    this.index += 1;
  }

  private readValue(): unknown {
    if (this.index >= this.source.length) throw new SyntaxError('Unexpected end of input');

    const char = this.current;
    if (char === '[') return this.readSequence(']');
    if (char === '(') return this.readSequence(')');
    if (char === '{') return this.readMapping();
    if (char === '"' || char === "'") return this.readString();

    // String prefixes: b'…', r'…', f'…', u'…', rb'…'
    const prefix = this.source.slice(this.index, this.index + 3).match(/^[bBrRuUfF]{1,2}(?=['"])/);
    if (prefix) {
      this.index += prefix[0].length;
      return this.readString();
    }

    return this.readAtom();
  }

  private readSequence(close: string): unknown[] {
    this.index += 1; // opening bracket
    const items: unknown[] = [];
    this.skipSpace();
    while (this.current !== close) {
      items.push(this.readValue());
      this.skipSpace();
      if (this.current === ',') {
        this.index += 1;
        this.skipSpace();
        continue;
      }
      if (this.current !== close) throw new SyntaxError(`Expected , or ${close} at ${this.index}`);
    }
    this.expect(close);
    return items;
  }

  private readMapping(): Record<string, unknown> {
    this.index += 1; // '{'
    const result: Record<string, unknown> = {};
    this.skipSpace();
    while (this.current !== '}') {
      const key = this.readValue();
      this.skipSpace();
      this.expect(':');
      this.skipSpace();
      result[String(key)] = this.readValue();
      this.skipSpace();
      if (this.current === ',') {
        this.index += 1;
        this.skipSpace();
        continue;
      }
      if (this.current !== '}') throw new SyntaxError(`Expected , or } at ${this.index}`);
    }
    this.expect('}');
    return result;
  }

  private readString(): string {
    const quote = this.current;
    // Triple-quoted literals are rare in repr output but cheap to support.
    const triple = this.source.startsWith(quote.repeat(3), this.index);
    const delimiter = triple ? quote.repeat(3) : quote;
    this.index += delimiter.length;

    let out = '';
    while (this.index < this.source.length) {
      if (this.source.startsWith(delimiter, this.index)) {
        this.index += delimiter.length;
        return out;
      }

      const char = this.source[this.index];
      if (char !== '\\') {
        out += char;
        this.index += 1;
        continue;
      }

      this.index += 1;
      const code = this.source[this.index];
      this.index += 1;

      if (code === 'x' || code === 'u' || code === 'U') {
        const width = code === 'x' ? 2 : code === 'u' ? 4 : 8;
        const hex = this.source.slice(this.index, this.index + width);
        this.index += width;
        const point = Number.parseInt(hex, 16);
        // Lone surrogates survive the round-trip through str(); they render as
        // tofu, so drop them rather than emitting broken code units.
        if (Number.isNaN(point)) out += hex;
        else if (point >= 0xd800 && point <= 0xdfff) out += '';
        else out += String.fromCodePoint(point);
        continue;
      }

      out += ESCAPES[code] ?? code;
    }

    throw new SyntaxError('Unterminated string literal');
  }

  private readAtom(): unknown {
    const rest = this.source.slice(this.index);
    const word = rest.match(/^(True|False|None|nan|inf|-inf|Ellipsis)\b/);
    if (word) {
      this.index += word[0].length;
      switch (word[0]) {
        case 'True':
          return true;
        case 'False':
          return false;
        case 'nan':
          return Number.NaN;
        case 'inf':
          return Number.POSITIVE_INFINITY;
        case '-inf':
          return Number.NEGATIVE_INFINITY;
        default:
          return null;
      }
    }

    const number = rest.match(/^[+-]?(\d+\.?\d*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?)/);
    if (number) {
      this.index += number[0].length;
      return Number(number[0]);
    }

    throw new SyntaxError(`Unrecognised token at ${this.index}`);
  }
}

/** Parses a Python literal, returning `undefined` when the text is not one. */
export function parsePythonLiteral(source: string): unknown {
  const trimmed = source.trim();
  if (!/^[[({]/.test(trimmed)) return undefined;
  try {
    return new PythonLiteralParser(trimmed).parse();
  } catch {
    return undefined;
  }
}
