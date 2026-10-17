/**
 * voice.js — Web Speech API: speech-to-text input + text-to-speech readout.
 */

export class VoiceInput {
  constructor({ onResult, onStateChange }) {
    this.onResult = onResult || (() => {});
    this.onStateChange = onStateChange || (() => {});
    this.recognition = null;
    this.listening = false;
    this._init();
  }

  _init() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;
    const rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 1;

    rec.onresult = (event) => {
      let transcript = "";
      for (const res of event.results) transcript += res[0].transcript;
      this.onResult(transcript, event.results[event.results.length - 1].isFinal);
    };
    rec.onend = () => {
      this.listening = false;
      this.onStateChange(false);
    };
    rec.onerror = () => {
      this.listening = false;
      this.onStateChange(false);
    };
    this.recognition = rec;
  }

  get supported() {
    return this.recognition !== null;
  }

  toggle() {
    if (!this.supported) return false;
    if (this.listening) {
      this.recognition.stop();
      this.listening = false;
      this.onStateChange(false);
      return false;
    }
    try {
      this.recognition.start();
      this.listening = true;
      this.onStateChange(true);
      return true;
    } catch {
      return false;
    }
  }
}

export class SpeechReader {
  constructor() {
    this.supported = "speechSynthesis" in window;
    this.speaking = false;
  }

  speak(text) {
    if (!this.supported || !text) return;
    this.stop();
    // Strip markdown lightly
    const clean = text
      .replace(/```[\s\S]*?```/g, " code block omitted. ")
      .replace(/[#*_`>]/g, "")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .slice(0, 4000);
    const utter = new SpeechSynthesisUtterance(clean);
    utter.rate = 1.02;
    utter.pitch = 1.0;
    utter.onend = () => (this.speaking = false);
    utter.onerror = () => (this.speaking = false);
    this.speaking = true;
    window.speechSynthesis.speak(utter);
  }

  stop() {
    if (this.supported) window.speechSynthesis.cancel();
    this.speaking = false;
  }

  toggle(text) {
    if (this.speaking) {
      this.stop();
      return false;
    }
    this.speak(text);
    return this.speaking;
  }
}
