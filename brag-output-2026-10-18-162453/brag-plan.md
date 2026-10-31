# Brag Plan: Nyvexa

## What is this app?
Nyvexa is a full-stack AI assistant workspace — streaming chat over SSE, autonomous research with citations, a tool-using agent, side-by-side model comparison, and per-user knowledge bases — wrapped in a premium light-SaaS interface (ivory/navy, Inter, no neon) with free NVIDIA NIM / OpenRouter models.

## The angle
"Calm confidence." A serious, beautifully-made workspace that quietly does five things most AI chat wrappers don't: researches with sources, runs tools, compares models live, remembers your documents, and hardens itself with 41 security tests. The video shows the product *working* — typing, researching, citing, comparing — never a feature list.

## Hook (first 2-3 seconds)
The real composer, close and centered on the ivory canvas: the placeholder **"Ask Nyvexa anything…"** while a cursor types a real suggestion-chip line — **"Research FastAPI SSE streaming patterns"** — then Send. One click and the app goes to work. The hook is the product taking an instruction, not a logo.

## Key moments (the middle)
- The **AUTONOMOUS RESEARCH** panel building itself: "Planning research queries…" → "Searching the web (3 queries)…" → "Synthesizing findings…", steps landing one by one while the blue caret streams tokens.
- **Sources landing** — a SOURCES card fills with links and the answer's `[1] [2]` citation superscripts light up, one source flashing on the highlight.
- **Compare mode splitting the screen** — "Nyvexa · Compare" with two panes (blue `#1769C2` vs deep blue `#0f4c81`) streaming two different models at the same time.

## Outro / punchline
The blue N mark lands, then the product's own subtitle, verbatim: **"Streaming answers, deep research, tool-using agents, and your own knowledge base."** → "Nyvexa · AI Workspace". The music fades under it.

## User flow worth showing
Entry (type a real prompt in the composer → Send) → key action (live research panel + streaming tokens with caret) → result (numbered sources + citations, then two-model comparison). The centerpiece scenes are the working app, not landing-page sections.

## Tone
- Preset: `polished`
- Creative direction: quiet premium product film — ivory/navy design system with Inter, restraint over spectacle
- Interpretation: fewer scenes with longer holds, entrances that snap then settle, typography that matches the app's own UI, no neon energy, no hype language — the product's polish is the flex.

## Format: landscape — 1920x1080
## Duration: 21s

## Visual identity (from the project)
- Background: `#f7f6f2` (warm ivory `--nv-bg`), panels `#ffffff`
- Accent: `#1769c2` (primary blue), secondary pane accent `#0f4c81`
- Text: `#102747` (deep navy), dim `#66758c`
- Display font: Inter (600/700 for headings)
- Body font: Inter (400/500), JetBrains Mono for code/query chips
- Strongest visual element: the live chat workspace — composer, research panel with mono query chips, sources card, two-pane compare grid; soft navy-tinted double shadows, 8/10/12px radii, hairline `rgba(16,39,71,0.1)` borders.

## Share copy (draft)
Built Nyvexa: streaming chat, autonomous research with sources, a tool-using agent, and live two-model compare — free models, 41 security tests, one calm ivory-and-navy workspace.

## Audio direction
- Role: warm bed with sparse professional accents
- Music: `happy-beats-business-moves-vol-12-by-ende-dot-app.mp3` (steady, clean — the polished pick), volume ~0.32
- Music treatment: starts at 0 under the typing hook, gentle fade-in over first ~1s, holds steady, fades out across the last 1.5s under the logo
- Music cue guidance: preset at `assets/music/cues/happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.json` (tempo ≈ 109.96 BPM). Strong-cue locks: **8.74s** (research → sources scene cut), **13.11s** (sources → compare cut), **17.47s** (compare → outro logo). Beat grid for sequential steps ≈ every 2 beats (1.09s spacing) so research steps stay readable.
- Audio-reactive treatment: subtle; music RMS/bass breathes the ivory background warmth and the blue brand mark glow on stronger moments. No waveform/equalizer visuals.
- SFX posture: sparse — keyboard ticks while the prompt types, a soft send click, gentle per-step drops for the research panel, one soft bell on the sources landing, one soft reveal on the outro.
- Audio-coupled moments: typed prompt (randomized keypresses), send click, research steps arriving one by one, citation/source card arrival, compare pane headers splitting in, final logo.
- Restraint rule: nothing aggressive; no SFX over streaming text; SFX ≤ 0.7 volume.

## Storyboard

### Scene 1 — The ask — 4.0s
Ivory canvas. The composer card is center frame with placeholder "Ask Nyvexa anything…", mode pills (Auto / Chat / Research / Deep Research / Agent / Compare) and the blue Send button. A caret types "Research FastAPI SSE streaming patterns" (verbatim chip), then Send clicks and depresses. Product material: real composer + suggestion chip copy from `frontend/index.html`.
Sequential/interaction: yes — simulated typing character-by-character, then a simulated Send click.
Audio intent: attentive, crisp; the click starts the show.
Audio-coupled idea: randomized `keyboard/keypress-*.wav` per character (thin out to every few chars), `ui/click1.ogg` on Send.
Music: vol-12 bed fades in.
Transition mood: clean → Scene 2

### Scene 2 — Autonomous research — 4.7s (t=4.0–8.74)
Cut/pull back to the workspace: header with blue "N" + "Nyvexa / AI Workspace", welcome line faint, status hint flips to "Nyvexa is responding…". The AUTONOMOUS RESEARCH panel builds: three steps land one by one — "Planning research queries…", "Searching the web (3 queries)…" with mono query chips, "Synthesizing findings…" — while typed tokens stream behind a blinking `#1769c2` caret.
Sequential/interaction: yes — 3 step rows arrive one by one, each with a soft drop; hold each label ≥0.8s settled.
Audio intent: methodical, building; the product visibly working.
Audio-coupled idea: step arrivals every ~1.1s (on the 2-beat grid); a subtle tick per step, nothing over the streaming text.
Music: bed continues, energy rising toward the 8.74s cue.
Transition mood: hard cut on strong cue 8.74s → Scene 3

### Scene 3 — Sources & citations — 4.4s (t=8.74–13.11)
The answer settles with visible `[1] [2]` citation superscripts; a SOURCES (6) card slides in with link rows (title + domain); one source row flashes its highlight as its citation is "clicked"; the usage pill "↑ 412 ↓ 198 (1,042 tok)" fades in.
Sequential/interaction: yes — source rows arrive one by one, then the citation highlight flash.
Audio intent: satisfaction; evidence landing.
Audio-coupled idea: soft `casino/card-place-*` per source row (accent first + last only), `impactGlass_light_001` soft ping on the citation highlight.
Music: hits strong cue at scene entry (beat-locked 8.74s).
Transition mood: hard cut on strong cue 13.11s → Scene 4

### Scene 4 — Two models, one screen — 4.4s (t=13.11–17.47)
Header reads "Nyvexa · Compare". The screen splits into two white panes: pane A header blue `#1769C2` labeled "Nemotron 3 Super 120B (NVIDIA)", pane B header `#0f4c81` labeled "Gemma 4 31B (free)". Tokens stream into both panes simultaneously at different speeds; both carets blink. A "vs" divider settles.
Sequential/interaction: yes — pane A header, then pane B header (2-beat spacing), then concurrent streaming.
Audio intent: parallel energy; the two-lane rhythm is the point.
Audio-coupled idea: pane A/B header arrivals with two soft drops ~0.5s apart; streaming silent.
Music: bed at its fullest; scene entry beat-locked to 13.11s.
Transition mood: soft crossfade → Scene 5

### Scene 5 — Outro — 3.5s (t=17.47–21.0)
Ivory field. The blue rounded-square "N" mark lands (soft scale-in), then the verbatim line reveals: "Streaming answers, deep research, tool-using agents, and your own knowledge base." Below it: "Nyvexa · AI Workspace". Nothing else.
Sequential/interaction: yes — mark, then tagline, then wordmark, held to read.
Audio intent: resolve; one soft bell on the mark, music fades under.
Audio-coupled idea: `interface/bong_001` (soft, low) at the mark landing, beat-locked to 17.47s; tagline reveal with no SFX.
Music: fade-out across the last 1.5s.
Transition mood: end.

**Music mood for this video:** steady, clean, confident — vol-12 bed at low volume.
**Audio summary:** quiet bed under a crisp typing hook, methodical accents through the research build, satisfying landing cues for sources, fullest energy at the compare split, soft bell and fade under the outro.
