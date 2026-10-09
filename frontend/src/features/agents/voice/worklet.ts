/**
 * The AudioWorklet that turns the microphone into what the server's socket takes:
 * mono PCM16 at 16 kHz, in chunks of about 160 ms. Written as a string and loaded
 * from a Blob so the single-file build needs no extra asset.
 */
export const WORKLET_SOURCE = `
class Pcm16 extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.out = new Int16Array(2560);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    while (this.pos < ch.length) {
      const i = Math.floor(this.pos);
      const j = Math.min(i + 1, ch.length - 1);
      const f = this.pos - i;
      const v = Math.max(-1, Math.min(1, ch[i] * (1 - f) + ch[j] * f));
      this.out[this.n++] = v < 0 ? v * 32768 : v * 32767;
      if (this.n === this.out.length) {
        const buf = this.out.slice(0).buffer;
        this.port.postMessage(buf, [buf]);
        this.n = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= ch.length;
    return true;
  }
}
registerProcessor("pcm16", Pcm16);
`;
