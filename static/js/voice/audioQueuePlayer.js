/**
 * Real, standalone sequential audio-chunk player for streaming TTS.
 * Chunks of text are enqueued as they arrive from the TextChunker;
 * this class kicks off real synthesis for each chunk immediately, but
 * always PLAYS chunks strictly in the order they were enqueued, even
 * if a later chunk's synthesis finishes first.
 */

export class AudioQueuePlayer {
  constructor({ synthesizeUrl = '/api/tts/synthesize', onError = null } = {}) {
    this._synthesizeUrl = synthesizeUrl;
    this._onError = onError;
    this._queue = [];
    this._audioEl = new Audio();
    this._playing = false;
    this._stopped = false;

    this._audioEl.addEventListener('ended', () => this._advance());
    this._audioEl.addEventListener('error', () => {
      if (this._onError) this._onError(new Error('Audio playback error'));
      this._advance();
    });
  }

  enqueue(text) {
    if (this._stopped || !text) return;
    const entry = {
      text,
      objectUrl: null,
      audioPromise: this._synthesize(text),
    };
    this._queue.push(entry);
    if (!this._playing) this._advance();
  }

  stop() {
    this._stopped = true;
    this._audioEl.pause();
    this._audioEl.removeAttribute('src');
    for (const entry of this._queue) {
      if (entry.objectUrl) URL.revokeObjectURL(entry.objectUrl);
    }
    this._queue = [];
    this._playing = false;
  }

  async _synthesize(text) {
    try {
      const resp = await fetch(this._synthesizeUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, format: 'audio' }),
      });
      if (!resp.ok) throw new Error(`TTS synthesis failed: HTTP ${resp.status}`);
      const blob = await resp.blob();
      return URL.createObjectURL(blob);
    } catch (err) {
      if (this._onError) this._onError(err);
      return null;
    }
  }

  async _advance() {
    if (this._stopped) return;
    if (this._queue.length === 0) {
      this._playing = false;
      return;
    }
    this._playing = true;
    const entry = this._queue.shift();
    const objectUrl = await entry.audioPromise;
    if (this._stopped) return;
    if (!objectUrl) {
      this._advance();
      return;
    }
    entry.objectUrl = objectUrl;
    this._audioEl.src = objectUrl;
    try {
      await this._audioEl.play();
    } catch (err) {
      if (this._onError) this._onError(err);
      this._advance();
    }
  }
}
