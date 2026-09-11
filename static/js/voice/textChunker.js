/**
 * Real, standalone sentence-boundary chunker for streaming TTS.
 * Takes incremental text deltas (as they arrive from the chat stream)
 * and emits complete, speakable chunks as soon as a real sentence
 * boundary is found -- not before, and not by naively splitting on
 * every period (which would wrongly split "Mr. Smith" or "3.14").
 */

const ABBREVIATIONS = new Set([
  'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'vs', 'etc',
  'inc', 'ltd', 'co', 'corp', 'jan', 'feb', 'mar', 'apr', 'jun',
  'jul', 'aug', 'sep', 'sept', 'oct', 'nov', 'dec', 'e.g', 'i.e',
  'approx', 'no', 'vol', 'fig',
]);

const MAX_CHUNK_CHARS = 280;
const MIN_CHUNK_CHARS = 8;

export class TextChunker {
  constructor() {
    this._buffer = '';
  }

  push(deltaText) {
    if (!deltaText) return [];
    this._buffer += deltaText;
    const chunks = [];

    let cursor = 0;
    while (true) {
      const boundary = this._findNextBoundary(this._buffer, cursor);
      if (boundary === -1) break;
      const chunk = this._buffer.slice(cursor, boundary).trim();
      if (chunk.length >= MIN_CHUNK_CHARS) {
        chunks.push(chunk);
      }
      cursor = boundary;
    }

    this._buffer = this._buffer.slice(cursor);

    while (this._buffer.length > MAX_CHUNK_CHARS) {
      let splitAt = this._buffer.lastIndexOf(' ', MAX_CHUNK_CHARS);
      if (splitAt <= 0) splitAt = MAX_CHUNK_CHARS;
      const chunk = this._buffer.slice(0, splitAt).trim();
      if (chunk.length >= MIN_CHUNK_CHARS) chunks.push(chunk);
      this._buffer = this._buffer.slice(splitAt);
    }

    return chunks;
  }

  flush() {
    const remaining = this._buffer.trim();
    this._buffer = '';
    return remaining ? [remaining] : [];
  }

  _findNextBoundary(text, fromIndex) {
    const re = /[.!?]+(["')\]]*)(\s|$)/g;
    re.lastIndex = fromIndex;
    let match;
    while ((match = re.exec(text)) !== null) {
      const punctEnd = match.index + match[0].length - (match[2] ? match[2].length : 0);
      if (match[2] === '' && punctEnd >= text.length) {
        return -1;
      }
      if (this._isRealSentenceEnd(text, match.index)) {
        return punctEnd;
      }
    }
    return -1;
  }

  _isRealSentenceEnd(text, periodIndex) {
    if (text[periodIndex] === '.') {
      const before = text[periodIndex - 1];
      const after = text[periodIndex + 1];
      if (before >= '0' && before <= '9' && after >= '0' && after <= '9') {
        return false;
      }
    }

    const preceding = text.slice(0, periodIndex);
    const wordMatch = preceding.match(/([A-Za-z][A-Za-z.]*)$/);
    if (wordMatch) {
      const word = wordMatch[1].toLowerCase().replace(/\.$/, '');
      if (ABBREVIATIONS.has(word)) return false;
      if (wordMatch[1].length === 1 && text[periodIndex - 1] === text[periodIndex - 1].toUpperCase()) {
        return false;
      }
    }

    return true;
  }
}
