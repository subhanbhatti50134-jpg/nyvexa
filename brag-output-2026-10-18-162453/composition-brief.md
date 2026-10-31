# Hyperframes Composition Brief: Nyvexa

## Objective
Create a short launch-style brag video for Nyvexa — a calm, premium product film showing the workspace actually working: type → research → sources → compare → brand outro.

## Output
- Composition directory: `brag-output-2026-10-18-162453/composition/`
- Rendered video: `brag-output-2026-10-18-162453/brag.mp4`
- Format: landscape — 1920x1080
- Duration: 21s

## Source Material
- Project root: `/home/muhammadsubhan/Nyvexa AI`
- Primary files read: `frontend/index.html`, `frontend/css/theme.css`, `frontend/js/*`, `README.md`, `backend/` routers+services (via project inspection)
- Product name: Nyvexa
- Tagline / strongest claim: "Streaming answers, deep research, tool-using agents, and your own knowledge base."
- Key UI or visual moment to recreate: the chat workspace — composer with mode pills, AUTONOMOUS RESEARCH step panel, SOURCES card with citation superscripts, two-pane Compare grid, blue "N" brand mark
- Copy that must appear verbatim:
  - "Ask Nyvexa anything…"
  - "Research FastAPI SSE streaming patterns" (suggestion chip)
  - "Nyvexa is responding…"
  - "Planning research queries…"
  - "Searching the web (3 queries)…"
  - "Synthesizing findings…"
  - "SOURCES" / source rows with title + domain
  - "Nyvexa · Compare"
  - "Nemotron 3 Super 120B (NVIDIA)" / "Gemma 4 31B (free)"
  - "Streaming answers, deep research, tool-using agents, and your own knowledge base."
  - "Nyvexa · AI Workspace"
  - Mode pills: Auto / Chat / Research / Deep Research / Agent / Compare

## Creative Direction
- Tone preset: polished
- Creative direction: quiet premium product film — ivory/navy design system with Inter, restraint over spectacle
- Interpretation: fewer scenes, longer holds, snappy-but-settled entrances, the app's own typography and shadows; no hype language, no neon energy — the product's polish is the flex.
- Angle: "Calm confidence." The video shows the product working — typing, researching, citing, comparing — never a feature list. Specific claims only (free models, real modes, real copy).
- Hook: composer types "Research FastAPI SSE streaming patterns" → Send click.
- Outro / punchline: brand mark lands with the verbatim welcome subtitle → "Nyvexa · AI Workspace".
- Avoid:
  - Generic SaaS language ("streamline your workflow" and kin)
  - Abstract filler visuals / color washes / particle systems
  - Unrelated visual redesign (palette and chrome must come from `theme.css`)

## Visual Identity
- Background: `#f7f6f2` (ivory), panels `#ffffff`, sidebar tint `#e9eef5`
- Text: `#102747` primary, `#66758c` dim
- Accent: `#1769c2` primary blue, `#0f4c81` compare-pane B, borders `rgba(16,39,71,0.1)`
- Display font: Inter (600/700)
- Body font: Inter (400/500); JetBrains Mono for query chips/usage numbers
- Visual references from the project: composer card with soft double shadow `0 1px 2px rgba(16,39,71,.06), 0 8px 24px rgba(16,39,71,.1)`; radii 8/10/12px; blue rounded-square "N" brand mark (radius ~22%); typing dots + blinking blue caret; mono query chips; two-pane grid `1fr 1fr`.

## Storyboard
Use the storyboard in `brag-output-2026-10-18-162453/brag-plan.md` as the creative contract.

Scene summary:
1. The ask — 4.0s — composer types the prompt, Send clicks
2. Autonomous research — 4.7s — 3 research steps land one by one, tokens stream with caret
3. Sources & citations — 4.4s — citations + SOURCES rows arrive, one highlights, usage pill
4. Two models, one screen — 4.4s — compare split, two pane headers, concurrent streaming
5. Outro — 3.5s — N mark + verbatim tagline + wordmark

## Audio
- Audio role: warm bed with sparse professional accents
- Audio arc: quiet under the typing hook → methodical accents in research → satisfying landing cues for sources → fullest at the compare split → soft bell + fade under the outro
- Music: `assets/music/happy-beats-business-moves-vol-12-by-ende-dot-app.mp3` (copied into composition)
- Music treatment: fade-in over ~1s from t=0, bed volume ~0.32, fade-out across the final 1.5s under the logo
- Music cue guidance: bundled preset `assets/music/cues/happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.json` (tempo ≈ 109.96 BPM) — strong-cue locks at **8.74s** (scene 3 entry), **13.11s** (scene 4 entry), **17.47s** (outro mark), each within ±0.15s, marked `// beat-locked`. Sequential research steps on the 2-beat grid (~1.09s apart) marked `// beat-grid`. Ignore any cue that hurts readability.
- Audio-reactive treatment: subtle — use extracted audio RMS/bass to breathe the ivory background warmth and modulate the brand-mark glow; no waveform/equalizer visuals, no strobing, no text scaling.
- Audio-coupled moments:
  - Scene 1 typing — randomized keyboard ticks per character (thinned), Send click at the click
  - Scene 2 research steps — soft drop per step arrival, beat-grid aligned
  - Scene 3 sources — soft card-place on row arrivals (accent first + last), glass ping on citation highlight
  - Scene 4 compare — two soft drops for pane headers ~0.5s apart
  - Scene 5 outro — one soft bell at mark landing (beat-locked 17.47s)
- SFX selection guidance: polished restraint — low/medium HF-risk files only, SFX volume ≤ 0.7, align SFX to animation start, never over streaming text. Use `<skill-dir>/assets/sfx/sfx-analysis.md` as guidance if available at the install path `/home/muhammadsubhan/.agents/skills/brag/assets/sfx/sfx-analysis.md`.
- Exact SFX choice: Hyperframes should choose filenames, timestamps, density, and volume based on the implemented animation.
- Audio files: music copied to `composition/assets/music/`; copy chosen SFX into `composition/assets/sfx/...`

## Hyperframes Instructions
Load the composition-building HyperFrames domain skills — `hyperframes-core` (composition contract + `data-*` timing), `hyperframes-animation` (motion), `hyperframes-creative` (design spec, beats, audio-reactive), `hyperframes-keyframes` (seek-safe keyframes), and `hyperframes-cli` (lint/check/render). /brag is its own workflow: do not enter the `hyperframes` entry-point intent interview and do not route into its generic promo / launch-video workflow. Prefer native Hyperframes conventions over anything in `/brag`.

Requirements:
- Show at least one real UI, copy, or visual element from the source project — the centerpiece scenes must be the working chat workspace, not abstract graphics.
- Keep all text readable in the final render (reading-time floor: ~0.8s settled for labels, ~0.3s/word for sentences).
- Keep the video within 15-25 seconds (planned: 21s).
- Include the planned music/SFX layer as specified above.
- Treat `/brag` audio notes as guidance, not a fixed cue sheet. Choose SFX after the visual animation exists.
- Treat music cue metadata as optional timing hints. Ignore cues that hurt readability, scene pacing, or the product story.
- Major reveals may move toward nearby strong cues within about 0.15s. Smaller entrances may align to nearby beat points within about 0.10s. Only the 3 listed strong-cue locks are required.
- Honor the planned music treatment (fade-in, fade-out under logo, subtle audio-reactive) using the best Hyperframes-supported implementation.
- When music is present, extract audio data and wire at least one visual element to it per the hyperframes-creative audio-reactive workflow (helper ships with that skill — don't hardcode a path). If extraction is unavailable, document it and continue.
- Use local assets for audio; paths relative to `composition/`.
- Run `hyperframes check` before render — it is brag's single gate.
- Keep creation and rendering local. Remote or publishing workflows require a separate explicit user request.
